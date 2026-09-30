"""
Golden Set Case Schema
======================

Overview
--------
The shape of one scripted scenario in the held-out golden set: the language it runs in, the
seed state it starts from, the customer's scripted turns, and what a correct run must produce.
`evals.metrics` scores a harness run's verdicts against these expectations; this module defines
only what a case *is*, never how one is run or scored.

Scope
-----
In: ``Case``, its category and provenance labels, the safe-behavior vocabulary adversarial cases
are scored against, and ``InjectedToolFailure``, the tool-failure condition a case declares for
``evals.injector`` to apply.
Out: running a case against a system variant (the runner, a later slice), actually failing a tool
call (`evals.injector`), the deterministic and judge checks that turn a run into a verdict
(`evals.metrics.CaseResult`), and the corpus of 135 authored cases itself (a following slice,
delivered as generated data, not schema).

Design Principles
------------------
- **A case is a plain record, per the package's own rule**: no I/O, no clock, nothing derived at
  import time. Authoring 135 of these is a data-entry problem, not a code problem.
- **The mix-table categories are a closed set.** `CaseCategory` has exactly the six rows of the
  golden set's category table (`plan/docs/evaluation-plan.md`); a seventh stratum is a plan
  change before it is a code change.
- **Safe behavior is adversarial-only.** The evaluation plan scores an adversarial case on its
  safe expected behavior, never on task completion; a non-adversarial case is scored on its
  expected intent and reason code instead. A case cannot declare both or neither incorrectly:
  exactly the adversarial cases carry a safe behavior, and no other case does.
- **Provenance is stated, never inferred.** Every case says whether it reflects an observed row,
  team-generated wording, or an injected condition, so the report can name the limitation instead
  of a reader having to guess it from the case ID.

Runtime Contract
-----------------
``Case``, ``InjectedToolFailure``, the enumerations ``CaseCategory`` and ``SafeBehavior``, and the
``Provenance`` literal.

Limitations
-----------
``Case`` does not validate that ``seed_ref`` resolves to a real row of ``data/gold/ops_seed`` or
``data/gold/eval_bank``; that binding is checked when the corpus is authored and again by the
runner before a case executes. Provenance is stated once per case, not per utterance or per
injected condition inside it; a case whose turns mix provenances states the more specific one in
its free-text description.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable case records
from enum import StrEnum  # Closed sets: category, safe behavior
from typing import Literal  # The provenance label

# Local modules
from app.domain.policy.models import (  # Expected policy-engine reason and dispute category
    DisputeCategory,
    ReasonCode,
)
from contracts.service_v1.envelope import Intent, Lang  # Expected reply intent; case language
from contracts.service_v1.tools import Tool  # Which tool an injected failure targets

# -----------------------------------------------------------------------------
# Vocabulary
# -----------------------------------------------------------------------------


class CaseCategory(StrEnum):
    """Which row of the golden-set mix table a case belongs to.

    Exactly the six categories of the mix table in ``plan/docs/evaluation-plan.md``; adversarial
    cases are excluded from the correctness-rate sets (S, A, E) by `evals.metrics` but counted in
    unsafe outcomes, same as the category's own definition there.
    """

    NORMAL = "normal"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    HUMAN_REQUIRED = "human_required"
    MULTILINGUAL = "multilingual"
    ADVERSARIAL = "adversarial"


class SafeBehavior(StrEnum):
    """What an adversarial case must do instead of completing the requested task."""

    REFUSE = "refuse"
    RE_AUTH = "re_auth"
    CLARIFY = "clarify"
    HANDOFF = "handoff"


#: Whether a case reflects a real seeded row, team-authored wording, or an injected condition.
Provenance = Literal["observed", "team_generated", "injected"]


@dataclass(frozen=True, slots=True)
class InjectedToolFailure:
    """Which tool the runner's failure injector must fail for a case, and how.

    Every call the case's run makes to `tool` fails with `cause`; every other tool call passes
    through unchanged. A case with only one turn (every tool-failure case today) needs nothing
    more specific than this; a future case needing a failure on only one of several calls to the
    same tool is a reason to add that, not a reason to build it now.
    """

    tool: Tool
    cause: Literal["timeout", "error", "circuit_open"] = "timeout"
    retryable: bool = True


# -----------------------------------------------------------------------------
# Case
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Case:
    """One scripted scenario of the golden set.

    Parameters
    ----------
    case_id
        Stable identifier, unique across the golden set; never reused even if a case is retired.
    category
        The mix-table row this case counts toward.
    lang
        The conversation's language.
    provenance
        Whether the case reflects an observed row, team-authored wording, or an injected
        condition (see the module's Limitations).
    seed_ref
        The identifier of the row (or rows) the case's initial state is built from: a
        `data/gold/ops_seed` identifier for a case grounded in the demo bank's seeded state, or a
        `data/gold/eval_bank` identifier for a frozen scenario built for this harness (in
        particular, every injected adversarial condition — an orphan transaction, a null field,
        a poisoned merchant name — lives in `eval_bank`, since `ops_seed` is not built to hold
        one). A `CONFIRM_FILING` case's `seed_ref` names exactly one `ops_seed:TRX-...`
        transaction — the one `expected_category` names a correct run as confirming filing for
        (see `_exactly_confirm_filing_cases_declare_a_target`).
    user_turns
        The customer's scripted lines, in order, in `lang`.
    expected_intent
        The reply intent a correct run produces.
    expected_reason_code
        The policy-engine reason code a correct run produces, when `expected_intent` names a
        policy decision; `None` for a case whose correct reply carries no policy decision (a
        status inquiry, a policy answer, an abstention).
    expected_safe_behavior
        Required exactly when `category` is `ADVERSARIAL`, and forbidden otherwise (see
        `_exactly_adversarial_cases_declare_a_safe_behavior`).
    expected_policy_section_id
        The corpus section (`app.retrieval.corpus_index.PolicyChunk.section_id`) a correct policy
        answer is grounded in; required exactly when `expected_intent` is `Intent.POLICY_ANSWER`,
        forbidden otherwise (see `_exactly_policy_answers_declare_a_section`). Lets the evaluation
        judge and the human validation sample score grounding for a policy answer without
        reopening the running conversation's own retrieval trace (ADR-2's envelope boundary) — the
        golden-set author already knows which section a question is grounded in when writing it;
        this field promotes that knowledge from `description` prose to a checked value. Not
        validated against the real corpus files here (`Case` stays I/O-free, per the module's own
        rule); a test resolves every declared id against `corpus_index.load_chunks` instead.
    expected_category
        The dispute category a correct run confirms filing for; required exactly when
        `expected_intent` is `Intent.CONFIRM_FILING`, forbidden otherwise (see
        `_exactly_confirm_filing_cases_declare_a_target`). Grounds `correct_outcome`'s check in
        which transaction and category a run actually reached confirmation for, not merely that
        some confirmable state was reached — `seed_ref` already names the one transaction a
        `CONFIRM_FILING` case's filing targets (see below), so this field states the other half of
        that target without a second, redundant reference.
    injected_failure
        Set only for a case whose scripted condition is a tool call failing mid-flow; the runner's
        failure injector reads it to fail exactly that tool for this case's run. `None` for every
        other case, adversarial or not.
    description
        One line of free text: what the case tests and why, for the generated case sheet and for
        a person reading a failure gallery.
    """

    case_id: str
    category: CaseCategory
    lang: Lang
    provenance: Provenance
    seed_ref: str
    user_turns: tuple[str, ...]
    expected_intent: Intent
    expected_reason_code: ReasonCode | None = None
    expected_safe_behavior: SafeBehavior | None = None
    expected_policy_section_id: str | None = None
    expected_category: DisputeCategory | None = None
    injected_failure: InjectedToolFailure | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.case_id:
            raise ValueError("case_id must not be empty")
        if not self.user_turns:
            raise ValueError("user_turns must hold at least one turn")
        self._exactly_adversarial_cases_declare_a_safe_behavior()
        self._exactly_policy_answers_declare_a_section()
        self._exactly_confirm_filing_cases_declare_a_target()

    def _exactly_adversarial_cases_declare_a_safe_behavior(self) -> None:
        """`expected_safe_behavior` is set if and only if the case is adversarial."""
        is_adversarial = self.category is CaseCategory.ADVERSARIAL
        has_safe_behavior = self.expected_safe_behavior is not None
        if is_adversarial and not has_safe_behavior:
            raise ValueError("an adversarial case must declare expected_safe_behavior")
        if has_safe_behavior and not is_adversarial:
            raise ValueError("expected_safe_behavior is adversarial-only")

    def _exactly_policy_answers_declare_a_section(self) -> None:
        """`expected_policy_section_id` is set if and only if the reply is a policy answer."""
        is_policy_answer = self.expected_intent is Intent.POLICY_ANSWER
        has_section = self.expected_policy_section_id is not None
        if is_policy_answer and not has_section:
            raise ValueError("a policy-answer case must declare expected_policy_section_id")
        if has_section and not is_policy_answer:
            raise ValueError("expected_policy_section_id is policy-answer-only")

    def _exactly_confirm_filing_cases_declare_a_target(self) -> None:
        """`expected_category` is set if and only if the reply is a filing confirmation, and a
        `CONFIRM_FILING` case's `seed_ref` names exactly one transaction to confirm it for."""
        is_confirm_filing = self.expected_intent is Intent.CONFIRM_FILING
        has_category = self.expected_category is not None
        if is_confirm_filing and not has_category:
            raise ValueError("a confirm-filing case must declare expected_category")
        if has_category and not is_confirm_filing:
            raise ValueError("expected_category is confirm-filing-only")
        if is_confirm_filing and not self.seed_ref.startswith("ops_seed:TRX-"):
            raise ValueError(
                "a confirm-filing case's seed_ref must name exactly one ops_seed transaction"
            )

    @property
    def is_adversarial(self) -> bool:
        """Whether this case belongs to the adversarial suite."""
        return self.category is CaseCategory.ADVERSARIAL
