"""
LLM Judge Tests
================

Component: ``evals.judge``. Hermetic and pure: ``FakeLlm`` makes no network call, so both the
success and the malformed-output paths run the same way in CI as they would against the real
provider.
"""

from __future__ import annotations

import pytest

from app.llm.client import FakeLlm, LlmOutputInvalid, LlmUnavailable
from evals.judge import JudgeVerdict, LlmJudge

_MODEL = "claude-sonnet-5"


def test_a_well_formed_tool_call_maps_to_a_judge_verdict() -> None:
    llm = FakeLlm(
        responses=[
            {
                "grounding": 2,
                "language_quality": 1,
                "clarification": 2,
                "rationale": "One awkward phrase, otherwise on point.",
            }
        ]
    )
    judge = LlmJudge(llm, model=_MODEL)

    verdict = judge.score(
        "norm-policy-es-01",
        language="es",
        user_turns=("¿Cuántos días tengo para reportar un cargo que no reconozco?",),
        system_replies=("Tienes 120 días desde la fecha de la transacción.",),
        facts_and_sources="Section filing-windows: 120 days for an unrecognized charge.",
    )

    assert verdict == JudgeVerdict(
        case_id="norm-policy-es-01",
        grounding=2,
        language_quality=1,
        clarification=2,
        rationale="One awkward phrase, otherwise on point.",
        judge_model=_MODEL,
        prompt_version="1",
    )


def test_clarification_is_none_when_the_model_omits_it() -> None:
    """A case with no clarifying question: the rubric's NA, not a guessed score."""
    llm = FakeLlm(
        responses=[
            {"grounding": 2, "language_quality": 2, "rationale": "Grounded and clear."},
        ]
    )
    judge = LlmJudge(llm, model=_MODEL)

    verdict = judge.score(
        "norm-policy-es-02",
        language="es",
        user_turns=("¿En cuánto tiempo me responden?",),
        system_replies=("En 3 días hábiles.",),
        facts_and_sources="Section response-time: 3 business days.",
    )

    assert verdict.clarification is None


def test_a_score_outside_the_rubrics_scale_is_a_judge_output_error() -> None:
    """The forced tool's own schema bounds this, but a provider can still misbehave."""
    llm = FakeLlm(responses=[{"grounding": 5, "language_quality": 2, "rationale": "x"}])
    judge = LlmJudge(llm, model=_MODEL)

    with pytest.raises(LlmOutputInvalid):
        judge.score(
            "norm-policy-es-01",
            language="es",
            user_turns=("¿Cuántos días tengo?",),
            system_replies=("120 días.",),
            facts_and_sources="Section filing-windows: 120 days.",
        )


def test_a_missing_required_field_is_a_judge_output_error() -> None:
    llm = FakeLlm(responses=[{"grounding": 2, "language_quality": 2}])
    judge = LlmJudge(llm, model=_MODEL)

    with pytest.raises(LlmOutputInvalid):
        judge.score(
            "norm-policy-es-01",
            language="es",
            user_turns=("¿Cuántos días tengo?",),
            system_replies=("120 días.",),
            facts_and_sources="Section filing-windows: 120 days.",
        )


def test_a_provider_failure_is_not_swallowed() -> None:
    """Unlike LlmNlu, the judge has no customer waiting on it: the caller sees the failure."""
    llm = FakeLlm(responses=[LlmUnavailable("timeout")])
    judge = LlmJudge(llm, model=_MODEL)

    with pytest.raises(LlmUnavailable):
        judge.score(
            "norm-policy-es-01",
            language="es",
            user_turns=("¿Cuántos días tengo?",),
            system_replies=("120 días.",),
            facts_and_sources="Section filing-windows: 120 days.",
        )


def test_the_request_carries_the_versioned_rubric_prompt_and_temperature_zero() -> None:
    llm = FakeLlm(responses=[{"grounding": 2, "language_quality": 2, "rationale": "x"}])
    judge = LlmJudge(llm, model=_MODEL)

    judge.score(
        "norm-policy-es-01",
        language="es",
        user_turns=("¿Cuántos días tengo?",),
        system_replies=("120 días.",),
        facts_and_sources="Section filing-windows: 120 days.",
    )

    sent = llm.requests[0]
    assert sent.model == _MODEL
    assert sent.temperature == 0.0
    assert sent.prompt_version == "1"
    assert sent.tool.name == "record_judgment"
    assert "¿Cuántos días tengo?" in sent.user_text
    assert "120 días." in sent.user_text
    assert "Section filing-windows: 120 days." in sent.user_text


@pytest.mark.parametrize(
    ("grounding", "language_quality", "clarification"),
    [(-1, 2, None), (3, 2, None), (2, -1, None), (2, 2, 3)],
)
def test_judge_verdict_rejects_a_score_outside_zero_to_two(
    grounding: int, language_quality: int, clarification: int | None
) -> None:
    with pytest.raises(ValueError, match="must be 0, 1"):
        JudgeVerdict(
            case_id="x",
            grounding=grounding,
            language_quality=language_quality,
            clarification=clarification,
            rationale="x",
            judge_model=_MODEL,
            prompt_version="1",
        )
