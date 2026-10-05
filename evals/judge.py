"""
LLM Judge
=========

Overview
--------
Scores one finished case transcript against the LLM judge's rubric: grounding, language quality, and
clarification quality (when the case asked a clarifying question). The rubric is the same one, word
for word, that the two human raters receive when they double-score the same held-out sample, so the
judge-versus-human agreement check compares like against like; the rubric is versioned with the
prompt.

Scope
-----
In: ``JudgeVerdict``, the forced tool schema, and ``LlmJudge.score`` — one structured-output call
per case, at temperature 0, over a transcript's own turns and a ``facts_and_sources`` string the
caller assembles.
Out: assembling ``facts_and_sources`` itself (the caller's job — see this module's Limitations);
comparing a verdict against a human rater's score or computing agreement
(``evals.judge_validation``); running a case in the first place (``evals.runner``); turning many
verdicts into the report's judge-validation section (the report generator).

Design Principles
-----------------
- **Reuses the production LLM port, not a second adapter.** Unlike the B1 baseline's
  ``naive_agent_client`` (which needs open tool choice, the opposite of what ``LlmClient`` forces),
  the judge is the shape ``app.llm.client.LlmClient`` is built for: one forced tool call
  over a fixed input, returning one structured result. Building a second adapter here would
  duplicate the port's error taxonomy and its ``FakeLlm`` test double for no boundary reason.
- **Same calling convention as ``app.conversation.llm_understanding.LlmNlu``.** A versioned prompt
  loaded once at construction, a ``ToolSpec`` describing the forced call, ``temperature=0.0`` for
  structured extraction, no retry inside this module.
- **Clarification is scored only when it applies.** The rubric names clarification quality as
  applicable only to a case that asked a clarifying question; the tool schema does not require it,
  and the prompt instructs the model to omit it rather than guess a value — matching
  the judge rubric's own ``NA`` convention for the human raters' identical column.
- **A judge call that cannot complete is not swallowed.** Unlike ``LlmNlu`` (which falls back to
  unusable understanding so a customer is never shown a provider error), a judge call has no
  customer waiting on it: any ``LlmError`` propagates to the caller, matching the harness's
  "no hidden retry" rule for a case runtime error (see ``evals.cli``).

Runtime Contract
-----------------
``JudgeVerdict(case_id, grounding, language_quality, clarification, rationale, judge_model,
prompt_version)``. ``LlmJudge(llm, *, model, prompt=None)``. ``LlmJudge.score(case_id, *, language,
user_turns, system_replies, facts_and_sources) -> JudgeVerdict`` raises ``LlmError``.

Limitations
-----------
``facts_and_sources`` is a plain string this module trusts the caller to have assembled; this
module has no opinion on whether it came from a store query, a golden-set case's own authored facts,
or a human-filled case-sheet column, and does not itself reopen the customer-facing API's envelope
boundary to find out. A call that raises aborts scoring that one case; there is no retry or
partial-credit path here, the same choice the rest of this harness already makes for a case it
cannot run to completion.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Sequence  # Ordered conversation turns
from dataclasses import dataclass  # Immutable verdict record

# Third-party libraries
from pydantic import BaseModel, ConfigDict, Field, ValidationError  # Validated tool-call arguments

# Local modules
from app.llm.client import (  # The port and its request/error shapes
    CompletionRequest,
    LlmClient,
    LlmOutputInvalid,
    ToolSpec,
)
from app.llm.prompts import PromptTemplate, load_prompt  # Versioned prompt loading and filling

_PROMPT_NAME = "judge_v1"

# The rubric's closed 0-2 scale, shared by all three dimensions.
_MIN_SCORE = 0
_MAX_SCORE = 2
_SCORE_SCHEMA = {"type": "integer", "minimum": _MIN_SCORE, "maximum": _MAX_SCORE}

_JUDGE_TOOL = ToolSpec(
    name="record_judgment",
    description=(
        "Record the rubric score for one finished case transcript: nothing more, nothing invented."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "grounding": _SCORE_SCHEMA,
            "language_quality": _SCORE_SCHEMA,
            "clarification": _SCORE_SCHEMA,
            "rationale": {"type": "string", "maxLength": 500},
        },
        "required": ["grounding", "language_quality", "rationale"],
    },
)


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """One case's rubric score from the automated judge.

    ``clarification`` is ``None`` for a case the rubric does not ask that question about, matching
    the rubric's ``NA`` convention for the same column. ``judge_model`` and ``prompt_version`` are
    the model and rubric prompt that produced the score.
    """

    case_id: str
    grounding: int
    language_quality: int
    clarification: int | None
    rationale: str
    judge_model: str
    prompt_version: str

    def __post_init__(self) -> None:
        """Reject a score outside the rubric's 0-2 scale.

        Raises
        ------
        ValueError
            ``grounding``, ``language_quality`` or a non-``None`` ``clarification`` is not 0, 1
            or 2.
        """
        scores = (
            ("grounding", self.grounding),
            ("language_quality", self.language_quality),
        )
        for name, value in scores:
            if not _MIN_SCORE <= value <= _MAX_SCORE:
                raise ValueError(f"{name} must be 0, 1 or 2, got {value}")
        if self.clarification is not None and not _MIN_SCORE <= self.clarification <= _MAX_SCORE:
            raise ValueError(f"clarification must be 0, 1, 2 or None, got {self.clarification}")


def _joined(turns: Sequence[str]) -> str:
    """The turns as one newline-separated block for the prompt."""
    return "\n".join(turns)


class _JudgeExtraction(BaseModel):
    """The tool call's arguments, validated before ``JudgeVerdict`` applies its own bounds."""

    model_config = ConfigDict(extra="ignore")

    grounding: int = Field(ge=_MIN_SCORE, le=_MAX_SCORE)
    language_quality: int = Field(ge=_MIN_SCORE, le=_MAX_SCORE)
    clarification: int | None = Field(default=None, ge=_MIN_SCORE, le=_MAX_SCORE)
    rationale: str


class LlmJudge:
    """Scores a finished case transcript against the committed rubric, through the LLM port."""

    def __init__(self, llm: LlmClient, *, model: str, prompt: PromptTemplate | None = None) -> None:
        """
        Parameters
        ----------
        llm : LlmClient
            The port to call; a real adapter or ``FakeLlm`` in tests.
        model : str
            A model id from the configuration allow-list (the caller's job to check;
            ``app.config.Settings.judge_model`` is already validated against it).
        prompt : PromptTemplate | None
            The rubric prompt to use; loaded from ``prompts/judge_v1.yaml`` when omitted.
        """
        self._llm = llm
        self._model = model
        self._prompt = prompt or load_prompt(_PROMPT_NAME)

    def score(
        self,
        case_id: str,
        *,
        language: str,
        user_turns: Sequence[str],
        system_replies: Sequence[str],
        facts_and_sources: str,
    ) -> JudgeVerdict:
        """Score one finished transcript.

        Raises
        ------
        LlmUnavailable, LlmRequestRejected
            The underlying ``LlmClient`` call did not complete; not caught here (see this
            module's Limitations).
        LlmOutputInvalid
            The model did not call the forced tool, or its arguments do not match the rubric's
            own shape (a score outside 0-2, a missing required field).
        """
        user_text = self._prompt.render_task(
            language=language,
            user_turns=_joined(user_turns),
            system_replies=_joined(system_replies),
            facts_and_sources=facts_and_sources,
        )
        request = CompletionRequest(
            model=self._model,
            system=self._prompt.system,
            user_text=user_text,
            tool=_JUDGE_TOOL,
            prompt_version=self._prompt.version,
            temperature=0.0,
        )
        result = self._llm.complete(request)
        try:
            extraction = _JudgeExtraction.model_validate(result.tool_input)
            return JudgeVerdict(
                case_id=case_id,
                grounding=extraction.grounding,
                language_quality=extraction.language_quality,
                clarification=extraction.clarification,
                rationale=extraction.rationale,
                judge_model=result.model,
                prompt_version=result.prompt_version,
            )
        except (ValidationError, ValueError) as error:
            raise LlmOutputInvalid(
                f"judge tool call did not match the rubric shape: {error}"
            ) from error
