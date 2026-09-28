"""
Dialogue Controller Tests
=========================

Component: ``app.conversation.controller``. Hermetic: an in-memory dialogue store, hand-rolled
fakes for the tool port and the handoff outbox, and a scripted understanding stub per turn — no
network, no real Postgres. The policy question tests reuse the real, committed corpus and policy,
matching ``tests/test_policy_answer.py``'s own fixtures.
"""

from __future__ import annotations

# Standard libraries
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal

# Third-party libraries
import psycopg
import pytest

# Local modules
from app.conversation.controller import (
    _ESCALATE_TRIGGER_OF,
    _REQUEST_SUMMARY_OF,
    _ROUTES,
    DialogueController,
    _idempotency_key,
    _matches_hint,
)
from app.conversation.handoff import build_packet
from app.conversation.state import ConversationPhase, DialogueState
from app.conversation.store import Conflict, DuplicateTurn, InMemoryDialogueStore
from app.conversation.understanding import UnderstandingUnavailable
from app.domain.policy.loader import load_policy
from app.domain.policy.models import (
    DisputeCategory,
    Outcome,
    Policy,
    PolicyDecision,
    ReasonCode,
    TransactionStatus,
)
from app.domain.policy.models import Fact as PolicyFact
from app.persistence.handoff_outbox import HandoffContent
from app.retrieval.lexical import LexicalRetriever
from app.security.errors import ErrorCode, ProblemError
from app.security.sessions import Principal
from contracts.service_v1.api import TurnRequest
from contracts.service_v1.cases import AmountProvenance, CaseRecord, CaseStatus, DisclosedAmount
from contracts.service_v1.cases import Money as CaseMoney
from contracts.service_v1.envelope import CUSTOMER_REASON_OF, CustomerReason, Slot
from contracts.service_v1.handoff import HandoffPacket, HandoffTrigger
from contracts.service_v1.nlu import ConfirmationAnswer, NluIntent, NluResult, TransactionHint
from contracts.service_v1.tools import (
    CreateDisputeCaseResult,
    ProductLabel,
    ToolFailure,
    ToolRefusalCode,
    TransactionFact,
    TransactionFilters,
    TransactionPage,
)
from contracts.service_v1.tools import Tool as ToolName

_DOMAIN_DATE = date(2026, 6, 18)
_NOW = datetime(2026, 6, 18, 15, 0, tzinfo=UTC)
_SESSION_ID = "sess-1"
_CUSTOMER_ID = "CUST-1"


def _now() -> datetime:
    return _NOW


def _principal() -> Principal:
    return Principal(
        customer_id=_CUSTOMER_ID,
        session_id=_SESSION_ID,
        issued_at=_NOW,
        expires_at=_NOW,
        audience="customer",
    )


def _turn(turn_id: str, text: str = "hola") -> TurnRequest:
    return TurnRequest(turn_id=turn_id, text=text)


def _transaction(
    ref: str = "TX-1",
    *,
    occurred_on: date = _DOMAIN_DATE,
    merchant: str | None = "Amazon",
    amount: Decimal | None = Decimal("100.00"),
    currency: str = "USD",
    last4: str = "1234",
) -> TransactionFact:
    disclosed = (
        DisclosedAmount(
            money=CaseMoney(amount=amount, currency=currency), provenance=AmountProvenance.REPORTED
        )
        if amount is not None
        else DisclosedAmount(money=None, provenance=AmountProvenance.UNKNOWN)
    )
    return TransactionFact(
        ref=ref,
        occurred_on=occurred_on,
        merchant=merchant,
        amount=disclosed,
        product=ProductLabel(name="Credit Card", last4=last4),
        status=TransactionStatus.APPROVED,
    )


def _case(
    case_number: str = "D-1",
    *,
    transaction_ref: str = "TX-1",
    category: DisputeCategory = DisputeCategory.UNRECOGNIZED_CHARGE,
    status: CaseStatus = CaseStatus.OPEN,
) -> CaseRecord:
    return CaseRecord(
        case_number=case_number,
        status=status,
        transaction_ref=transaction_ref,
        category=category,
        amount=DisclosedAmount(
            money=CaseMoney(amount=Decimal("100.00"), currency="USD"),
            provenance=AmountProvenance.REPORTED,
        ),
        domain_date=_DOMAIN_DATE,
        expected_first_response_date=date(2026, 7, 18),
        created_at_utc=_NOW,
        policy_version="2",
        reason_code=ReasonCode.ELIGIBLE,
        language="es",
    )


def _decision(
    outcome: Outcome,
    reason_code: ReasonCode,
    *,
    requires_confirmation: bool = False,
    ref: str = "TX-1",
    category: DisputeCategory = DisputeCategory.UNRECOGNIZED_CHARGE,
    triggers: tuple[ReasonCode, ...] = (),
) -> PolicyDecision:
    return PolicyDecision(
        outcome=outcome,
        reason_code=reason_code,
        policy_version="2",
        requires_confirmation=requires_confirmation,
        facts=(PolicyFact(name="checked", value="true"),),
        triggers=triggers,
        transaction_ref=ref,
        category=category,
    )


@dataclass
class ScriptedNlu:
    """Returns the same, pre-built understanding for every message this turn."""

    result: NluResult
    calls: list[tuple[str, str | None]] = field(default_factory=list)

    def understand(self, text: str, *, language_hint: str | None) -> NluResult:
        self.calls.append((text, language_hint))
        return self.result


@dataclass
class UnavailableNlu:
    """An ``Understanding`` whose own dependency is never reachable (E9)."""

    def understand(self, text: str, *, language_hint: str | None) -> NluResult:
        raise UnderstandingUnavailable("the provider could not be reached")


_UNSET = object()  # A distinct sentinel from a deliberately-returned None override.


@dataclass
class FakeToolPort:
    """A hand-rolled ``ToolPort``: canned answers, no store."""

    transactions: tuple[TransactionFact, ...] = ()
    cases: tuple[CaseRecord, ...] = ()
    evaluate_result: PolicyDecision | ToolFailure | None = None
    create_result: CreateDisputeCaseResult | ToolFailure | None = None
    list_transactions_result: TransactionPage | ToolFailure | None = None
    list_cases_result: tuple[CaseRecord, ...] | ToolFailure | None = None
    get_case_result: object = _UNSET
    get_transaction_result: object = _UNSET
    create_calls: int = 0

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        if self.list_transactions_result is not None:
            return self.list_transactions_result
        items = [
            t
            for t in self.transactions
            if (filters.since is None or t.occurred_on >= filters.since)
            and (filters.until is None or t.occurred_on <= filters.until)
        ]
        return TransactionPage(items=tuple(items[:5]), total_count=len(items))

    def get_transaction(self, ref: str) -> TransactionFact | ToolFailure | None:
        if self.get_transaction_result is not _UNSET:
            return self.get_transaction_result  # type: ignore[return-value]
        return next((t for t in self.transactions if t.ref == ref), None)

    def list_dispute_cases(self) -> tuple[CaseRecord, ...] | ToolFailure:
        if self.list_cases_result is not None:
            return self.list_cases_result
        return self.cases

    def get_case(self, case_number: str) -> CaseRecord | ToolFailure | None:
        if self.get_case_result is not _UNSET:
            return self.get_case_result  # type: ignore[return-value]
        return next((c for c in self.cases if c.case_number == case_number), None)

    def evaluate_dispute(self, request: object) -> PolicyDecision | ToolFailure:
        assert self.evaluate_result is not None
        return self.evaluate_result

    def create_dispute_case(self, request: object) -> CreateDisputeCaseResult | ToolFailure:
        self.create_calls += 1
        assert self.create_result is not None
        return self.create_result


@dataclass
class FakeHandoffOutbox:
    """Builds a real packet via the actual builder, without touching a store."""

    fail: bool = False
    packets: list[HandoffPacket] = field(default_factory=list)

    def record(
        self, content: HandoffContent, *, session_id: str, turn_id: str, trace_id: str
    ) -> HandoffPacket:
        if self.fail:
            raise psycopg.OperationalError("outbox unreachable")
        packet = build_packet(
            ticket_ref=f"T-{len(self.packets) + 1:04d}",
            reference_date=content.reference_date,
            created_at=content.created_at,
            language=content.language,
            trigger=content.trigger,
            first_name=content.first_name,
            customer_id=content.customer_id,
            request_summary=content.request_summary,
            reason_codes=content.reason_codes,
            policy_version=content.policy_version,
            category=content.category,
            verified_facts=content.verified_facts,
            actions=content.actions,
            attempted_action=content.attempted_action,
            existing_case_number=content.existing_case_number,
        )
        self.packets.append(packet)
        return packet


@pytest.fixture
def policy() -> Policy:
    return load_policy()


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    return LexicalRetriever.from_corpus()


def _controller(
    result: NluResult,
    *,
    store: InMemoryDialogueStore,
    tool_port: FakeToolPort,
    policy: Policy,
    outbox: FakeHandoffOutbox,
    retriever: LexicalRetriever,
) -> tuple[DialogueController, ScriptedNlu]:
    nlu = ScriptedNlu(result)
    controller = DialogueController(
        nlu,
        store=store,
        tool_port=tool_port,
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
    )
    return controller, nlu


_EMPTY_HINT = TransactionHint()


def _file_dispute(
    *, transaction: TransactionHint = _EMPTY_HINT, category: DisputeCategory | None = None
) -> NluResult:
    return NluResult(
        intent=NluIntent.FILE_DISPUTE,
        confidence=0.8,
        language="es",
        transaction=transaction,
        category=category,
    )


def _confirmation(answer: ConfirmationAnswer) -> NluResult:
    return NluResult(
        intent=NluIntent.CONFIRMATION, confidence=0.9, language="es", confirmation=answer
    )


def _plain(intent: NluIntent, **kwargs: object) -> NluResult:
    kwargs.setdefault("language", "es")
    return NluResult(intent=intent, confidence=0.8, **kwargs)


# -----------------------------------------------------------------------------
# Exhaustiveness
# -----------------------------------------------------------------------------


def test_routes_are_exhaustive_over_nlu_intent() -> None:
    assert set(_ROUTES) == set(NluIntent)


def test_request_summaries_are_exhaustive_over_handoff_trigger() -> None:
    assert set(_REQUEST_SUMMARY_OF) == set(HandoffTrigger)


def test_escalate_triggers_cover_every_escalate_reason_code() -> None:
    escalate_codes = {
        code for code in ReasonCode if CUSTOMER_REASON_OF[code] is CustomerReason.NEEDS_REVIEW
    }
    assert set(_ESCALATE_TRIGGER_OF) == escalate_codes


# -----------------------------------------------------------------------------
# Pure helpers
# -----------------------------------------------------------------------------


def test_matches_hint_checks_every_given_field() -> None:
    fact = _transaction(merchant="Amazon", amount=Decimal("50.00"), currency="USD", last4="1234")

    assert _matches_hint(fact, TransactionHint())
    assert _matches_hint(fact, TransactionHint(merchant="amazon"))
    assert not _matches_hint(fact, TransactionHint(merchant="Netflix"))
    assert _matches_hint(fact, TransactionHint(amount=Decimal("50.00")))
    assert not _matches_hint(fact, TransactionHint(amount=Decimal("99.00")))
    assert not _matches_hint(fact, TransactionHint(currency="EUR"))
    assert _matches_hint(fact, TransactionHint(product_last4="1234"))
    assert not _matches_hint(fact, TransactionHint(product_last4="9999"))


def test_matches_hint_falls_back_to_description_when_merchant_is_absent() -> None:
    fact = _transaction(merchant=None).model_copy(update={"description": "AMZN MKTP US"})
    assert _matches_hint(fact, TransactionHint(merchant="amzn"))


def test_matches_hint_is_accent_and_case_insensitive() -> None:
    """Spanish and Portuguese merchant names carry accents the customer may not retype."""
    fact = _transaction(merchant="Café Colombia")
    assert _matches_hint(fact, TransactionHint(merchant="café"))
    assert _matches_hint(fact, TransactionHint(merchant="CAFÉ"))
    assert _matches_hint(fact, TransactionHint(merchant="colombia"))


def test_matches_hint_never_matches_an_unknown_amount_against_a_stated_one() -> None:
    fact = _transaction(amount=None)
    assert not _matches_hint(fact, TransactionHint(amount=Decimal("10.00")))


def test_idempotency_key_is_deterministic_and_well_shaped() -> None:
    key = _idempotency_key("turn-abc")
    assert key == _idempotency_key("turn-abc")
    assert key != _idempotency_key("turn-xyz")
    assert len(key) <= 32
    assert key.isalnum()


# -----------------------------------------------------------------------------
# First message and language
# -----------------------------------------------------------------------------


def test_ambiguous_first_message_offers_both_languages() -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.UNCLEAR, language=None),
        store=store,
        tool_port=FakeToolPort(),
        policy=load_policy(),
        outbox=FakeHandoffOutbox(),
        retriever=LexicalRetriever.from_corpus(),
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.lang == "es"
    assert "español" in response.reply.lower() or "espanhol" in response.reply.lower()


def test_switch_language_updates_the_conversation_language() -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.SWITCH_LANGUAGE, requested_language="pt"),
        store=store,
        tool_port=FakeToolPort(),
        policy=load_policy(),
        outbox=FakeHandoffOutbox(),
        retriever=LexicalRetriever.from_corpus(),
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.lang == "pt"


def test_small_talk_and_farewell(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    greeting = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert not greeting.end_session

    controller, _ = _controller(
        _plain(NluIntent.FAREWELL),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    farewell = controller.handle_turn(_turn("turn-0002"), principal=_principal())
    assert farewell.end_session


# -----------------------------------------------------------------------------
# Filing a dispute: slot collection, search, evaluation, filing, verification
# -----------------------------------------------------------------------------


def test_file_dispute_asks_for_transaction_then_presents_a_single_match(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    port = FakeToolPort(transactions=(_transaction(),))
    outbox = FakeHandoffOutbox()

    controller, _ = _controller(
        _file_dispute(),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    asked = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert asked.next_expected is Slot.TRANSACTION

    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    presented = controller.handle_turn(_turn("turn-0002"), principal=_principal())
    assert "Amazon" in presented.reply
    state = store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-1"


def test_no_match_states_not_found(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Nobody")),
        store=store,
        tool_port=FakeToolPort(transactions=(_transaction(),)),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.next_expected is None
    assert store.get(_SESSION_ID).selected_ref is None  # type: ignore[union-attr]


def test_multiple_matches_ask_for_detail_then_escalate_at_the_budget(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(transactions=(_transaction("TX-1"), _transaction("TX-2")))

    for i in range(1, policy.routing.clarification_budget + 1):
        controller, _ = _controller(
            _file_dispute(transaction=TransactionHint(merchant="Amazon")),
            store=store,
            tool_port=port,
            policy=policy,
            outbox=outbox,
            retriever=retriever,
        )
        response = controller.handle_turn(_turn(f"turn-{i:04d}"), principal=_principal())

    assert response.end_session
    assert outbox.packets
    assert outbox.packets[0].trigger.value == "low_understanding"


def test_eligible_decision_requiring_confirmation_then_yes_files_and_verifies(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    transaction = _transaction()
    case = _case()
    port = FakeToolPort(
        transactions=(transaction,),
        cases=(case,),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )

    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    confirm = controller.handle_turn(_turn("turn-0002"), principal=_principal())
    assert confirm.next_expected is Slot.CONFIRMATION

    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.YES),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    filed = controller.handle_turn(_turn("turn-0003"), principal=_principal())
    assert "D-1" in filed.reply
    assert store.get(_SESSION_ID).last_case_number == "D-1"  # type: ignore[union-attr]


def test_confirmation_no_cancels_the_filing(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    store.save(
        DialogueState(
            session_id=_SESSION_ID,
            lang="es",
            phase=ConversationPhase.CONFIRMING,
            pending_slot=Slot.CONFIRMATION,
            selected_ref="TX-1",
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
            updated_at=_NOW,
        ),
        expected_version=0,
        turn_id="turn-setup",
        now=_now(),
    )
    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.NO),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.next_expected is None
    assert not response.end_session


def test_ineligible_decision_states_the_reason(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(Outcome.INELIGIBLE, ReasonCode.FILING_WINDOW_EXPIRED),
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0002"), principal=_principal())
    assert not response.end_session
    assert response.next_expected is None


def test_replaying_an_ineligible_turn_never_re_evaluates_or_files(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A retried turn that originally read as ineligible must not be recomputed: `evaluate_dispute`
    reads the store's current facts, not the ones the original decision rested on, so a fact that
    changed since (here: the same port now answering eligible, with no confirmation required)
    could otherwise turn a past refusal into a filing with no new confirmation from the customer."""
    store = InMemoryDialogueStore()
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(Outcome.INELIGIBLE, ReasonCode.FILING_WINDOW_EXPIRED),
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    original = controller.handle_turn(_turn("turn-0002"), principal=_principal())

    # The world moved on: the same transaction and category would now be filed outright.
    port.evaluate_result = _decision(
        Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=False
    )
    port.create_result = CreateDisputeCaseResult(created=True, case_number="D-999")

    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    replay = controller.handle_turn(_turn("turn-0002"), principal=_principal())

    # No re-filing happened, whatever the reply says: a decision that carried no case or ticket
    # is replayed with a generic, still-truthful acknowledgment rather than recomputed, precisely
    # because recomputing is what would have refiled it here.
    assert port.create_calls == 0
    assert store.get(_SESSION_ID).last_case_number is None  # type: ignore[union-attr]
    assert not replay.end_session
    assert replay.state_version == original.state_version


@pytest.mark.parametrize(
    ("reason_code", "expects_fraud_wording"),
    [(ReasonCode.ESCALATE_FRAUD_CLAIM, True), (ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD, False)],
)
def test_escalate_decision_hands_off(
    policy: Policy,
    retriever: LexicalRetriever,
    reason_code: ReasonCode,
    expects_fraud_wording: bool,
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        transactions=(_transaction(),), evaluate_result=_decision(Outcome.ESCALATE, reason_code)
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0002"), principal=_principal())
    assert response.end_session
    assert response.handoff_ticket is not None
    assert outbox.packets[0].evidence.reason_codes == (reason_code,)


def test_duplicate_open_case_refusal_is_presented_as_ineligible(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(
            created=False, refusal=ToolRefusalCode.DUPLICATE_OPEN_CASE, existing_case_number="D-9"
        ),
    )
    outbox = FakeHandoffOutbox()
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0002"), principal=_principal())
    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.YES),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0003"), principal=_principal())
    assert not response.end_session
    assert not outbox.packets


def test_unverified_filing_hands_off(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
        get_case_result=None,
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0002"), principal=_principal())
    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.YES),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0003"), principal=_principal())
    assert response.end_session
    assert outbox.packets[0].trigger.value == "filing_unverified"


def test_no_confirmation_required_files_immediately(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    case = _case()
    port = FakeToolPort(
        transactions=(_transaction(),),
        cases=(case,),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=False
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0002"), principal=_principal())
    assert "D-1" in response.reply


def test_tool_failure_during_search_hands_off(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        list_transactions_result=ToolFailure(tool=ToolName.LIST_TRANSACTIONS, cause="error")
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.end_session
    assert outbox.packets[0].trigger.value == "tool_failure"


# -----------------------------------------------------------------------------
# Browsing, status and policy questions
# -----------------------------------------------------------------------------


def test_list_transactions_presents_or_states_not_found(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.LIST_TRANSACTIONS),
        store=store,
        tool_port=FakeToolPort(transactions=(_transaction(),)),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.next_expected is None
    assert "coincid" in response.reply.lower()

    controller, _ = _controller(
        _plain(NluIntent.LIST_TRANSACTIONS),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    empty = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert "no encontré" in empty.reply.lower()


def test_dispute_status_presents_cases_or_states_none(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.DISPUTE_STATUS),
        store=store,
        tool_port=FakeToolPort(cases=(_case(),)),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert "D-1" in response.reply

    controller, _ = _controller(
        _plain(NluIntent.DISPUTE_STATUS),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    none_found = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert none_found.reply


def test_policy_question_answers_or_abstains(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(
            NluIntent.POLICY_QUESTION,
            policy_query="que es esta politica",
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        ),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    answered = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert answered.reply

    controller, _ = _controller(
        _plain(NluIntent.POLICY_QUESTION, policy_query="xyzzy nonsense gibberish"),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    abstained = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert abstained.reply


@pytest.mark.parametrize(
    "intent",
    [
        NluIntent.REPORT_FRAUD,
        NluIntent.REPORT_CARD_LOSS,
        NluIntent.REQUEST_PERSON,
    ],
)
def test_reports_and_requests_hand_off(
    policy: Policy, retriever: LexicalRetriever, intent: NluIntent
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    controller, _ = _controller(
        _plain(intent),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.end_session
    assert response.handoff_ticket is not None


@pytest.mark.parametrize("intent", [NluIntent.REQUEST_REVERSAL, NluIntent.UNSUPPORTED_ACTION])
def test_reversal_and_unsupported_action_refuse_without_handoff(
    policy: Policy, retriever: LexicalRetriever, intent: NluIntent
) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(intent),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert not response.end_session
    assert response.handoff_ticket is None


# -----------------------------------------------------------------------------
# Idempotent replay and conflicts
# -----------------------------------------------------------------------------


def test_a_repeated_turn_id_replays_a_filed_case_without_refiling(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    case = _case()
    port = FakeToolPort(
        transactions=(_transaction(),),
        cases=(case,),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0002"), principal=_principal())
    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.YES),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    first = controller.handle_turn(_turn("turn-0003"), principal=_principal())

    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.YES),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    replay = controller.handle_turn(_turn("turn-0003"), principal=_principal())
    assert replay.reply == first.reply
    assert replay.state_version == first.state_version


def test_a_repeated_turn_id_replays_a_pending_clarification(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _file_dispute(),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    first = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    controller, _ = _controller(
        _file_dispute(),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    replay = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert replay.reply == first.reply
    assert replay.state_version == first.state_version


def test_a_repeated_turn_id_replays_a_handoff(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    controller, _ = _controller(
        _plain(NluIntent.REQUEST_PERSON),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    first = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    controller, _ = _controller(
        _plain(NluIntent.REQUEST_PERSON),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    replay = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert replay.handoff_ticket == first.handoff_ticket
    assert len(outbox.packets) == 1


@dataclass
class _AlwaysConflictStore:
    """Wraps a real store but makes every save raise ``Conflict``, whatever it is asked to save."""

    inner: InMemoryDialogueStore

    def get(self, session_id: str):  # type: ignore[no-untyped-def]
        return self.inner.get(session_id)

    def save(self, state, *, expected_version, turn_id, now):  # type: ignore[no-untyped-def]
        raise Conflict(f"session {state.session_id} moved on")


def test_a_concurrent_conflict_raises_turn_conflict(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = _AlwaysConflictStore(InMemoryDialogueStore())
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=store,  # type: ignore[arg-type]
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    with pytest.raises(ProblemError) as excinfo:
        controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert excinfo.value.code is ErrorCode.TURN_CONFLICT


def test_handoff_not_registered_when_the_outbox_fails(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.REQUEST_PERSON),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(fail=True),
        retriever=retriever,
    )
    with caplog.at_level(logging.WARNING):
        response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.end_session
    assert response.handoff_ticket is None
    logged = [r for r in caplog.records if "handoff_not_registered" in r.getMessage()]
    assert logged
    assert "request_id=" in logged[0].getMessage()


def test_a_save_time_race_replays_the_winning_state(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A concurrent request may win the write between this call's read and its own save; the
    reply then comes from the winner's state, not from what this call itself computed."""
    winner = DialogueState(
        session_id=_SESSION_ID,
        version=2,
        lang="es",
        phase=ConversationPhase.HANDED_OFF,
        last_turn_id="turn-0001",
        last_ticket_ref="T-9999",
        updated_at=_NOW,
    )

    @dataclass
    class RaceStore:
        def get(self, session_id: str) -> DialogueState | None:
            return None

        def save(self, state: DialogueState, *, expected_version: int, turn_id: str, now: object):  # type: ignore[no-untyped-def]
            raise DuplicateTurn(winner)

    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=RaceStore(),  # type: ignore[arg-type]
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.handoff_ticket == "T-9999"


def test_a_repeated_turn_id_after_a_terminal_reply_recomputes_safely(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """Farewell, ineligible and cancelled outcomes carry no case number, ticket or pending slot
    to replay from directly; recomputing them is safe since none can file or hand off again."""
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.FAREWELL),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    first = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    controller, _ = _controller(
        _plain(NluIntent.FAREWELL),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    replay = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert replay.reply == first.reply
    assert replay.end_session


def test_confirmation_without_a_pending_confirmation_falls_back(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.YES),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.next_expected is None
    assert not response.end_session


def test_choice_and_correction_fall_back_like_unclear(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.CHOICE, choice=1),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    choice_response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert choice_response.reply

    controller, _ = _controller(
        _plain(NluIntent.CORRECTION),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    correction_response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert correction_response.reply


def test_a_non_duplicate_creation_refusal_hands_off(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(
            created=False, refusal=ToolRefusalCode.SESSION_CAP_REACHED
        ),
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0002"), principal=_principal())
    controller, _ = _controller(
        _confirmation(ConfirmationAnswer.YES),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0003"), principal=_principal())
    assert response.end_session
    assert outbox.packets[0].trigger.value == "tool_failure"


def test_get_transaction_failure_while_presenting_confirmation_hands_off(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        get_transaction_result=ToolFailure(tool=ToolName.GET_TRANSACTION, cause="error"),
    )
    controller, _ = _controller(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0002"), principal=_principal())
    assert response.end_session
    assert outbox.packets[0].trigger.value == "tool_failure"


def test_list_dispute_cases_failure_hands_off(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    controller, _ = _controller(
        _plain(NluIntent.DISPUTE_STATUS),
        store=store,
        tool_port=FakeToolPort(
            list_cases_result=ToolFailure(tool=ToolName.LIST_DISPUTE_CASES, cause="error")
        ),
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    assert response.end_session
    assert outbox.packets[0].trigger.value == "tool_failure"


# -----------------------------------------------------------------------------
# An unreachable understanding dependency (E9): never the customer's own ambiguity
# -----------------------------------------------------------------------------


def test_an_unreachable_understanding_dependency_hands_off_on_a_fresh_session(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    outbox = FakeHandoffOutbox()
    controller = DialogueController(
        UnavailableNlu(),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
    )

    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert response.end_session
    assert len(outbox.packets) == 1
    assert outbox.packets[0].trigger.value == "tool_failure"


def test_an_unreachable_understanding_dependency_never_spends_the_clarification_budget(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The escalation bypasses ``_advance`` entirely: a session already partway through
    clarification keeps its exact attempt count, proving the outage was never charged to the
    customer's own ambiguity budget."""
    store = InMemoryDialogueStore()
    store.save(
        DialogueState(
            session_id=_SESSION_ID,
            lang="es",
            phase=ConversationPhase.CLARIFYING,
            pending_slot=Slot.TRANSACTION,
            clarification_attempts=1,
            updated_at=_NOW,
        ),
        expected_version=0,
        turn_id="turn-0000",
        now=_NOW,
    )
    outbox = FakeHandoffOutbox()
    controller = DialogueController(
        UnavailableNlu(),
        store=store,
        tool_port=FakeToolPort(),
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
    )

    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    saved = store.get(_SESSION_ID)
    assert saved is not None
    assert saved.clarification_attempts == 1
    assert saved.phase is ConversationPhase.HANDED_OFF


def test_replaying_a_turn_whose_recompute_hits_an_unreachable_dependency_degrades_safely(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A replay that needs to recompute (nothing cached) but cannot reach the understanding
    dependency renders a generic acknowledgment, touching neither persisted state nor the outbox —
    a retried replay is free to try recomputing again once the outage clears."""
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    original, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=outbox,
        retriever=retriever,
    )
    original.handle_turn(_turn("turn-0001"), principal=_principal())
    before = store.get(_SESSION_ID)
    assert before is not None

    replay_controller = DialogueController(
        UnavailableNlu(),
        store=store,
        tool_port=FakeToolPort(),
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
    )

    response = replay_controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert response.end_session
    assert outbox.packets == []
    assert store.get(_SESSION_ID) == before
