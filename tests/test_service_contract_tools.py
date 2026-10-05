"""
Tool Contract Tests
=====================

Component: ``contracts.service_v1.tools``. Hermetic and pure: the contracts are declarative
models, so the tests check what they accept, what they refuse, and what they cannot carry.
The rule under test throughout: a value the create tool refuses is a permission reason, never a
policy reason, and no request here can express a foreign customer.
"""

from __future__ import annotations

from datetime import date
from typing import get_type_hints

import pytest
from pydantic import ValidationError

from app.domain.policy.models import (
    DisputeCategory,
    Outcome,
    PolicyDecision,
    ReasonCode,
    TransactionStatus,
)
from contracts.service_v1.cases import AmountProvenance, DisclosedAmount
from contracts.service_v1.tools import (
    PERMISSIONS,
    CreateDisputeCaseRequest,
    CreateDisputeCaseResult,
    EvaluateDisputeRequest,
    Permission,
    ProductLabel,
    Tool,
    ToolFailure,
    ToolPort,
    ToolRefusalCode,
    TransactionFact,
    TransactionFilters,
    TransactionPage,
)


def test_the_permissions_table_covers_every_tool() -> None:
    """A tool added to the closed set without an entry here would ship unreviewed."""
    assert set(PERMISSIONS) == set(Tool)


def test_only_the_create_tool_enforces_a_permission_invariant() -> None:
    """Every read tool enforces nothing beyond the session scoping every tool already has."""
    for tool in Tool:
        if tool is Tool.CREATE_DISPUTE_CASE:
            assert PERMISSIONS[tool]
        else:
            assert PERMISSIONS[tool] == frozenset()


def test_the_create_tool_requires_every_permission_invariant() -> None:
    """The create tool carries every invariant of the permission set, nothing assumed silently."""
    assert PERMISSIONS[Tool.CREATE_DISPUTE_CASE] == {
        Permission.CONFIRMED,
        Permission.IDEMPOTENT,
        Permission.SESSION_CREATE_CAP,
        Permission.NO_DUPLICATE_OPEN_CASE,
        Permission.CONTROLLER_ONLY,
    }


def test_no_tool_refusal_code_states_a_policy_verdict() -> None:
    """not_eligible and requires_person are not codes of this tool.

    ``duplicate_open_case`` is a deliberate exception: the policy's reason code names the same
    real condition the tool re-checks defensively at creation time, against a race between
    evaluation and confirmation; the two closed sets share the name for one condition on purpose.
    """
    refusal_values = {code.value for code in ToolRefusalCode}
    reason_values = {code.value for code in ReasonCode}
    shared_by_design = {ToolRefusalCode.DUPLICATE_OPEN_CASE.value}

    assert "not_eligible" not in refusal_values
    assert "requires_person" not in refusal_values
    assert (refusal_values - shared_by_design).isdisjoint(reason_values)


def test_transaction_filters_accept_no_window() -> None:
    """Filters are optional: an unfiltered read is a normal, valid request."""
    filters = TransactionFilters()

    assert filters.since is None
    assert filters.until is None


def test_transaction_filters_refuse_a_window_that_starts_after_it_ends() -> None:
    """A window whose start is after its end could never match anything."""
    with pytest.raises(ValidationError, match="since must not be after until"):
        TransactionFilters(since=date(2026, 2, 1), until=date(2026, 1, 1))


def test_transaction_filters_have_no_customer_field() -> None:
    """The session supplies the customer; a filter cannot express a foreign one."""
    assert "customer_id" not in TransactionFilters.model_fields


def test_transaction_filters_bound_the_merchant_text() -> None:
    """The merchant text is optional, never empty and no longer than a stored merchant name."""
    assert TransactionFilters().merchant is None
    assert TransactionFilters(merchant="x" * 80).merchant == "x" * 80
    for refused in ("", "x" * 81):
        with pytest.raises(ValidationError):
            TransactionFilters(merchant=refused)


@pytest.mark.parametrize("refused", [" ", "   ", "\u00a0"])
def test_transaction_filters_refuse_a_merchant_made_of_blanks(refused: str) -> None:
    """Blanks alone name nothing, and would drop every transaction that has no merchant."""
    with pytest.raises(ValidationError, match="merchant must hold more than blanks"):
        TransactionFilters(merchant=refused)


def test_transaction_filters_refuse_a_control_character_in_the_merchant() -> None:
    with pytest.raises(ValidationError, match="control or formatting character"):
        TransactionFilters(merchant="Super\x00Ahorro")


def test_a_merchant_name_holding_a_control_character_is_refused() -> None:
    """System-held text still refuses a control character before it reaches a render or a log."""
    with pytest.raises(ValidationError, match="control or formatting character"):
        TransactionFact(
            ref="TX-1",
            occurred_on=date(2026, 1, 1),
            merchant="Shop\x00Name",
            amount=DisclosedAmount(money=None, provenance=AmountProvenance.UNKNOWN),
            product=ProductLabel(name="Card", last4="1234"),
            status=TransactionStatus.APPROVED,
        )


def test_a_merchant_name_holding_a_bidirectional_override_is_refused() -> None:
    """A right-to-left override can make text render in an order that misleads a reader.

    Built through ``chr`` rather than a literal: the character itself is exactly what a linter's
    own Trojan Source check refuses to see written directly into source.
    """
    right_to_left_override = chr(0x202E)
    with pytest.raises(ValidationError, match="control or formatting character"):
        TransactionFact(
            ref="TX-1",
            occurred_on=date(2026, 1, 1),
            merchant=f"Shop{right_to_left_override}name",
            amount=DisclosedAmount(money=None, provenance=AmountProvenance.UNKNOWN),
            product=ProductLabel(name="Card", last4="1234"),
            status=TransactionStatus.APPROVED,
        )


def test_a_transaction_fact_shows_the_description_when_the_merchant_is_absent() -> None:
    """Most of the source data has no merchant; the description is a compatible fallback."""
    transaction = TransactionFact(
        ref="TX-1",
        occurred_on=date(2026, 1, 1),
        merchant=None,
        description="Recurring subscription payment",
        amount=DisclosedAmount(money=None, provenance=AmountProvenance.UNKNOWN),
        product=ProductLabel(name="Card", last4="1234"),
        status=TransactionStatus.APPROVED,
    )

    assert transaction.merchant is None
    assert transaction.description == "Recurring subscription payment"


def test_a_transaction_fact_may_carry_neither_merchant_nor_description() -> None:
    """The contract does not assume one of the two is always present in the source data."""
    transaction = TransactionFact(
        ref="TX-1",
        occurred_on=date(2026, 1, 1),
        merchant=None,
        amount=DisclosedAmount(money=None, provenance=AmountProvenance.UNKNOWN),
        product=ProductLabel(name="Card", last4="1234"),
        status=TransactionStatus.APPROVED,
    )

    assert transaction.merchant is None
    assert transaction.description is None


def test_a_transaction_page_total_count_covers_its_items() -> None:
    """A count below the items listed would understate what was found."""
    transaction = TransactionFact(
        ref="TX-1",
        occurred_on=date(2026, 1, 1),
        merchant=None,
        amount=DisclosedAmount(money=None, provenance=AmountProvenance.UNKNOWN),
        product=ProductLabel(name="Card", last4="1234"),
        status=TransactionStatus.APPROVED,
    )

    with pytest.raises(ValidationError, match="total_count is below"):
        TransactionPage(items=(transaction, transaction), total_count=1)


def test_a_transaction_page_rejects_a_negative_total_count() -> None:
    """The field bound alone refuses a count that could never be a real total."""
    with pytest.raises(ValidationError):
        TransactionPage(items=(), total_count=-1)


def test_an_empty_transaction_page_is_a_valid_result() -> None:
    """No match is a normal result, not an error."""
    page = TransactionPage()

    assert page.items == ()
    assert page.total_count == 0


def test_a_tool_failure_is_retryable_by_default() -> None:
    """A store failure is retryable unless the tool states otherwise."""
    failure = ToolFailure(tool=Tool.GET_TRANSACTION, cause="timeout")

    assert failure.retryable is True


def _decision(**overrides: object) -> PolicyDecision:
    """Build an eligible policy decision that requires confirmation, with ``overrides`` applied.

    Parameters
    ----------
    **overrides : object
        Field values that replace the defaults, so a test varies exactly one thing.
    """
    fields: dict[str, object] = {
        "outcome": Outcome.ELIGIBLE,
        "reason_code": ReasonCode.ELIGIBLE,
        "policy_version": "1",
        "requires_confirmation": True,
        "facts": (),
        "transaction_ref": "TRX-1",
        "category": DisputeCategory.UNRECOGNIZED_CHARGE,
    }
    fields.update(overrides)
    return PolicyDecision(**fields)


def _create_request(**overrides: object) -> CreateDisputeCaseRequest:
    """Build a confirmed create request carrying an eligible decision, with ``overrides`` applied.

    Parameters
    ----------
    **overrides : object
        Field values that replace the defaults, so a test varies exactly one thing.

    Raises
    ------
    pydantic.ValidationError
        The overrides make the request invalid.
    """
    fields: dict[str, object] = {
        "transaction_ref": "TX-1",
        "category": DisputeCategory.UNRECOGNIZED_CHARGE,
        "confirmed": True,
        "idempotency_key": "IDEM-1",
        "decision": _decision(),
    }
    fields.update(overrides)
    return CreateDisputeCaseRequest(**fields)


def test_a_create_request_may_carry_no_decision() -> None:
    """A missing decision is representable, so a fail-closed refusal can be exercised."""
    request = _create_request(decision=None)

    assert request.decision is None


def test_a_create_request_has_no_customer_field() -> None:
    """The session supplies the customer; a create request cannot name a foreign one."""
    assert "customer_id" not in CreateDisputeCaseRequest.model_fields


def test_a_create_result_is_exactly_one_of_created_or_refused() -> None:
    """A result cannot claim both outcomes, and cannot claim neither."""
    with pytest.raises(ValidationError, match="mutually exclusive"):
        CreateDisputeCaseResult(
            created=True, case_number="CASE-1", refusal=ToolRefusalCode.CONFIRMATION_REQUIRED
        )
    with pytest.raises(ValidationError, match="mutually exclusive"):
        CreateDisputeCaseResult(created=False)


def test_a_created_result_carries_its_case_number() -> None:
    """A created case is reported with the number that names it."""
    result = CreateDisputeCaseResult(created=True, case_number="CASE-1")

    assert result.case_number == "CASE-1"


def test_a_refused_result_carries_no_case_number() -> None:
    """A refusal never reports a number for a case that was never filed."""
    with pytest.raises(ValidationError, match="case_number is present exactly"):
        CreateDisputeCaseResult(
            created=False, case_number="CASE-1", refusal=ToolRefusalCode.CONFIRMATION_REQUIRED
        )


def test_a_duplicate_open_case_refusal_carries_the_existing_case_number() -> None:
    """The customer is told which case is already on file for the transaction."""
    result = CreateDisputeCaseResult(
        created=False,
        refusal=ToolRefusalCode.DUPLICATE_OPEN_CASE,
        existing_case_number="CASE-1",
    )

    assert result.existing_case_number == "CASE-1"


def test_only_a_duplicate_open_case_refusal_may_carry_an_existing_case_number() -> None:
    """Any other refusal, or a created case, names no pre-existing case (nothing to name)."""
    with pytest.raises(ValidationError, match="existing_case_number is present exactly"):
        CreateDisputeCaseResult(
            created=False,
            refusal=ToolRefusalCode.CONFIRMATION_REQUIRED,
            existing_case_number="CASE-1",
        )


def test_a_duplicate_open_case_refusal_requires_the_existing_case_number() -> None:
    """The field is required, not merely allowed, once the refusal is duplicate_open_case."""
    with pytest.raises(ValidationError, match="existing_case_number is present exactly"):
        CreateDisputeCaseResult(created=False, refusal=ToolRefusalCode.DUPLICATE_OPEN_CASE)


def test_evaluate_dispute_request_has_no_customer_field() -> None:
    """The transaction is looked up server-side; the request carries no foreign customer."""
    assert "customer_id" not in EvaluateDisputeRequest.model_fields


def test_the_tool_port_declares_every_tool() -> None:
    """The port's methods name exactly the closed set of tools this contract declares."""
    assert set(Tool) == {
        Tool.LIST_TRANSACTIONS,
        Tool.GET_TRANSACTION,
        Tool.LIST_DISPUTE_CASES,
        Tool.GET_CASE,
        Tool.EVALUATE_DISPUTE,
        Tool.CREATE_DISPUTE_CASE,
    }
    methods = {
        name
        for name, value in vars(ToolPort).items()
        if not name.startswith("_") and callable(value)
    }
    assert methods == {
        "list_transactions",
        "get_transaction",
        "list_dispute_cases",
        "get_case",
        "evaluate_dispute",
        "create_dispute_case",
    }


def test_create_dispute_case_declares_a_store_failure_path() -> None:
    """A store or audit-write failure fails the filing closed, never silently."""
    hints = get_type_hints(ToolPort.create_dispute_case)

    assert hints["return"] == CreateDisputeCaseResult | ToolFailure
