"""
Independent Case-Row Oracle Tests
====================================

Component: ``evals.oracle``. Hermetic and pure: the policy is the one shipped in ``policy/``,
cases and transaction facts are built in memory, the date is always passed in.
"""

from __future__ import annotations

# Standard libraries
import ast  # Prove the module reads neither the clock nor the file system
from datetime import date, timedelta  # Filing-window arithmetic
from decimal import Decimal  # Exact money in the case fixture
from pathlib import Path  # Locate the source file that must stay pure

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from app.domain.policy import (
    DisputeCategory,
    Outcome,
    Policy,
    ReasonCode,
    TransactionStatus,
    load_policy,
)
from contracts.service_v1.cases import (
    AmountProvenance,
    CaseRecord,
    CaseStatus,
    DisclosedAmount,
    Money,
)
from evals.oracle import TransactionFacts, check_case, run_oracle

TODAY = date(2026, 9, 26)
ELIGIBLE_TRANSACTION_DATE = TODAY - timedelta(days=10)


@pytest.fixture(scope="module")
def policy() -> Policy:
    """The policy that ships with the repository."""
    return load_policy()


def make_case(**overrides: object) -> CaseRecord:
    """A case that matches an eligible request; a test overrides only what it varies."""
    values: dict[str, object] = {
        "case_number": "CASE-0001",
        "status": CaseStatus.OPEN,
        "transaction_ref": "TRX-0001",
        "category": DisputeCategory.UNRECOGNIZED_CHARGE,
        "amount": DisclosedAmount(
            money=Money(amount=Decimal("100.00"), currency="USD"),
            provenance=AmountProvenance.REPORTED,
        ),
        "domain_date": TODAY,
        "expected_first_response_date": TODAY + timedelta(days=5),
        "created_at_utc": "2026-09-26T12:00:00+00:00",
        "policy_version": "v1",
        "reason_code": ReasonCode.ELIGIBLE,
        "language": "es",
    }
    return CaseRecord(**{**values, **overrides})


def make_transaction(**overrides: object) -> TransactionFacts:
    """The facts of a transaction that clears every eligibility gate."""
    values: dict[str, object] = {
        "transaction_date": ELIGIBLE_TRANSACTION_DATE,
        "transaction_status": TransactionStatus.APPROVED,
        "transaction_type": "Purchase",
        "product_type": "Tarjeta Débito",
    }
    return TransactionFacts(**{**values, **overrides})  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# check_case: no violation
# -----------------------------------------------------------------------------


def test_a_case_whose_transaction_still_clears_every_gate_is_not_a_violation(
    policy: Policy,
) -> None:
    finding = check_case(make_case(), make_transaction(), policy, today=TODAY)

    assert finding.applicable is True
    assert finding.missing_transaction is False
    assert finding.recomputed_outcome is Outcome.ELIGIBLE
    assert finding.recomputed_reason_code is ReasonCode.ELIGIBLE
    assert finding.violation is False


# -----------------------------------------------------------------------------
# check_case: each eligibility gate, recomputed from the transaction alone
# -----------------------------------------------------------------------------


def test_a_transaction_outside_the_filing_window_is_a_violation(policy: Policy) -> None:
    window = policy.categories[DisputeCategory.UNRECOGNIZED_CHARGE].filing_window_days
    stale = make_transaction(transaction_date=TODAY - timedelta(days=window + 1))

    finding = check_case(make_case(), stale, policy, today=TODAY)

    assert finding.violation is True
    assert finding.recomputed_outcome is Outcome.INELIGIBLE
    assert finding.recomputed_reason_code is ReasonCode.FILING_WINDOW_EXPIRED


def test_a_declined_transaction_is_a_violation(policy: Policy) -> None:
    finding = check_case(
        make_case(),
        make_transaction(transaction_status=TransactionStatus.DECLINED),
        policy,
        today=TODAY,
    )

    assert finding.violation is True
    assert finding.recomputed_reason_code is ReasonCode.TRANSACTION_DECLINED


def test_a_non_disputable_transaction_type_is_a_violation(policy: Policy) -> None:
    finding = check_case(
        make_case(),
        make_transaction(transaction_type="Interest Payment"),
        policy,
        today=TODAY,
    )

    assert finding.violation is True
    assert finding.recomputed_reason_code is ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE


def test_an_out_of_scope_product_type_is_a_violation(policy: Policy) -> None:
    finding = check_case(
        make_case(), make_transaction(product_type="Not A Real Product"), policy, today=TODAY
    )

    assert finding.violation is True
    assert finding.recomputed_reason_code is ReasonCode.PRODUCT_OUT_OF_SCOPE


def test_a_transaction_dated_after_today_is_a_violation(policy: Policy) -> None:
    finding = check_case(
        make_case(),
        make_transaction(transaction_date=TODAY + timedelta(days=1)),
        policy,
        today=TODAY,
    )

    assert finding.violation is True
    assert finding.recomputed_reason_code is ReasonCode.TRANSACTION_DATE_IN_FUTURE


# -----------------------------------------------------------------------------
# check_case: the fraud-claim exception
# -----------------------------------------------------------------------------


def test_a_fraud_claim_is_not_applicable_rather_than_a_false_violation(policy: Policy) -> None:
    """The engine always escalates a fraud claim (never Outcome.ELIGIBLE); a stored fraud-claim
    case cannot have come through the automated eligibility path, so the oracle skips it."""
    fraud_case = make_case(category=DisputeCategory.FRAUD_CLAIM, reason_code=ReasonCode.ELIGIBLE)

    finding = check_case(fraud_case, make_transaction(), policy, today=TODAY)

    assert finding.applicable is False
    assert finding.recomputed_outcome is None
    assert finding.violation is False


def test_a_fraud_claim_is_not_applicable_even_when_its_own_transaction_would_fail_a_gate(
    policy: Policy,
) -> None:
    """The engine's fraud-claim exception bypasses gates 1-3 entirely; the oracle must not flag
    a fraud-claim case just because its transaction happens to be declined."""
    fraud_case = make_case(category=DisputeCategory.FRAUD_CLAIM)
    declined = make_transaction(transaction_status=TransactionStatus.DECLINED)

    finding = check_case(fraud_case, declined, policy, today=TODAY)

    assert finding.applicable is False
    assert finding.violation is False


# -----------------------------------------------------------------------------
# run_oracle
# -----------------------------------------------------------------------------


def test_run_oracle_checks_every_case_in_order(policy: Policy) -> None:
    cases = [
        make_case(case_number="CASE-0001", transaction_ref="TRX-0001"),
        make_case(case_number="CASE-0002", transaction_ref="TRX-0002"),
    ]
    transactions = {
        "TRX-0001": make_transaction(),
        "TRX-0002": make_transaction(transaction_status=TransactionStatus.DECLINED),
    }

    findings = run_oracle(cases, transactions, policy, today=TODAY)

    assert [f.case_number for f in findings] == ["CASE-0001", "CASE-0002"]
    assert findings[0].violation is False
    assert findings[1].violation is True


def test_run_oracle_flags_a_case_whose_transaction_is_not_known_rather_than_skipping_it(
    policy: Policy,
) -> None:
    orphan = make_case(transaction_ref="TRX-DOES-NOT-EXIST")

    (finding,) = run_oracle([orphan], {}, policy, today=TODAY)

    assert finding.missing_transaction is True
    assert finding.violation is True
    assert finding.recomputed_outcome is None


def test_run_oracle_of_no_cases_is_empty(policy: Policy) -> None:
    assert run_oracle([], {}, policy, today=TODAY) == ()


# -----------------------------------------------------------------------------
# Purity
# -----------------------------------------------------------------------------

# The same checks `tests/test_policy_engine.py` runs over `app.domain.policy`'s own pure
# modules, applied here since this module makes the identical purity claim.
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
_FORBIDDEN_NAMES = {"open", "print", "input", "exec", "eval", "getenv", "uuid4", "random"}


def test_the_module_reads_neither_the_clock_nor_the_file_system() -> None:
    """No import of an I/O or time module, and no reference to a clock, file or environment."""
    tree = ast.parse(Path("evals/oracle.py").read_text(encoding="utf-8"))

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
