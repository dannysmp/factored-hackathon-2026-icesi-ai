"""
Dispute Policy Engine Tests
===========================

Component: ``app.domain.policy`` (models and engine). Hermetic and pure: the policy is the one
shipped in ``policy/``, requests are built in memory and the date is always passed in. The
boundary tests pin the last valid day and the first invalid one of every window and threshold;
the property tests pin determinism and the consistency of outcome and reason code.
"""

from __future__ import annotations

# Standard libraries
import ast  # Prove the engine reads neither the clock nor the file system
import copy  # Deep copies of a loaded policy
import pickle  # Round trip of a loaded policy
from datetime import date, timedelta  # Filing-window arithmetic
from decimal import Decimal  # Exact money in boundary cases
from pathlib import Path  # Locate the source files that must stay pure
from typing import Any  # Request overrides

# Third-party libraries
import pytest  # Test runner and parametrisation
from hypothesis import given  # Property-based tests
from hypothesis import strategies as st  # Value generators
from pydantic import ValidationError  # Boundary validation of requests

from app.domain.policy import (
    DisputeCategory,
    DisputeRequest,
    Outcome,
    Policy,
    ReasonCode,
    TransactionStatus,
    evaluate_dispute,
    load_policy,
)

# Local modules
from app.domain.policy.models import ReadOnlyMap

TODAY = date(2026, 9, 26)


@pytest.fixture(scope="module")
def policy() -> Policy:
    """The policy that ships with the repository."""
    return load_policy()


def make_request(**overrides: Any) -> DisputeRequest:
    """An eligible request; a test overrides only the fact it is about."""
    values: dict[str, Any] = {
        "category": DisputeCategory.UNRECOGNIZED_CHARGE,
        "transaction_date": TODAY - timedelta(days=10),
        "transaction_status": TransactionStatus.APPROVED,
        "transaction_type": "Purchase",
        "product_type": "Tarjeta Débito",
        "amount_usd": Decimal("100.00"),
        "nlu_confidence": 0.9,
        "is_repeat_complainer": False,
        "has_open_case_for_transaction": False,
        "risk_score": 0.1,
    }
    return DisputeRequest(**{**values, **overrides})


# -----------------------------------------------------------------------------
# Eligible requests
# -----------------------------------------------------------------------------


def test_a_request_that_satisfies_every_rule_is_eligible_and_needs_confirmation(
    policy: Policy,
) -> None:
    """The decision is stamped with the policy version and states the window it was measured in."""
    decision = evaluate_dispute(make_request(), policy, today=TODAY)

    assert decision.outcome is Outcome.ELIGIBLE
    assert decision.reason_code is ReasonCode.ELIGIBLE
    assert decision.requires_confirmation is True
    assert decision.policy_version == policy.version
    assert {fact.name: fact.value for fact in decision.facts} == {
        "category": "unrecognized_charge",
        "age_days": "10",
        "filing_window_days": "120",
    }
    assert decision.triggers == ()


def test_confirmation_follows_the_category_rule(policy: Policy) -> None:
    """A category whose rule needs no confirmation yields an eligible decision without it."""
    rule = policy.categories[DisputeCategory.WRONG_AMOUNT].model_copy(
        update={"requires_confirmation": False}
    )
    relaxed = policy.model_copy(
        update={"categories": {**policy.categories, DisputeCategory.WRONG_AMOUNT: rule}}
    )

    decision = evaluate_dispute(
        make_request(category=DisputeCategory.WRONG_AMOUNT), relaxed, today=TODAY
    )

    assert decision.outcome is Outcome.ELIGIBLE
    assert decision.requires_confirmation is False


# -----------------------------------------------------------------------------
# Eligibility gates
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category",
    [c for c in DisputeCategory if c is not DisputeCategory.FRAUD_CLAIM],
    ids=lambda c: c.value,
)
def test_the_filing_window_includes_its_last_day_and_excludes_the_next(
    policy: Policy, category: DisputeCategory
) -> None:
    """Day N is inside the window of every category and day N+1 is expired."""
    window = policy.categories[category].filing_window_days
    # A request that would otherwise be eligible, so only the window is under test
    base = {"category": category, "nlu_confidence": 1.0}

    last_day = evaluate_dispute(
        make_request(**base, transaction_date=TODAY - timedelta(days=window)),
        policy,
        today=TODAY,
    )
    next_day = evaluate_dispute(
        make_request(**base, transaction_date=TODAY - timedelta(days=window + 1)),
        policy,
        today=TODAY,
    )

    assert last_day.reason_code is not ReasonCode.FILING_WINDOW_EXPIRED
    assert next_day.outcome is Outcome.INELIGIBLE
    assert next_day.reason_code is ReasonCode.FILING_WINDOW_EXPIRED
    assert {fact.name: fact.value for fact in next_day.facts} == {
        "category": category.value,
        "age_days": str(window + 1),
        "filing_window_days": str(window),
    }


def test_the_fraud_window_still_marks_the_gate_for_the_person_who_receives_the_claim(
    policy: Policy,
) -> None:
    """Day 180 is inside the fraud window (no gate noted); day 181 is outside (gate noted)."""
    window = policy.categories[DisputeCategory.FRAUD_CLAIM].filing_window_days

    def facts(days: int) -> dict[str, str]:
        request = make_request(
            category=DisputeCategory.FRAUD_CLAIM, transaction_date=TODAY - timedelta(days=days)
        )
        decision = evaluate_dispute(request, policy, today=TODAY)
        assert decision.outcome is Outcome.ESCALATE
        return {fact.name: fact.value for fact in decision.facts}

    assert "eligibility_gate" not in facts(window)
    assert facts(window + 1)["eligibility_gate"] == "filing_window_expired"


def test_a_transaction_dated_today_is_inside_the_window(policy: Policy) -> None:
    """Age zero is valid."""
    decision = evaluate_dispute(make_request(transaction_date=TODAY), policy, today=TODAY)

    assert decision.outcome is Outcome.ELIGIBLE


def test_a_transaction_dated_in_the_future_is_ineligible(policy: Policy) -> None:
    """A future date cannot be a charge; the request is refused, not treated as age zero."""
    decision = evaluate_dispute(
        make_request(transaction_date=TODAY + timedelta(days=1)), policy, today=TODAY
    )

    assert decision.reason_code is ReasonCode.TRANSACTION_DATE_IN_FUTURE
    assert decision.outcome is Outcome.INELIGIBLE


@pytest.mark.parametrize(
    "product",
    ["Préstamo Personal", "Préstamo Hipotecario", "Inversión", "Seguro", "unknown product"],
)
def test_products_outside_the_scope_are_ineligible(policy: Policy, product: str) -> None:
    """Loans, investments, insurance and anything unrecognised are handled elsewhere."""
    decision = evaluate_dispute(make_request(product_type=product), policy, today=TODAY)

    assert decision.reason_code is ReasonCode.PRODUCT_OUT_OF_SCOPE
    assert decision.facts[0].value == product


@pytest.mark.parametrize(
    "product", ["Cuenta Ahorro", "Cuenta Corriente", "Tarjeta Crédito", "Tarjeta Débito"]
)
def test_the_four_in_scope_products_are_accepted(policy: Policy, product: str) -> None:
    """Each in-scope product passes the product gate."""
    decision = evaluate_dispute(make_request(product_type=product), policy, today=TODAY)

    assert decision.outcome is Outcome.ELIGIBLE


@pytest.mark.parametrize("kind", ["Deposit", "Adjustment", "Refund"])
def test_credits_and_unknown_types_are_not_disputable(policy: Policy, kind: str) -> None:
    """A deposit or a bank adjustment is not a charge to dispute."""
    decision = evaluate_dispute(make_request(transaction_type=kind), policy, today=TODAY)

    assert decision.reason_code is ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE


@pytest.mark.parametrize("kind", ["Purchase", "Withdrawal", "Transfer", "Payment"])
def test_the_four_charge_types_are_disputable(policy: Policy, kind: str) -> None:
    """Each charge type passes the type gate."""
    decision = evaluate_dispute(make_request(transaction_type=kind), policy, today=TODAY)

    assert decision.outcome is Outcome.ELIGIBLE


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (TransactionStatus.DECLINED, ReasonCode.TRANSACTION_DECLINED),
        (TransactionStatus.PENDING, ReasonCode.TRANSACTION_PENDING),
        (TransactionStatus.REVERSED, ReasonCode.TRANSACTION_REVERSED),
    ],
)
def test_only_approved_transactions_can_be_disputed(
    policy: Policy, status: TransactionStatus, reason: ReasonCode
) -> None:
    """Declined, pending and reversed transactions each have their own reason."""
    decision = evaluate_dispute(make_request(transaction_status=status), policy, today=TODAY)

    assert decision.outcome is Outcome.INELIGIBLE
    assert decision.reason_code is reason
    assert decision.facts[0].value == status.value


def test_every_status_but_approved_has_a_reason() -> None:
    """A new status added to the vocabulary cannot fall through the status gate unnoticed."""
    from app.domain.policy.engine import _STATUS_REASONS  # noqa: PLC0415 - internal table

    assert set(_STATUS_REASONS) == set(TransactionStatus) - {TransactionStatus.APPROVED}


def test_an_open_case_for_the_transaction_blocks_a_second_dispute(policy: Policy) -> None:
    """One transaction, one open dispute."""
    decision = evaluate_dispute(
        make_request(has_open_case_for_transaction=True), policy, today=TODAY
    )

    assert decision.outcome is Outcome.INELIGIBLE
    assert decision.reason_code is ReasonCode.DUPLICATE_OPEN_CASE


def test_gates_apply_in_a_fixed_order(policy: Policy) -> None:
    """With every gate failing, the product decides; removing one failure exposes the next."""
    failing: dict[str, Any] = {
        "product_type": "Seguro",
        "transaction_type": "Deposit",
        "transaction_status": TransactionStatus.DECLINED,
        "transaction_date": TODAY + timedelta(days=1),
        "has_open_case_for_transaction": True,
    }
    order = [
        ("product_type", "Tarjeta Débito", ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE),
        ("transaction_type", "Purchase", ReasonCode.TRANSACTION_DECLINED),
        ("transaction_status", TransactionStatus.APPROVED, ReasonCode.TRANSACTION_DATE_IN_FUTURE),
        ("transaction_date", TODAY - timedelta(days=500), ReasonCode.FILING_WINDOW_EXPIRED),
        ("transaction_date", TODAY, ReasonCode.DUPLICATE_OPEN_CASE),
    ]

    assert (
        evaluate_dispute(make_request(**failing), policy, today=TODAY).reason_code
        is ReasonCode.PRODUCT_OUT_OF_SCOPE
    )
    for field, fixed, expected in order:
        failing[field] = fixed
        decision = evaluate_dispute(make_request(**failing), policy, today=TODAY)
        assert decision.reason_code is expected


def test_a_non_fraud_request_that_cannot_be_filed_is_refused_not_escalated(
    policy: Policy,
) -> None:
    """A dispute the bank cannot take is refused before routing, whatever the routing says."""
    decision = evaluate_dispute(
        make_request(
            transaction_status=TransactionStatus.DECLINED,
            nlu_confidence=0.0,
            is_repeat_complainer=True,
            amount_usd=Decimal("99999"),
        ),
        policy,
        today=TODAY,
    )

    assert decision.outcome is Outcome.INELIGIBLE
    assert decision.triggers == ()


# -----------------------------------------------------------------------------
# Routing to a person
# -----------------------------------------------------------------------------


def test_a_fraud_claim_is_always_routed_to_a_person(policy: Policy) -> None:
    """Even a small, confident, clean fraud claim goes to a person."""
    decision = evaluate_dispute(
        make_request(category=DisputeCategory.FRAUD_CLAIM), policy, today=TODAY
    )

    assert decision.outcome is Outcome.ESCALATE
    assert decision.reason_code is ReasonCode.ESCALATE_FRAUD_CLAIM
    assert decision.requires_confirmation is False
    assert "eligibility_gate" not in {fact.name for fact in decision.facts}


@pytest.mark.parametrize(
    ("overrides", "gate"),
    [
        ({"transaction_status": TransactionStatus.DECLINED}, "transaction_declined"),
        ({"transaction_date": TODAY - timedelta(days=181)}, "filing_window_expired"),
        ({"transaction_date": TODAY + timedelta(days=1)}, "transaction_date_in_future"),
        ({"product_type": "Seguro"}, "product_out_of_scope"),
        ({"transaction_type": "Deposit"}, "transaction_type_not_disputable"),
        ({"has_open_case_for_transaction": True}, "duplicate_open_case"),
    ],
    ids=["declined", "expired", "future", "product", "type", "open-case"],
)
def test_a_fraud_claim_that_fails_a_gate_still_goes_to_a_person_with_the_gate_recorded(
    policy: Policy, overrides: dict[str, Any], gate: str
) -> None:
    """A customer reporting fraud is never refused by a rule; the person sees which gate failed."""
    decision = evaluate_dispute(
        make_request(category=DisputeCategory.FRAUD_CLAIM, **overrides), policy, today=TODAY
    )

    assert decision.outcome is Outcome.ESCALATE
    assert decision.reason_code is ReasonCode.ESCALATE_FRAUD_CLAIM
    assert {fact.name: fact.value for fact in decision.facts}["eligibility_gate"] == gate


def test_an_open_case_refusal_states_the_fact_it_rests_on(policy: Policy) -> None:
    """Every refusal carries the fact behind it."""
    decision = evaluate_dispute(
        make_request(has_open_case_for_transaction=True), policy, today=TODAY
    )

    assert [(fact.name, fact.value) for fact in decision.facts] == [
        ("has_open_case_for_transaction", "True")
    ]


def test_an_amount_at_the_threshold_is_escalated_and_one_cent_below_is_not(
    policy: Policy,
) -> None:
    """The threshold amount itself is reviewed by a person."""
    threshold = policy.routing.escalate_amount_usd

    at = evaluate_dispute(make_request(amount_usd=threshold), policy, today=TODAY)
    below = evaluate_dispute(
        make_request(amount_usd=threshold - Decimal("0.01")), policy, today=TODAY
    )

    assert at.reason_code is ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD
    assert below.outcome is Outcome.ELIGIBLE


def test_an_unknown_amount_is_escalated_unless_the_policy_says_otherwise(policy: Policy) -> None:
    """Without a US-dollar amount a person decides; the policy can turn that off."""
    unknown = make_request(amount_usd=None)
    permissive = policy.model_copy(
        update={"routing": policy.routing.model_copy(update={"escalate_unknown_amount": False})}
    )

    assert evaluate_dispute(unknown, policy, today=TODAY).reason_code is (
        ReasonCode.ESCALATE_AMOUNT_UNKNOWN
    )
    assert evaluate_dispute(unknown, permissive, today=TODAY).outcome is Outcome.ELIGIBLE


def test_confidence_equal_to_the_floor_passes_and_below_it_is_escalated(policy: Policy) -> None:
    """The floor is the lowest confidence a machine may act on."""
    floor = policy.routing.nlu_confidence_floor

    at = evaluate_dispute(make_request(nlu_confidence=floor), policy, today=TODAY)
    below = evaluate_dispute(make_request(nlu_confidence=floor - 0.01), policy, today=TODAY)

    assert at.outcome is Outcome.ELIGIBLE
    assert below.reason_code is ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE


def test_a_repeat_complainer_is_escalated_unless_the_policy_says_otherwise(policy: Policy) -> None:
    """The rule is a policy parameter."""
    repeat = make_request(is_repeat_complainer=True)
    lenient = policy.model_copy(
        update={"routing": policy.routing.model_copy(update={"escalate_repeat_complainer": False})}
    )

    assert evaluate_dispute(repeat, policy, today=TODAY).reason_code is (
        ReasonCode.ESCALATE_REPEAT_COMPLAINER
    )
    assert evaluate_dispute(repeat, lenient, today=TODAY).outcome is Outcome.ELIGIBLE


def test_a_risk_score_at_the_threshold_is_escalated_and_a_missing_score_is_ignored(
    policy: Policy,
) -> None:
    """The score only routes: at the threshold a person reviews; absent, nothing changes."""
    threshold = policy.routing.risk_score_threshold

    at = evaluate_dispute(make_request(risk_score=threshold), policy, today=TODAY)
    below = evaluate_dispute(make_request(risk_score=threshold - 0.01), policy, today=TODAY)
    absent = evaluate_dispute(make_request(risk_score=None), policy, today=TODAY)

    assert at.reason_code is ReasonCode.ESCALATE_RISK_SCORE
    assert below.outcome is Outcome.ELIGIBLE
    assert absent.outcome is Outcome.ELIGIBLE


def test_every_routing_trigger_is_reported_in_policy_order(policy: Policy) -> None:
    """The person receiving the case sees all triggers; the first is the reason code."""
    decision = evaluate_dispute(
        make_request(
            category=DisputeCategory.FRAUD_CLAIM,
            nlu_confidence=0.1,
            is_repeat_complainer=True,
            amount_usd=Decimal("9000"),
            risk_score=0.99,
        ),
        policy,
        today=TODAY,
    )

    assert decision.triggers == (
        ReasonCode.ESCALATE_FRAUD_CLAIM,
        ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,
        ReasonCode.ESCALATE_REPEAT_COMPLAINER,
        ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        ReasonCode.ESCALATE_RISK_SCORE,
    )
    assert decision.reason_code is decision.triggers[0]


def test_an_escalation_carries_the_facts_behind_each_rule(policy: Policy) -> None:
    """The handoff can show why: confidence, floor, amount, threshold and the score."""
    decision = evaluate_dispute(make_request(amount_usd=None, risk_score=0.5), policy, today=TODAY)

    facts = {fact.name: fact.value for fact in decision.facts}
    assert facts["amount_usd"] == "unknown"
    assert facts["risk_score"] == "0.5"
    assert facts["risk_score_threshold"] == str(policy.routing.risk_score_threshold)
    assert facts["nlu_confidence_floor"] == str(policy.routing.nlu_confidence_floor)


def test_the_facts_of_an_escalation_omit_a_missing_risk_score(policy: Policy) -> None:
    """A fact that was not available is not invented."""
    decision = evaluate_dispute(
        make_request(category=DisputeCategory.FRAUD_CLAIM, risk_score=None), policy, today=TODAY
    )

    assert "risk_score" not in {fact.name for fact in decision.facts}


# -----------------------------------------------------------------------------
# Request validation
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"nlu_confidence": 1.01},
        {"nlu_confidence": -0.01},
        {"risk_score": 1.5},
        {"amount_usd": Decimal("-1")},
        {"transaction_status": "Unknown"},
        {"is_repeat_complainer": "yes"},
        {"has_open_case_for_transaction": 0},
        {"category": "chargeback"},
        {"unexpected": True},
    ],
    ids=[
        "conf-high",
        "conf-low",
        "score-high",
        "negative-amount",
        "status",
        "text-as-bool",
        "number-as-bool",
        "category",
        "extra",
    ],
)
def test_a_malformed_request_is_rejected_at_the_boundary(overrides: dict[str, Any]) -> None:
    """The engine never sees an out-of-range or unknown value."""
    with pytest.raises(ValidationError):
        make_request(**overrides)


def test_requests_and_decisions_are_immutable(policy: Policy) -> None:
    """A decision cannot be edited after it is made."""
    decision = evaluate_dispute(make_request(), policy, today=TODAY)

    with pytest.raises(ValidationError):
        decision.outcome = Outcome.ESCALATE  # type: ignore[misc]


def test_a_loaded_policy_cannot_be_modified(policy: Policy) -> None:
    """Neither a field nor a category rule of a loaded policy can be reassigned."""
    with pytest.raises(ValidationError):
        policy.version = "2"  # type: ignore[misc]
    with pytest.raises(TypeError):
        policy.categories[DisputeCategory.FRAUD_CLAIM] = None  # type: ignore[assignment]
    with pytest.raises(TypeError):
        del policy.categories[DisputeCategory.WRONG_AMOUNT]


@pytest.mark.filterwarnings("error")
def test_a_loaded_policy_can_be_copied_pickled_and_serialised(policy: Policy) -> None:
    """Read-only does not mean unusable: every copy or round trip equals the original."""
    assert copy.deepcopy(policy) == policy
    assert policy.model_copy(deep=True) == policy
    assert pickle.loads(pickle.dumps(policy)) == policy  # noqa: S301 - our own object
    assert Policy.model_validate_json(policy.model_dump_json()) == policy
    assert Policy.model_validate(policy.model_dump()) == policy


def test_the_read_only_mapping_does_not_share_its_source() -> None:
    """Changing the dictionary it was built from does not change the mapping."""
    source = {"a": 1}
    mapping = ReadOnlyMap(source)

    source["a"] = 2
    source["b"] = 3

    assert dict(mapping) == {"a": 1}


def test_the_read_only_mapping_reads_like_a_mapping(policy: Policy) -> None:
    """Length, iteration, membership and representation behave as for a dictionary."""
    categories = policy.categories

    assert len(categories) == len(DisputeCategory)
    assert set(categories) == set(DisputeCategory)
    assert DisputeCategory.WRONG_AMOUNT in categories
    assert repr(categories).startswith("ReadOnlyMap(")


# -----------------------------------------------------------------------------
# Properties
# -----------------------------------------------------------------------------

requests = st.builds(
    DisputeRequest,
    category=st.sampled_from(list(DisputeCategory)),
    transaction_date=st.dates(min_value=date(2025, 1, 1), max_value=date(2027, 1, 1)),
    transaction_status=st.sampled_from(list(TransactionStatus)),
    transaction_type=st.sampled_from(["Purchase", "Withdrawal", "Transfer", "Payment", "Deposit"]),
    product_type=st.sampled_from(["Tarjeta Débito", "Cuenta Ahorro", "Seguro", "Inversión"]),
    amount_usd=st.none() | st.decimals(min_value=0, max_value=20000, places=2),
    nlu_confidence=st.floats(min_value=0, max_value=1),
    is_repeat_complainer=st.booleans(),
    has_open_case_for_transaction=st.booleans(),
    risk_score=st.none() | st.floats(min_value=0, max_value=1),
)
days = st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31))


@given(request=requests, today=days)
def test_the_same_inputs_always_give_the_same_decision(
    request: DisputeRequest, today: date
) -> None:
    """Determinism: two evaluations are equal."""
    policy = load_policy()

    assert evaluate_dispute(request, policy, today=today) == evaluate_dispute(
        request, policy, today=today
    )


@given(request=requests, today=days)
def test_outcome_reason_and_triggers_are_consistent(request: DisputeRequest, today: date) -> None:
    """Eligible has the eligible reason and no triggers; escalate is led by its first trigger."""
    decision = evaluate_dispute(request, load_policy(), today=today)

    if decision.outcome is Outcome.ELIGIBLE:
        assert decision.reason_code is ReasonCode.ELIGIBLE
        assert decision.triggers == ()
    elif decision.outcome is Outcome.ESCALATE:
        assert decision.triggers
        assert decision.reason_code is decision.triggers[0]
        assert decision.requires_confirmation is False
    else:
        assert not decision.reason_code.value.startswith("escalate_")
        assert decision.reason_code is not ReasonCode.ELIGIBLE
        assert decision.requires_confirmation is False


@given(request=requests, today=days)
def test_a_fraud_claim_is_never_refused(request: DisputeRequest, today: date) -> None:
    """Whatever the facts, a fraud claim is escalated: no rule refuses it."""
    fraud = request.model_copy(update={"category": DisputeCategory.FRAUD_CLAIM})

    decision = evaluate_dispute(fraud, load_policy(), today=today)

    assert decision.outcome is Outcome.ESCALATE
    assert decision.triggers[0] is ReasonCode.ESCALATE_FRAUD_CLAIM


@given(request=requests, today=days, shift=st.integers(min_value=-200, max_value=200))
def test_only_the_age_of_the_transaction_matters_not_the_calendar(
    request: DisputeRequest, today: date, shift: int
) -> None:
    """Moving the transaction date and today together changes neither outcome nor reasons."""
    policy = load_policy()
    moved = request.model_copy(
        update={"transaction_date": request.transaction_date + timedelta(days=shift)}
    )

    original = evaluate_dispute(request, policy, today=today)
    shifted = evaluate_dispute(moved, policy, today=today + timedelta(days=shift))

    assert (original.outcome, original.reason_code, original.triggers) == (
        shifted.outcome,
        shifted.reason_code,
        shifted.triggers,
    )


# -----------------------------------------------------------------------------
# Purity
# -----------------------------------------------------------------------------

_PACKAGE = Path(__file__).resolve().parents[1] / "app" / "domain" / "policy"
# The loader is the one module that touches the file system; everything else must be pure.
_PURE_MODULES = sorted(
    path for path in _PACKAGE.glob("*.py") if path.name not in {"loader.py", "__init__.py"}
)
_FORBIDDEN_IMPORTS = {
    "os",
    "pathlib",
    "time",
    "random",
    "socket",
    "subprocess",
    "yaml",
    "logging",
    "secrets",
    "sys",
    "io",
    "shutil",
    "requests",
    "httpx",
    "uuid",
}
# Attributes that read the clock, the file system or the environment.
_FORBIDDEN_ATTRIBUTES = {
    "today",
    "now",
    "utcnow",
    "read_text",
    "read_bytes",
    "getenv",
    "environ",
    "urandom",
}
# Bare names that read input, run code or generate randomness.
_FORBIDDEN_NAMES = {"open", "print", "input", "exec", "eval", "getenv", "uuid4", "random"}


def test_the_pure_modules_are_the_ones_expected() -> None:
    """The purity check covers the whole package: a new module is checked without editing this."""
    assert {path.name for path in _PURE_MODULES} == {"corpus.py", "engine.py", "models.py"}


@pytest.mark.parametrize("module", _PURE_MODULES, ids=lambda path: path.name)
def test_the_engine_reads_neither_the_clock_nor_the_file_system(module: Path) -> None:
    """No import of an I/O or time module, and no reference to a clock, file or environment."""
    tree = ast.parse(module.read_text(encoding="utf-8"))

    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}

    assert imported & _FORBIDDEN_IMPORTS == set()
    assert attributes & _FORBIDDEN_ATTRIBUTES == set()
    assert names & _FORBIDDEN_NAMES == set()
