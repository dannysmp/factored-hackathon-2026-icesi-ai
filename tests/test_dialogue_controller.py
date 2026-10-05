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
from collections.abc import Callable
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
from app.conversation.handoff import HandoffContent, build_packet
from app.conversation.llm_understanding import LlmNlu
from app.conversation.state import ConversationPhase, DialogueState
from app.conversation.store import Conflict, DuplicateTurn, InMemoryDialogueStore
from app.conversation.understanding import TurnAccounting, UnderstandingUnavailable
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
from app.llm.client import FakeLlm
from app.retrieval.lexical import LexicalRetriever
from app.security.errors import ErrorCode, ProblemError
from app.security.sessions import Principal
from contracts.service_v1.api import MAX_TEXT_LENGTH, TurnRequest, TurnResponse
from contracts.service_v1.cases import AmountProvenance, CaseRecord, CaseStatus, DisclosedAmount
from contracts.service_v1.cases import Money as CaseMoney
from contracts.service_v1.console import TimelineEntry
from contracts.service_v1.envelope import (
    CUSTOMER_REASON_OF,
    CustomerReason,
    DateSource,
    Intent,
    Lang,
    Slot,
)
from contracts.service_v1.handoff import HandoffPacket, HandoffTrigger
from contracts.service_v1.nlu import ConfirmationAnswer, NluIntent, NluResult, TransactionHint
from contracts.service_v1.tools import (
    CreateDisputeCaseResult,
    EvaluateDisputeRequest,
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
    original: tuple[Decimal, str] | None = None,
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
        original_amount=None
        if original is None
        else CaseMoney(amount=original[0], currency=original[1]),
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
    """Returns the same, pre-built understanding for every message this turn.

    ``accounting`` defaults to ``None`` (``FakeNlu``'s own behavior, and what nearly every test
    here wants); a test proving the controller's own accounting-logging behavior sets it.
    """

    result: NluResult
    accounting: TurnAccounting | None = None
    calls: list[tuple[str, str | None, date]] = field(default_factory=list)

    def understand(
        self, text: str, *, language_hint: str | None, reference_date: date
    ) -> tuple[NluResult, TurnAccounting | None]:
        self.calls.append((text, language_hint, reference_date))
        return self.result, self.accounting


@dataclass
class UnavailableNlu:
    """An ``Understanding`` whose own dependency is never reachable."""

    def understand(
        self, text: str, *, language_hint: str | None, reference_date: date
    ) -> tuple[NluResult, TurnAccounting | None]:
        raise UnderstandingUnavailable("the provider could not be reached")


_UNSET = object()  # A distinct sentinel from a deliberately-returned None override.


@dataclass
class FakeToolPort:
    """A hand-rolled ``ToolPort``: canned answers, no store."""

    transactions: tuple[TransactionFact, ...] = ()
    cases: tuple[CaseRecord, ...] = ()
    evaluate_result: object = _UNSET
    create_result: CreateDisputeCaseResult | ToolFailure | None = None
    list_transactions_result: TransactionPage | ToolFailure | None = None
    list_cases_result: tuple[CaseRecord, ...] | ToolFailure | None = None
    get_case_result: object = _UNSET
    get_transaction_result: object = _UNSET
    create_calls: int = 0
    evaluate_requests: list[EvaluateDisputeRequest] = field(default_factory=list)

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

    def evaluate_dispute(
        self, request: EvaluateDisputeRequest
    ) -> PolicyDecision | ToolFailure | None:
        assert self.evaluate_result is not _UNSET, "evaluate_result was never configured"
        self.evaluate_requests.append(request)
        return self.evaluate_result  # type: ignore[return-value]

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
            open_questions=content.open_questions,
        )
        self.packets.append(packet)
        return packet


@dataclass
class FakeDialogueTurnLog:
    """Records every entry it's given; ``fail`` proves a store failure never reaches the reply."""

    fail: bool = False
    entries: list[tuple[TimelineEntry, str]] = field(default_factory=list)

    def record(self, entry: TimelineEntry, *, session_id: str) -> None:
        if self.fail:
            raise psycopg.OperationalError("turn log unreachable")
        self.entries.append((entry, session_id))


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
    accounting: TurnAccounting | None = None,
    turn_log: FakeDialogueTurnLog | None = None,
    max_turns: int = 30,
) -> tuple[DialogueController, ScriptedNlu]:
    nlu = ScriptedNlu(result, accounting)
    controller = DialogueController(
        nlu,
        store=store,
        tool_port=tool_port,
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=max_turns,
        turn_log=turn_log,
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


def test_matches_hint_ignores_case_in_an_accented_name_typed_with_its_accent() -> None:
    fact = _transaction(merchant="Café Colombia")
    assert _matches_hint(fact, TransactionHint(merchant="café"))
    assert _matches_hint(fact, TransactionHint(merchant="CAFÉ"))
    assert _matches_hint(fact, TransactionHint(merchant="colombia"))


@pytest.mark.parametrize(
    ("stored", "typed"),
    [
        ("Café Sol", "cafe"),
        ("Café Sol", "Cafe Sol"),
        ("Cafe Sol", "Café"),
        ("São Paulo", "SAO PAULO"),
        ("SAO PAULO", "são paulo"),
        ("Pão de Açúcar", "pao de acucar"),
        ("Señor Taco", "senor"),
        ("Café", " Café "),
        ("Café", "Café "),
    ],
)
def test_matches_hint_ignores_accents_whichever_side_carries_them(stored: str, typed: str) -> None:
    """A customer may drop an accent the stored name has, or add one it lacks."""
    assert _matches_hint(_transaction(merchant=stored), TransactionHint(merchant=typed))


@pytest.mark.parametrize("typed", ["\u0301", " ", "\u0301 \u0301"])
def test_matches_hint_rejects_a_merchant_that_folds_to_nothing(typed: str) -> None:
    """An accent mark or blank alone names no merchant, so it must not match every transaction."""
    assert not _matches_hint(_transaction(merchant="Café Sol"), TransactionHint(merchant=typed))


def test_matches_hint_still_rejects_a_different_merchant_after_accent_folding() -> None:
    """Folding accents must not make unrelated names compare equal."""
    assert not _matches_hint(
        _transaction(merchant="Café Sol"), TransactionHint(merchant="Sol Luna")
    )


def test_matches_hint_never_matches_an_unknown_amount_against_a_stated_one() -> None:
    fact = _transaction(amount=None)
    assert not _matches_hint(fact, TransactionHint(amount=Decimal("10.00")))


@pytest.mark.parametrize(
    ("original", "quoted"),
    [
        (
            (Decimal("1914215.00"), "COP"),
            TransactionHint(amount=Decimal("1914215.00"), currency="COP"),
        ),
        ((Decimal("1914215.00"), "COP"), TransactionHint(amount=Decimal("1914215.00"))),
        (
            (Decimal("3499939.11"), "ARS"),
            TransactionHint(amount=Decimal("3499939.11"), currency="ARS"),
        ),
        (
            (Decimal("250000.00"), "CLP"),
            TransactionHint(amount=Decimal("250000.00"), currency="CLP"),
        ),
        ((Decimal("250000.00"), "CLP"), TransactionHint(currency="CLP")),
    ],
    ids=["cop", "cop-no-currency", "ars", "clp", "clp-currency-only"],
)
def test_matches_hint_reads_a_figure_quoted_in_the_currency_of_the_transaction(
    original: tuple[Decimal, str], quoted: TransactionHint
) -> None:
    """The amount in dollars is no help to a customer who quotes pesos."""
    with_dollars = _transaction(amount=Decimal("470.20"), currency="USD", original=original)
    without_dollars = _transaction(amount=None, original=original)

    assert _matches_hint(with_dollars, quoted)
    assert _matches_hint(without_dollars, quoted)


def test_matches_hint_still_reads_the_amount_in_dollars_of_a_transaction_made_in_pesos() -> None:
    fact = _transaction(
        amount=Decimal("470.20"), currency="USD", original=(Decimal("1914215.00"), "COP")
    )

    assert _matches_hint(fact, TransactionHint(amount=Decimal("470.20"), currency="USD"))
    assert _matches_hint(fact, TransactionHint(amount=Decimal("470.20")))


@pytest.mark.parametrize(
    "quoted",
    [
        TransactionHint(amount=Decimal("1914215.01"), currency="COP"),
        TransactionHint(amount=Decimal("1914215.00"), currency="ARS"),
        TransactionHint(amount=Decimal("470.20"), currency="COP"),
        TransactionHint(amount=Decimal("1914215.00"), currency="USD"),
    ],
    ids=["other-amount", "other-currency", "dollars-in-pesos", "pesos-in-dollars"],
)
def test_matches_hint_pairs_the_amount_with_the_currency_of_the_same_figure(
    quoted: TransactionHint,
) -> None:
    fact = _transaction(
        amount=Decimal("470.20"), currency="USD", original=(Decimal("1914215.00"), "COP")
    )

    assert not _matches_hint(fact, quoted)


@pytest.mark.parametrize("language", ["es", "pt", "en"])
@pytest.mark.parametrize(
    "quoted",
    [
        TransactionHint(amount=Decimal("1914215.00"), currency="COP"),
        TransactionHint(amount=Decimal("1914215.00")),
    ],
    ids=["with-currency", "amount-only"],
)
def test_a_figure_quoted_in_pesos_finds_a_transaction_that_has_no_dollar_amount(
    language: Lang, quoted: TransactionHint, policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    controller = _two_transaction_controller(
        [
            NluResult(
                intent=NluIntent.FILE_DISPUTE,
                confidence=0.8,
                language=language,
                transaction=quoted,
            )
        ],
        store=store,
        policy=policy,
        retriever=retriever,
        port=FakeToolPort(
            transactions=(
                _transaction("TX-1", merchant="Amazon", amount=Decimal("100.00")),
                _transaction(
                    "TX-2",
                    merchant=None,
                    amount=None,
                    occurred_on=date(2026, 6, 10),
                    last4="9876",
                    original=(Decimal("1914215.00"), "COP"),
                ),
            )
        ),
    )

    controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal())

    state = store.get(_SESSION_ID)
    assert state is not None
    assert (state.selected_ref, state.pending_slot) == ("TX-2", Slot.TRANSACTION_CHOICE)


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


@dataclass
class SequencedNlu:
    """Returns the next pre-built understanding on each call, one per customer message."""

    results: list[NluResult]
    calls: int = 0

    def understand(
        self, text: str, *, language_hint: str | None, reference_date: date
    ) -> tuple[NluResult, TurnAccounting | None]:
        result = self.results[self.calls]
        self.calls += 1
        return result, None


def _sequenced_controller(
    results: list[NluResult],
    *,
    store: InMemoryDialogueStore,
    policy: Policy,
    retriever: LexicalRetriever,
) -> DialogueController:
    return DialogueController(
        SequencedNlu(results),
        store=store,
        tool_port=FakeToolPort(),
        retriever=retriever,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
    )


def _english_dispute_opening() -> list[NluResult]:
    """What the model returns when the web client opens with "Hello" and the customer then writes
    in English: no language for the opener, English for the first substantive message."""
    return [
        _plain(NluIntent.UNCLEAR, language=None),
        _plain(NluIntent.FILE_DISPUTE, language="en"),
        _plain(NluIntent.FILE_DISPUTE, language="es"),
    ]


def test_the_language_offer_names_english_too(policy: Policy, retriever: LexicalRetriever) -> None:
    """The opener's offer tells an English customer that English is available."""
    controller = _sequenced_controller(
        _english_dispute_opening(),
        store=InMemoryDialogueStore(),
        policy=policy,
        retriever=retriever,
    )

    offer = controller.handle_turn(_turn("turn-0001", "Hello"), principal=_principal())

    assert "let me know if you prefer English" in offer.reply


def test_an_opener_without_a_language_signal_does_not_fix_the_language(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A "Hello" the model cannot place in a language leaves the language open, so the first
    English message is answered in English."""
    controller = _sequenced_controller(
        _english_dispute_opening(),
        store=InMemoryDialogueStore(),
        policy=policy,
        retriever=retriever,
    )

    controller.handle_turn(_turn("turn-0001", "Hello"), principal=_principal())
    first_message = controller.handle_turn(
        _turn("turn-0002", "Hi, there's a charge on my card I don't recognize."),
        principal=_principal(),
    )

    assert first_message.lang == "en"
    assert first_message.reply.startswith("Could you tell me the merchant")


def test_once_the_dispute_is_under_way_one_message_in_another_language_does_not_switch(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """After a dispute step has been taken the language is fixed for the conversation."""
    controller = _sequenced_controller(
        _english_dispute_opening(),
        store=InMemoryDialogueStore(),
        policy=policy,
        retriever=retriever,
    )

    controller.handle_turn(_turn("turn-0001", "Hello"), principal=_principal())
    controller.handle_turn(
        _turn("turn-0002", "A charge I don't recognize."), principal=_principal()
    )
    stray = controller.handle_turn(_turn("turn-0003", "no sé"), principal=_principal())

    assert stray.lang == "en"


def _opened_state(**changes: object) -> DialogueState:
    values: dict[str, object] = {
        "session_id": _SESSION_ID,
        "lang": "es",
        "last_turn_id": "turn-0001",
        "updated_at": _NOW,
    }
    return DialogueState(**{**values, **changes})


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"last_turn_id": None}, False),
        ({}, True),
        ({"pending_slot": Slot.REASON}, False),
        ({"category": DisputeCategory.UNRECOGNIZED_CHARGE}, False),
        ({"selected_ref": "TRX-1"}, False),
        ({"phase": ConversationPhase.CLARIFYING}, False),
        ({"phase": ConversationPhase.CONFIRMING}, False),
        ({"phase": ConversationPhase.CLOSED}, False),
        ({"phase": ConversationPhase.HANDED_OFF}, False),
        ({"phase": ConversationPhase.ABANDONED}, False),
    ],
)
def test_a_conversation_is_opening_only_before_any_dispute_step(
    changes: dict[str, object], expected: bool
) -> None:
    """Each condition is the only thing that keeps its case from reading as an opening."""
    assert _opened_state(**changes).is_opening is expected


def test_a_handed_off_conversation_keeps_its_language(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A fraud report hands off with no category, slot or reference set: only the phase keeps
    the conversation from reading as an opening."""
    controller = _sequenced_controller(
        [
            _plain(NluIntent.REPORT_FRAUD, language="es"),
            _plain(NluIntent.SMALL_TALK, language="en"),
        ],
        store=InMemoryDialogueStore(),
        policy=policy,
        retriever=retriever,
    )

    handed_off = controller.handle_turn(_turn("turn-0001"), principal=_principal())
    after = controller.handle_turn(_turn("turn-0002", "thanks"), principal=_principal())

    assert handed_off.handoff_ticket is not None
    assert after.lang == "es"


@pytest.mark.parametrize("intent", [NluIntent.UNCLEAR, NluIntent.SWITCH_LANGUAGE])
def test_a_message_that_says_nothing_reliable_about_the_language_does_not_switch_it(
    policy: Policy, retriever: LexicalRetriever, intent: NluIntent
) -> None:
    """An unclear message or an explicit language request is no evidence of the language."""
    extra = {"requested_language": "es"} if intent is NluIntent.SWITCH_LANGUAGE else {}
    controller = _sequenced_controller(
        [_plain(NluIntent.UNCLEAR, language=None), _plain(intent, language="en", **extra)],
        store=InMemoryDialogueStore(),
        policy=policy,
        retriever=retriever,
    )

    controller.handle_turn(_turn("turn-0001", "Hello"), principal=_principal())
    second = controller.handle_turn(_turn("turn-0002", "Netflix 15.99"), principal=_principal())

    assert second.lang == "es"


def test_the_controller_passes_its_own_domain_date_as_the_understanding_reference_date() -> None:
    """A customer-stated transaction date is resolved against the domain calendar's own
    reference date, never the wall clock — proven by reading back exactly what the controller
    itself passed into ``understand``, not by trusting it silently matches."""
    store = InMemoryDialogueStore()
    controller, nlu = _controller(
        _plain(NluIntent.UNCLEAR, language=None),
        store=store,
        tool_port=FakeToolPort(),
        policy=load_policy(),
        outbox=FakeHandoffOutbox(),
        retriever=LexicalRetriever.from_corpus(),
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert nlu.calls[-1][2] == _DOMAIN_DATE


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
    assert response.next_expected is Slot.TRANSACTION
    assert store.get(_SESSION_ID).selected_ref is None  # type: ignore[union-attr]


_NOBODY = TransactionHint(merchant="Nobody")


def test_a_description_that_matches_nothing_is_asked_again_twice_before_a_person_is_involved(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)

    first = dialogue.say(_file_dispute(transaction=_NOBODY))
    assert first.next_expected is Slot.TRANSACTION
    assert not first.end_session

    second = dialogue.say(_file_dispute(transaction=_NOBODY))
    assert second.next_expected is Slot.TRANSACTION
    assert not second.end_session
    assert dialogue.outbox.packets == []

    third = dialogue.say(_file_dispute(transaction=_NOBODY))
    assert third.end_session
    assert [packet.trigger.value for packet in dialogue.outbox.packets] == ["low_understanding"]


@pytest.mark.parametrize(
    "answer",
    [
        _plain(NluIntent.CORRECTION, transaction=_NOBODY),
        _plain(NluIntent.UNCLEAR, transaction=_NOBODY),
        _plain(NluIntent.CHOICE, choice=1, transaction=_NOBODY),
    ],
    ids=["correction", "unclear", "choice"],
)
def test_a_described_answer_that_matches_nothing_counts_whatever_the_intent_the_model_gave(
    policy: Policy, retriever: LexicalRetriever, answer: NluResult
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(_file_dispute())

    assert not dialogue.say(answer).end_session
    assert dialogue.outbox.packets == []

    assert dialogue.say(answer).end_session
    assert [packet.trigger.value for packet in dialogue.outbox.packets] == ["low_understanding"]


def test_an_empty_transaction_list_never_counts_toward_the_budget(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, port=FakeToolPort(transactions=()))

    for _ in range(4):
        reply = dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
        assert not reply.end_session

    assert dialogue.outbox.packets == []
    state = dialogue.store.get("sess-1")
    assert state is not None
    assert state.clarification_attempts == 0


def test_each_turn_logs_how_it_was_understood_and_where_the_dialogue_went(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    dialogue = _Dialogue(policy, retriever)

    with caplog.at_level(logging.INFO, logger="app.conversation.controller"):
        dialogue.say(_file_dispute())
        dialogue.say(_plain(NluIntent.UNCLEAR, transaction=_NOBODY))
        dialogue.say(_plain(NluIntent.UNCLEAR, transaction=_NOBODY))

    decided = [r.getMessage() for r in caplog.records if r.getMessage().startswith("turn_decided")]
    assert len(decided) == 3
    assert "understood=file_dispute" in decided[0]
    assert "has_hint=False" in decided[0]
    assert "slot_after=transaction attempts_after=0" in decided[0]
    assert "understood=unclear" in decided[1]
    assert "has_hint=True" in decided[1]
    assert "slot_before=transaction attempts_before=0" in decided[1]
    assert "attempts_after=1" in decided[1]
    assert "handoff_reason=None" in decided[1]
    assert "reply=handoff" in decided[2]
    assert "template=handoff_review" in decided[2]
    assert "handoff_reason=escalate_low_nlu_confidence" in decided[2]


def test_the_turn_decision_log_never_carries_the_customers_description(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    dialogue = _Dialogue(policy, retriever)
    described = TransactionHint(
        merchant="Nobody",
        amount=Decimal("1914215.00"),
        currency="COP",
        date_on=date(2026, 6, 3),
        date_source=DateSource.ABSOLUTE,
        product_last4="4417",
    )

    with caplog.at_level(logging.INFO, logger="app.conversation.controller"):
        dialogue.say(_file_dispute(transaction=described))

    assert any(r.getMessage().startswith("turn_decided") for r in caplog.records)
    everything = " ".join(r.getMessage() for r in caplog.records)
    for detail in ("Nobody", "1914215", "2026-06-03", "4417"):
        assert detail not in everything


def test_a_replayed_turn_does_not_log_a_second_decision(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(_file_dispute(), turn_id="turn-once")
    caplog.clear()

    with caplog.at_level(logging.INFO, logger="app.conversation.controller"):
        dialogue.say(_file_dispute(), turn_id="turn-once")

    assert not [r for r in caplog.records if r.getMessage().startswith("turn_decided")]


def test_a_described_transaction_that_finds_none_counts_toward_the_budget(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, port=FakeToolPort(transactions=()))

    assert not dialogue.say(_file_dispute(transaction=_NOBODY)).end_session
    assert not dialogue.say(_file_dispute(transaction=_NOBODY)).end_session
    assert dialogue.outbox.packets == []

    assert dialogue.say(_file_dispute(transaction=_NOBODY)).end_session
    assert [packet.trigger.value for packet in dialogue.outbox.packets] == ["low_understanding"]
    assert _unanswered_in(dialogue) == [(Slot.TRANSACTION, 2)]


def test_after_a_description_that_matches_nothing_the_transaction_stays_the_open_question(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(_file_dispute(transaction=_NOBODY))

    again = dialogue.say(_plain(NluIntent.UNCLEAR))
    assert again.next_expected is Slot.TRANSACTION
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.clarification_attempts == 1

    presented = dialogue.present_amazon()
    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.clarification_attempts == 0


def test_multiple_matches_ask_for_detail_twice_then_escalate(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The customer is asked to narrow the search the policy's budget number of times; the answer
    to the last of those that still matches several transactions is what sends the request on."""
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(transactions=(_transaction("TX-1"), _transaction("TX-2")))
    responses = []

    for i in range(1, policy.routing.clarification_budget + 2):
        controller, _ = _controller(
            _file_dispute(transaction=TransactionHint(merchant="Amazon")),
            store=store,
            tool_port=port,
            policy=policy,
            outbox=outbox,
            retriever=retriever,
        )
        responses.append(controller.handle_turn(_turn(f"turn-{i:04d}"), principal=_principal()))

    assert [response.end_session for response in responses] == [False, False, True]
    assert all(response.next_expected is Slot.TRANSACTION for response in responses[:-1])
    assert len(outbox.packets) == 1
    assert outbox.packets[0].trigger.value == "low_understanding"


def test_a_localized_amount_in_a_portuguese_report_still_presents_the_one_matching_transaction(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The customer's first message names no transaction; the second names the merchant, date and
    "99.948,89 ARS". The model reports the figure as spoken, which must not cost the turn: the one
    matching transaction is presented rather than the conversation escalating."""
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        transactions=(
            _transaction(
                merchant="Farmacia Salud",
                occurred_on=date(2026, 4, 21),
                amount=Decimal("99948.89"),
                currency="ARS",
            ),
        )
    )
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.9,
                "language": "pt",
                "mentions_second_dispute": False,
            },
            {
                "intent": "file_dispute",
                "confidence": 0.9,
                "language": "pt",
                "merchant": "Farmacia Salud",
                "date_expression": "21 de abril",
                "amount": "99.948,89",
                "currency": "ARS",
                "mentions_second_dispute": False,
            },
        ]
    )
    controller = DialogueController(
        LlmNlu(llm, model="claude-haiku-4-5-20251001"),
        store=store,
        tool_port=port,
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
    )

    asked = controller.handle_turn(
        _turn("turn-0001", "Quero contestar uma compra"), principal=_principal()
    )
    assert asked.next_expected is Slot.TRANSACTION

    presented = controller.handle_turn(
        _turn("turn-0002", "Foi na Farmacia Salud, 21 de abril, 99.948,89 ARS"),
        principal=_principal(),
    )

    assert not presented.end_session
    assert outbox.packets == []
    state = store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-1"


@pytest.mark.parametrize(
    ("language", "first_message", "second_message", "model_reading", "target"),
    [
        (
            "en",
            "Hi, there's a charge on my card I don't recognize.",
            "It was at Cine Premium on June 3rd, about 1,914,215 COP.",
            {
                "merchant": "Cine Premium",
                "date_expression": "June 3rd",
                "amount": "1,914,215",
                "currency": "COP",
            },
            ("Cine Premium", date(2026, 6, 3), Decimal("1914215"), "COP"),
        ),
        (
            "pt",
            "Quero contestar uma compra",
            "Foi na Farmacia Salud, no dia 21 de abril, cerca de 99.948,89 ARS.",
            {
                "merchant": "Farmacia Salud",
                "date_expression": "dia 21 de abril",
                "amount": "99.948,89",
                "currency": "ARS",
            },
            ("Farmacia Salud", date(2026, 4, 21), Decimal("99948.89"), "ARS"),
        ),
    ],
)
def test_a_month_name_date_finds_a_transaction_older_than_the_five_most_recent(
    policy: Policy,
    retriever: LexicalRetriever,
    *,
    language: str,
    first_message: str,
    second_message: str,
    model_reading: dict[str, str],
    target: tuple[str, date, Decimal, str],
) -> None:
    """The listing returns only the five newest transactions, so a date the customer gave in words
    ("June 3rd", "21 de abril") must narrow the search or an older transaction is reported as not
    found. The customer's second message names the merchant, month-and-day and amount; the one
    transaction matching all three is presented."""
    merchant, occurred_on, amount, currency = target
    recent = tuple(
        _transaction(
            f"TX-{index}",
            merchant=f"Recent shop {index}",
            occurred_on=date(2026, 6, 17 - index),
            amount=Decimal("10.00") + index,
        )
        for index in range(1, 9)
    )
    wanted = _transaction(
        "TX-WANTED", merchant=merchant, occurred_on=occurred_on, amount=amount, currency=currency
    )
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(transactions=(*recent, wanted))
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.9,
                "language": language,
                "mentions_second_dispute": False,
            },
            {
                "intent": "file_dispute",
                "confidence": 0.9,
                "language": language,
                "mentions_second_dispute": False,
                **model_reading,
            },
        ]
    )
    controller = DialogueController(
        LlmNlu(llm, model="claude-haiku-4-5-20251001"),
        store=store,
        tool_port=port,
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
    )

    asked = controller.handle_turn(_turn("turn-0001", first_message), principal=_principal())
    assert asked.next_expected is Slot.TRANSACTION

    presented = controller.handle_turn(_turn("turn-0002", second_message), principal=_principal())

    assert not presented.end_session
    assert outbox.packets == []
    state = store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-WANTED"


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


class _Dialogue:
    """Drives a session turn by turn through the real controller, one scripted result each."""

    def __init__(
        self, policy: Policy, retriever: LexicalRetriever, port: FakeToolPort | None = None
    ) -> None:
        self.policy = policy
        self.retriever = retriever
        self.store = InMemoryDialogueStore()
        self.outbox = FakeHandoffOutbox()
        self.port = port or FakeToolPort(transactions=(_transaction(),))
        self.turns = 0
        self.understood: list[str] = []
        self.language: str | None = None

    def say(
        self, result: NluResult, *, turn_id: str | None = None, text: str = "hola"
    ) -> TurnResponse:
        self.turns += 1
        if self.language is not None:
            result = result.model_copy(update={"language": self.language})
        controller, nlu = _controller(
            result,
            store=self.store,
            tool_port=self.port,
            policy=self.policy,
            outbox=self.outbox,
            retriever=self.retriever,
        )
        response = controller.handle_turn(
            _turn(turn_id or f"turn-{self.turns:04d}", text), principal=_principal()
        )
        self.understood.extend(call[0] for call in nlu.calls)
        return response

    def present_amazon(self) -> TurnResponse:
        return self.say(_file_dispute(transaction=TransactionHint(merchant="Amazon")))


def _filed_dialogue(
    policy: Policy, retriever: LexicalRetriever, language: str | None = None
) -> _Dialogue:
    port = FakeToolPort(
        transactions=(_transaction(),),
        cases=(_case(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.language = language
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    filed = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert "D-1" in filed.reply
    assert port.create_calls == 1
    return dialogue


@pytest.mark.parametrize(
    "message",
    [
        _confirmation(ConfirmationAnswer.YES),
        _confirmation(ConfirmationAnswer.NO),
        _confirmation(ConfirmationAnswer.AMBIGUOUS),
        _plain(NluIntent.UNCLEAR),
        _plain(NluIntent.SMALL_TALK),
        _plain(NluIntent.CORRECTION),
    ],
    ids=["yes", "no", "ambiguous", "unclear", "small-talk", "correction"],
)
def test_a_message_after_a_case_is_filed_never_reopens_the_filing_question(
    policy: Policy, retriever: LexicalRetriever, message: NluResult
) -> None:
    dialogue = _filed_dialogue(policy, retriever)

    reply = dialogue.say(message)

    assert reply.next_expected is None
    assert "Confirma" not in reply.reply
    assert dialogue.port.create_calls == 1
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.phase is ConversationPhase.CLOSED
    assert state.pending_slot is None
    assert dialogue.outbox.packets == []


def test_a_new_dispute_after_a_case_is_filed_starts_from_its_own_transaction(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _filed_dialogue(policy, retriever)

    reply = dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))

    assert reply.next_expected is Slot.TRANSACTION
    assert "Confirma" not in reply.reply
    assert dialogue.port.create_calls == 1


def test_a_new_dispute_with_another_reason_after_a_case_is_filed_is_evaluated_under_that_reason(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _filed_dialogue(policy, retriever)

    presented = dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.DUPLICATE_CHARGE,
        )
    )
    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    confirm = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert confirm.next_expected is Slot.CONFIRMATION
    assert "cargo duplicado" in confirm.reply
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.category is DisputeCategory.DUPLICATE_CHARGE
    assert dialogue.port.create_calls == 1


def test_starting_over_for_the_filed_transaction_is_refused_by_the_policy_not_filed_again(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _filed_dialogue(policy, retriever)
    dialogue.port.evaluate_result = _decision(Outcome.INELIGIBLE, ReasonCode.DUPLICATE_OPEN_CASE)

    presented = dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    refused = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert refused.next_expected is None
    assert "Ya existe una disputa abierta" in refused.reply
    assert dialogue.port.create_calls == 1


def _assert_dispute_cleared(dialogue: _Dialogue) -> None:
    """The dispute's pending question, selection and reason are gone and the phase is closed."""
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.phase is ConversationPhase.CLOSED
    assert state.pending_slot is None
    assert state.clarification_attempts == 0
    assert state.selected_ref is None
    assert state.category is None


def test_a_cancelled_filing_clears_the_selected_transaction_and_reason(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    dialogue.say(_confirmation(ConfirmationAnswer.YES))

    dialogue.say(_confirmation(ConfirmationAnswer.NO))

    _assert_dispute_cleared(dialogue)
    assert port.create_calls == 0


def test_an_ineligible_decision_clears_the_selected_transaction_and_reason(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(Outcome.INELIGIBLE, ReasonCode.FILING_WINDOW_EXPIRED),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )

    dialogue.say(_confirmation(ConfirmationAnswer.YES))

    _assert_dispute_cleared(dialogue)


def test_a_duplicate_open_case_refusal_clears_the_selected_transaction_and_reason(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(
            created=False, refusal=ToolRefusalCode.DUPLICATE_OPEN_CASE, existing_case_number="D-9"
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    dialogue.say(_confirmation(ConfirmationAnswer.YES))

    dialogue.say(_confirmation(ConfirmationAnswer.YES))

    _assert_dispute_cleared(dialogue)


def _dialogue_closed_without_a_case(
    policy: Policy, retriever: LexicalRetriever, closing: str
) -> _Dialogue:
    """A dialogue whose dispute ended by ``closing``: cancelled, ineligible or duplicate."""
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=(
            _decision(Outcome.INELIGIBLE, ReasonCode.FILING_WINDOW_EXPIRED)
            if closing == "ineligible"
            else _decision(Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True)
        ),
        create_result=(
            CreateDisputeCaseResult(
                created=False,
                refusal=ToolRefusalCode.DUPLICATE_OPEN_CASE,
                existing_case_number="D-9",
            )
            if closing == "duplicate"
            else None
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    if closing == "cancelled":
        dialogue.say(_confirmation(ConfirmationAnswer.YES))
        dialogue.say(_confirmation(ConfirmationAnswer.NO))
    elif closing == "duplicate":
        dialogue.say(_confirmation(ConfirmationAnswer.YES))
        dialogue.say(_confirmation(ConfirmationAnswer.YES))
    else:
        dialogue.say(_confirmation(ConfirmationAnswer.YES))
    return dialogue


_CLOSINGS_WITHOUT_A_CASE = ["cancelled", "ineligible", "duplicate"]


@pytest.mark.parametrize("closing", _CLOSINGS_WITHOUT_A_CASE)
def test_a_new_dispute_after_one_ended_asks_for_its_own_transaction(
    policy: Policy, retriever: LexicalRetriever, closing: str
) -> None:
    """The dispute that ended is not presented again: a new request starts from its transaction."""
    dialogue = _dialogue_closed_without_a_case(policy, retriever, closing)
    filed_before = dialogue.port.create_calls

    reply = dialogue.say(_file_dispute())

    assert reply.next_expected is Slot.TRANSACTION
    assert dialogue.port.create_calls == filed_before


@pytest.mark.parametrize("closing", _CLOSINGS_WITHOUT_A_CASE)
def test_a_policy_question_after_a_dispute_ended_is_declined_when_its_figure_needs_a_reason(
    policy: Policy, retriever: LexicalRetriever, closing: str
) -> None:
    """With the reason cleared, a figure that depends on one is declined with an advisor offered."""
    dialogue = _dialogue_closed_without_a_case(policy, retriever, closing)

    reply = dialogue.say(
        _plain(NluIntent.POLICY_QUESTION, policy_query="cuanto tiempo tienen para responder")
    )

    assert "No tengo esa información" in reply.reply
    assert "asesor" in reply.reply


@pytest.mark.parametrize("closing", _CLOSINGS_WITHOUT_A_CASE)
def test_a_person_requested_after_a_dispute_ended_is_replayed_without_that_dispute_reason(
    policy: Policy, retriever: LexicalRetriever, closing: str
) -> None:
    """The ticket reply names the reason only when the conversation still holds one."""
    dialogue = _dialogue_closed_without_a_case(policy, retriever, closing)
    dialogue.say(_plain(NluIntent.REQUEST_PERSON))
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.last_ticket_ref is not None
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=dialogue.store,
        tool_port=dialogue.port,
        policy=policy,
        outbox=dialogue.outbox,
        retriever=retriever,
    )

    assert controller._ticket_envelope(state).facts.category is None


def test_a_policy_question_after_a_case_is_filed_is_declined_when_its_figure_needs_a_reason(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _filed_dialogue(policy, retriever)

    reply = dialogue.say(
        _plain(NluIntent.POLICY_QUESTION, policy_query="cuanto tiempo tienen para responder")
    )

    assert "No tengo esa información" in reply.reply
    assert "asesor" in reply.reply
    assert dialogue.port.create_calls == 1


def test_a_retried_turn_after_a_case_is_filed_replays_the_filing_result(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _filed_dialogue(policy, retriever)
    first = dialogue.say(_plain(NluIntent.SMALL_TALK), turn_id="turn-late")
    assert "D-1" not in first.reply

    retried = dialogue.say(_plain(NluIntent.SMALL_TALK), turn_id="turn-late")

    assert "D-1" in retried.reply
    assert dialogue.port.create_calls == 1


def test_a_retried_turn_that_opened_a_second_dispute_asks_its_question_again(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _filed_dialogue(policy, retriever)
    first = dialogue.present_amazon()
    assert first.next_expected is Slot.TRANSACTION_CHOICE
    assert "D-1" not in first.reply
    turn_id = f"turn-{dialogue.turns:04d}"

    retried = dialogue.say(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")), turn_id=turn_id
    )

    assert retried.reply == first.reply
    assert retried.next_expected is Slot.TRANSACTION_CHOICE
    assert dialogue.port.create_calls == 1


@pytest.mark.parametrize("language", ["es", "pt", "en"])
def test_a_retried_turn_that_asked_for_confirmation_in_a_filed_session_asks_for_it_again(
    policy: Policy, retriever: LexicalRetriever, language: str
) -> None:
    dialogue = _filed_dialogue(policy, retriever, language)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    first = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    turn_id = f"turn-{dialogue.turns:04d}"
    assert first.next_expected is Slot.CONFIRMATION
    assert "D-1" not in first.reply

    retried = dialogue.say(_confirmation(ConfirmationAnswer.YES), turn_id=turn_id)

    assert "D-1" not in retried.reply
    assert retried.next_expected is Slot.CONFIRMATION
    assert retried.case_number is None
    assert dialogue.port.create_calls == 1


@pytest.mark.parametrize("language", ["es", "pt", "en"])
@pytest.mark.parametrize("closing", _CLOSINGS_WITHOUT_A_CASE)
def test_a_retried_turn_that_closed_a_second_dispute_does_not_report_the_first_case(
    policy: Policy, retriever: LexicalRetriever, closing: str, language: str
) -> None:
    dialogue = _filed_dialogue(policy, retriever, language)
    if closing == "ineligible":
        dialogue.port.evaluate_result = _decision(
            Outcome.INELIGIBLE, ReasonCode.FILING_WINDOW_EXPIRED
        )
    if closing == "duplicate":
        dialogue.port.create_result = CreateDisputeCaseResult(
            created=False,
            refusal=ToolRefusalCode.DUPLICATE_OPEN_CASE,
            existing_case_number="D-9",
        )
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    confirmations = {"cancelled": 2, "ineligible": 1, "duplicate": 2}[closing]
    answers = [ConfirmationAnswer.YES] * (confirmations - 1)
    answers.append(ConfirmationAnswer.NO if closing == "cancelled" else ConfirmationAnswer.YES)
    for answer in answers[:-1]:
        dialogue.say(_confirmation(answer))
    closed = dialogue.say(_confirmation(answers[-1]), turn_id="turn-close")
    assert "D-1" not in closed.reply

    retried = dialogue.say(_confirmation(answers[-1]), turn_id="turn-close")

    assert "D-1" not in retried.reply
    assert (
        retried.reply
        == {
            "es": "De acuerdo, no presenté la disputa.",
            "pt": "Tudo bem, não apresentei a contestação.",
            "en": "Understood, I didn't file the dispute.",
        }[language]
    )
    assert retried.case_number is None
    assert dialogue.port.create_calls == (2 if closing == "duplicate" else 1)


def test_the_status_of_a_filed_case_can_still_be_asked_for(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _filed_dialogue(policy, retriever)

    reply = dialogue.say(_plain(NluIntent.DISPUTE_STATUS))

    assert "D-1" in reply.reply
    assert dialogue.port.create_calls == 1


def test_yes_to_the_presented_transaction_moves_on_to_the_reason(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    presented = dialogue.present_amazon()
    assert presented.next_expected is Slot.TRANSACTION_CHOICE

    answered = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert answered.next_expected is Slot.REASON
    assert not answered.end_session
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-1"


def test_the_whole_filing_runs_through_the_customers_yes_to_the_presented_transaction(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        cases=(_case(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )
    dialogue = _Dialogue(policy, retriever, port)

    assert dialogue.say(_file_dispute()).next_expected is Slot.TRANSACTION
    assert dialogue.present_amazon().next_expected is Slot.TRANSACTION_CHOICE
    assert dialogue.say(_confirmation(ConfirmationAnswer.YES)).next_expected is Slot.REASON
    confirm = dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    assert confirm.next_expected is Slot.CONFIRMATION
    filed = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert "D-1" in filed.reply
    assert port.create_calls == 1


def test_yes_to_the_presented_transaction_evaluates_when_the_reason_is_already_known(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )

    confirm = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert confirm.next_expected is Slot.CONFIRMATION


def test_a_reason_given_instead_of_a_yes_proceeds_without_the_stale_question(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.present_amazon()

    confirm = dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    assert confirm.next_expected is Slot.CONFIRMATION


def test_no_to_the_presented_transaction_asks_for_the_right_one(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.present_amazon()

    response = dialogue.say(_confirmation(ConfirmationAnswer.NO))
    assert response.next_expected is Slot.TRANSACTION
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None


def test_an_unclear_answer_to_the_presented_transaction_asks_again_then_escalates(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.present_amazon()

    again = dialogue.say(_confirmation(ConfirmationAnswer.AMBIGUOUS))
    assert again.next_expected is Slot.TRANSACTION_CHOICE
    assert "Amazon" in again.reply

    escalated = dialogue.say(_confirmation(ConfirmationAnswer.AMBIGUOUS))
    assert escalated.end_session
    assert dialogue.outbox.packets[0].trigger.value == "low_understanding"
    assert _unanswered_in(dialogue) == [(Slot.TRANSACTION_CHOICE, 2)]


def _unanswered_in(dialogue: _Dialogue) -> list[tuple[Slot, int]]:
    """The open questions of the one packet a conversation handed over."""
    [packet] = dialogue.outbox.packets
    return [(question.slot, question.attempts) for question in packet.open_questions]


def _awaiting_filing_confirmation(
    policy: Policy, retriever: LexicalRetriever
) -> tuple[_Dialogue, TurnResponse]:
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    confirm = dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    assert confirm.next_expected is Slot.TRANSACTION_CHOICE
    asked = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert asked.next_expected is Slot.CONFIRMATION
    return dialogue, asked


@pytest.mark.parametrize("pending", ["transaction_choice", "confirmation"])
@pytest.mark.parametrize(
    "answer",
    [
        _confirmation(ConfirmationAnswer.YES_WITH_CHANGE),
        _plain(NluIntent.CORRECTION),
    ],
    ids=["yes_with_change", "correction"],
)
def test_a_change_of_mind_is_asked_about_not_repeated_back(
    policy: Policy, retriever: LexicalRetriever, pending: str, answer: NluResult
) -> None:
    if pending == "confirmation":
        dialogue, question = _awaiting_filing_confirmation(policy, retriever)
    else:
        dialogue = _Dialogue(policy, retriever)
        question = dialogue.present_amazon()

    first = dialogue.say(answer)

    assert first.reply != question.reply
    assert "transacción" in first.reply and "motivo" in first.reply
    assert first.next_expected is question.next_expected
    assert not first.end_session
    assert dialogue.outbox.packets == []


@pytest.mark.parametrize("pending", ["transaction_choice", "confirmation"])
def test_two_changes_in_a_row_hand_off_once_the_clarification_budget_is_spent(
    policy: Policy, retriever: LexicalRetriever, pending: str
) -> None:
    if pending == "confirmation":
        dialogue, _ = _awaiting_filing_confirmation(policy, retriever)
    else:
        dialogue = _Dialogue(policy, retriever)
        dialogue.present_amazon()
    change = _confirmation(ConfirmationAnswer.YES_WITH_CHANGE)

    first = dialogue.say(change)
    second = dialogue.say(change)

    assert not first.end_session
    assert second.end_session
    assert second.reply != first.reply
    assert len(dialogue.outbox.packets) == 1
    assert dialogue.outbox.packets[0].trigger.value == "low_understanding"
    assert _unanswered_in(dialogue) == [(Slot[pending.upper()], 2)]


def _awaiting_filing_confirmation_of_two(policy: Policy, retriever: LexicalRetriever) -> _Dialogue:
    port = FakeToolPort(
        transactions=(_transaction(), _transaction("TX-2", merchant="Netflix")),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    return dialogue


@pytest.mark.parametrize("through", ["confirmation", "file_dispute"])
def test_a_different_reason_at_the_filing_question_evaluates_again_under_that_reason(
    policy: Policy, retriever: LexicalRetriever, through: str
) -> None:
    dialogue, _ = _awaiting_filing_confirmation(policy, retriever)
    if through == "confirmation":
        changed = NluResult(
            intent=NluIntent.CONFIRMATION,
            confidence=0.9,
            language="es",
            confirmation=ConfirmationAnswer.YES_WITH_CHANGE,
            category=DisputeCategory.DUPLICATE_CHARGE,
        )
    else:
        changed = _file_dispute(category=DisputeCategory.DUPLICATE_CHARGE)

    response = dialogue.say(changed)

    assert response.next_expected is Slot.CONFIRMATION
    assert "cargo duplicado" in response.reply
    assert not response.end_session
    assert dialogue.outbox.packets == []
    assert dialogue.port.evaluate_requests[-1].category is DisputeCategory.DUPLICATE_CHARGE
    assert dialogue.port.evaluate_requests[-1].transaction_ref == "TX-1"
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.category is DisputeCategory.DUPLICATE_CHARGE
    assert state.selected_ref == "TX-1"
    assert state.clarification_attempts == 0
    assert dialogue.port.create_calls == 0


def test_a_different_transaction_at_the_filing_question_is_presented_for_confirmation(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _awaiting_filing_confirmation_of_two(policy, retriever)

    response = dialogue.say(_file_dispute(transaction=TransactionHint(merchant="Netflix")))

    assert response.next_expected is Slot.TRANSACTION_CHOICE
    assert "Netflix" in response.reply
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-2"
    assert state.category is DisputeCategory.UNRECOGNIZED_CHARGE
    assert dialogue.port.create_calls == 0

    confirmed = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert confirmed.next_expected is Slot.CONFIRMATION
    assert dialogue.port.evaluate_requests[-1].transaction_ref == "TX-2"
    assert dialogue.port.evaluate_requests[-1].category is DisputeCategory.UNRECOGNIZED_CHARGE
    assert dialogue.port.create_calls == 0


def test_a_different_transaction_and_reason_together_carry_both_to_the_new_transaction(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _awaiting_filing_confirmation_of_two(policy, retriever)

    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Netflix"),
            category=DisputeCategory.DUPLICATE_CHARGE,
        )
    )
    confirmed = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert confirmed.next_expected is Slot.CONFIRMATION
    assert dialogue.port.evaluate_requests[-1].transaction_ref == "TX-2"
    assert dialogue.port.evaluate_requests[-1].category is DisputeCategory.DUPLICATE_CHARGE


def test_a_transaction_not_found_at_the_filing_question_selects_none_and_files_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue, _ = _awaiting_filing_confirmation(policy, retriever)

    response = dialogue.say(_file_dispute(transaction=TransactionHint(merchant="Zzzz")))

    assert response.next_expected is Slot.TRANSACTION
    assert not response.end_session
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None
    assert state.pending_slot is Slot.TRANSACTION
    assert state.clarification_attempts == 0
    assert state.category is DisputeCategory.UNRECOGNIZED_CHARGE

    after_yes = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert after_yes.next_expected is Slot.TRANSACTION
    assert dialogue.port.create_calls == 0
    assert len(dialogue.port.evaluate_requests) == 1


def test_several_transactions_matching_at_the_filing_question_ask_for_detail_and_file_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(
            _transaction(),
            _transaction("TX-2", merchant="Netflix"),
            _transaction("TX-3", merchant="Netflix"),
        ),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    dialogue.say(_confirmation(ConfirmationAnswer.YES))

    response = dialogue.say(_file_dispute(transaction=TransactionHint(merchant="Netflix")))

    assert response.next_expected is Slot.TRANSACTION
    assert not response.end_session
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None
    assert state.clarification_attempts == 0
    assert port.create_calls == 0
    assert len(port.evaluate_requests) == 1


def test_a_description_that_changes_nothing_at_the_filing_question_counts_against_the_budget(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue, _ = _awaiting_filing_confirmation(policy, retriever)
    repeat = _file_dispute(
        transaction=TransactionHint(merchant="Amazon"),
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )

    first = dialogue.say(repeat)

    assert first.next_expected is Slot.CONFIRMATION
    assert not first.end_session
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.clarification_attempts == 1
    assert len(dialogue.port.evaluate_requests) == 1

    second = dialogue.say(repeat)

    assert second.end_session
    assert len(dialogue.outbox.packets) == 1
    assert dialogue.outbox.packets[0].trigger.value == "low_understanding"
    assert dialogue.port.create_calls == 0


def test_a_change_then_a_description_that_changes_nothing_hands_off_at_the_budget(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue, _ = _awaiting_filing_confirmation(policy, retriever)

    asked = dialogue.say(_confirmation(ConfirmationAnswer.YES_WITH_CHANGE))
    handed_off = dialogue.say(_file_dispute(transaction=TransactionHint(merchant="Amazon")))

    assert "motivo" in asked.reply
    assert handed_off.end_session
    assert dialogue.outbox.packets[0].trigger.value == "low_understanding"


def test_the_same_reason_at_the_filing_question_is_still_a_change_to_ask_about(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue, _ = _awaiting_filing_confirmation(policy, retriever)
    same = NluResult(
        intent=NluIntent.CONFIRMATION,
        confidence=0.9,
        language="es",
        confirmation=ConfirmationAnswer.YES_WITH_CHANGE,
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )

    response = dialogue.say(same)

    assert "transacción" in response.reply and "motivo" in response.reply
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.clarification_attempts == 1


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        (
            "es",
            "No logré identificar qué desea cambiar. Dígame qué parte es: la transacción o el "
            "motivo de la disputa. Si todo está bien, responda sí.",
        ),
        (
            "pt",
            "Não consegui identificar o que você quer alterar. Diga qual parte é: a transação ou "
            "o motivo da contestação. Se estiver tudo certo, responda sim.",
        ),
        (
            "en",
            "I did not catch what you want to change. Tell me which part it is: the transaction "
            "or the reason for the dispute. If everything is right, answer yes.",
        ),
    ],
)
def test_the_change_question_is_asked_in_the_customers_language(
    policy: Policy, retriever: LexicalRetriever, lang: str, expected: str
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")).model_copy(
            update={"language": lang}
        )
    )

    response = dialogue.say(
        _confirmation(ConfirmationAnswer.YES_WITH_CHANGE).model_copy(update={"language": lang})
    )

    assert response.reply == expected


def _reply_to_after_asking(
    policy: Policy, retriever: LexicalRetriever, pending: Slot, answer: NluResult
) -> TurnResponse:
    dialogue = _Dialogue(policy, retriever)
    if pending is Slot.REASON:
        dialogue.present_amazon()
        asked = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    else:
        asked = dialogue.say(_file_dispute())
    assert asked.next_expected is pending
    return dialogue.say(answer)


@pytest.mark.parametrize("pending", [Slot.REASON, Slot.TRANSACTION])
def test_a_correction_at_a_question_that_is_not_a_change_target_reads_like_an_unclear_reply(
    policy: Policy, retriever: LexicalRetriever, pending: Slot
) -> None:
    roomy = policy.model_copy(
        update={"routing": policy.routing.model_copy(update={"clarification_budget": 3})}
    )
    unclear = _reply_to_after_asking(roomy, retriever, pending, _plain(NluIntent.UNCLEAR))
    correction = _reply_to_after_asking(roomy, retriever, pending, _plain(NluIntent.CORRECTION))

    assert not unclear.end_session
    assert not correction.end_session
    assert unclear.next_expected is pending
    assert correction.reply == unclear.reply
    assert correction.next_expected is pending
    assert "No logré identificar qué desea cambiar" not in correction.reply


_UNCLEAR_QUESTION_WORDING = {
    "es": ("comercio", "monto", "fecha"),
    "pt": ("estabelecimento", "valor", "data"),
    "en": ("merchant", "amount", "date"),
}


@pytest.mark.parametrize("language", ["es", "pt", "en"])
def test_unclear_first_text_asks_which_transaction_instead_of_repeating_the_menu(
    policy: Policy, retriever: LexicalRetriever, language: str
) -> None:
    dialogue = _Dialogue(policy, retriever)
    menu = dialogue.say(_plain(NluIntent.SMALL_TALK, language=language))

    reply = _Dialogue(policy, retriever).say(_plain(NluIntent.UNCLEAR, language=language))

    assert reply.next_expected is Slot.TRANSACTION
    assert reply.reply != menu.reply
    assert all(word in reply.reply for word in _UNCLEAR_QUESTION_WORDING[language])
    assert not reply.end_session
    assert reply.handoff_ticket is None
    assert reply.case_number is None


def test_a_greeting_then_a_vague_request_asks_for_the_transaction_only_the_second_time(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)

    greeting = dialogue.say(_plain(NluIntent.SMALL_TALK))
    request = dialogue.say(_plain(NluIntent.UNCLEAR))

    assert greeting.next_expected is None
    assert request.next_expected is Slot.TRANSACTION
    assert request.reply != greeting.reply
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.pending_slot is Slot.TRANSACTION


@pytest.mark.parametrize("intent", [NluIntent.SMALL_TALK, NluIntent.FAREWELL])
def test_a_greeting_or_thanks_never_opens_the_transaction_question(
    policy: Policy, retriever: LexicalRetriever, intent: NluIntent
) -> None:
    dialogue = _Dialogue(policy, retriever)

    reply = dialogue.say(_plain(intent))

    assert reply.next_expected is None
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.pending_slot is None


@pytest.mark.parametrize("language", ["es", "pt", "en"])
def test_two_unusable_replies_hand_over_with_the_missing_transaction_recorded(
    policy: Policy, retriever: LexicalRetriever, language: str
) -> None:
    dialogue = _Dialogue(policy, retriever)

    first = dialogue.say(_plain(NluIntent.UNCLEAR, language=language))
    second = dialogue.say(_plain(NluIntent.UNCLEAR, language=language))
    assert first.next_expected is Slot.TRANSACTION
    assert second.next_expected is Slot.TRANSACTION
    assert second.reply == first.reply
    assert dialogue.outbox.packets == []

    third = dialogue.say(_plain(NluIntent.UNCLEAR, language=language))

    assert third.end_session
    assert third.next_expected is None
    assert third.handoff_ticket is not None
    [packet] = dialogue.outbox.packets
    assert packet.trigger.value == "low_understanding"
    assert [code.value for code in packet.evidence.reason_codes] == ["escalate_low_nlu_confidence"]
    assert [(q.slot, q.attempts) for q in packet.open_questions] == [(Slot.TRANSACTION, 2)]


def test_a_vague_message_answered_with_a_description_goes_on_to_the_transaction(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    asked = dialogue.say(_plain(NluIntent.UNCLEAR))
    assert asked.next_expected is Slot.TRANSACTION

    found = dialogue.say(_file_dispute(transaction=TransactionHint(merchant="Amazon")))

    assert found.next_expected is not Slot.TRANSACTION
    assert not found.end_session
    assert dialogue.outbox.packets == []


def test_a_correction_with_no_open_question_falls_back_to_the_greeting(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    greeting = dialogue.say(_plain(NluIntent.SMALL_TALK))

    response = dialogue.say(_plain(NluIntent.CORRECTION))

    assert response.reply == greeting.reply
    assert response.next_expected is None
    assert not response.end_session
    assert dialogue.outbox.packets == []


@pytest.mark.parametrize(
    "unrelated",
    [
        _plain(NluIntent.SMALL_TALK),
        _plain(NluIntent.POLICY_QUESTION, policy_query="que es esta politica"),
        _plain(NluIntent.LIST_TRANSACTIONS),
    ],
    ids=["small-talk", "policy-question", "list-request"],
)
def test_the_presented_transaction_question_stays_pending_across_an_unrelated_reply(
    policy: Policy, retriever: LexicalRetriever, unrelated: NluResult
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.present_amazon()
    before = dialogue.store.get(_SESSION_ID)
    assert before is not None

    reply = dialogue.say(unrelated)
    assert reply.next_expected is Slot.TRANSACTION_CHOICE
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.pending_slot is Slot.TRANSACTION_CHOICE
    assert state.selected_ref == before.selected_ref
    assert state.clarification_attempts == before.clarification_attempts

    answered = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert answered.next_expected is Slot.REASON
    assert dialogue.port.create_calls == 0
    assert dialogue.outbox.packets == []


def test_a_repeated_turn_id_replays_the_presented_transaction(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    first = dialogue.present_amazon()
    stored = dialogue.store.get(_SESSION_ID)

    replay = dialogue.say(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")), turn_id="turn-0001"
    )
    assert replay.reply == first.reply
    assert replay.next_expected is Slot.TRANSACTION_CHOICE
    assert dialogue.store.get(_SESSION_ID) == stored
    assert dialogue.port.create_calls == 0
    assert dialogue.outbox.packets == []


def test_a_reason_instead_of_a_yes_files_directly_and_leaves_no_slot_pending(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        cases=(_case(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=False
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.present_amazon()

    filed = dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    assert port.create_calls == 1
    assert filed.next_expected is None
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.pending_slot is None


def test_presenting_after_a_narrowing_prompt_starts_the_clarification_budget_over(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    assert dialogue.say(_file_dispute()).next_expected is Slot.TRANSACTION
    assert dialogue.present_amazon().next_expected is Slot.TRANSACTION_CHOICE

    again = dialogue.say(_confirmation(ConfirmationAnswer.AMBIGUOUS))
    assert again.next_expected is Slot.TRANSACTION_CHOICE
    assert dialogue.outbox.packets == []


def test_an_unclear_answer_hands_off_when_the_transaction_cannot_be_read(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    for unreadable in (None, ToolFailure(tool=ToolName.GET_TRANSACTION, cause="error")):
        dialogue = _Dialogue(policy, retriever)
        dialogue.present_amazon()
        dialogue.port.get_transaction_result = unreadable

        response = dialogue.say(_confirmation(ConfirmationAnswer.AMBIGUOUS))
        assert response.end_session
        assert len(dialogue.outbox.packets) == 1


def test_replaying_an_unreadable_presentation_hands_off_without_changing_state(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.present_amazon()
    stored = dialogue.store.get(_SESSION_ID)
    dialogue.port.get_transaction_result = None

    replay = dialogue.say(
        _file_dispute(transaction=TransactionHint(merchant="Amazon")), turn_id="turn-0001"
    )
    assert replay.end_session
    assert dialogue.store.get(_SESSION_ID) == stored
    assert dialogue.outbox.packets == []


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


# -----------------------------------------------------------------------------
# The question about which transaction, and how many answers it is given
# -----------------------------------------------------------------------------


def test_the_transaction_question_is_asked_twice_before_a_person_is_involved(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)

    first = dialogue.say(_file_dispute())
    assert first.next_expected is Slot.TRANSACTION
    assert not first.end_session

    second = dialogue.say(_plain(NluIntent.UNCLEAR))
    assert second.next_expected is Slot.TRANSACTION
    assert not second.end_session
    assert dialogue.outbox.packets == []

    third = dialogue.say(_plain(NluIntent.UNCLEAR))
    assert third.end_session
    assert [packet.trigger.value for packet in dialogue.outbox.packets] == ["low_understanding"]


def test_the_reason_question_is_asked_twice_before_a_person_is_involved(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.present_amazon()
    asked = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert asked.next_expected is Slot.REASON

    again = dialogue.say(_plain(NluIntent.UNCLEAR))
    assert again.next_expected is Slot.REASON
    assert not again.end_session
    assert dialogue.outbox.packets == []

    third = dialogue.say(_plain(NluIntent.UNCLEAR))
    assert third.end_session
    assert [packet.trigger.value for packet in dialogue.outbox.packets] == ["low_understanding"]
    assert _unanswered_in(dialogue) == [(Slot.REASON, 2)]


def test_a_described_reply_after_an_unsettled_answer_starts_the_count_again(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(_file_dispute())
    dialogue.say(_plain(NluIntent.UNCLEAR))
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.clarification_attempts == 1

    presented = dialogue.say(
        _plain(NluIntent.UNCLEAR, transaction=TransactionHint(merchant="Amazon"))
    )

    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.clarification_attempts == 0


def test_a_category_on_a_described_answer_does_not_replace_the_one_already_set(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))

    presented = dialogue.say(
        _plain(
            NluIntent.CORRECTION,
            category=DisputeCategory.WRONG_AMOUNT,
            transaction=TransactionHint(merchant="Amazon"),
        )
    )

    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.category is DisputeCategory.UNRECOGNIZED_CHARGE


@pytest.mark.parametrize(
    "answer",
    [
        _plain(NluIntent.CORRECTION, transaction=TransactionHint(merchant="Amazon")),
        _plain(NluIntent.UNCLEAR, transaction=TransactionHint(merchant="Amazon")),
        _plain(NluIntent.CHOICE, choice=1, transaction=TransactionHint(merchant="Amazon")),
    ],
    ids=["correction", "unclear", "choice"],
)
def test_a_described_transaction_answers_the_question_whatever_intent_the_model_gave(
    policy: Policy, retriever: LexicalRetriever, answer: NluResult
) -> None:
    """The model reads each message on its own, so a plain answer to "which transaction?" can come
    back as a correction, a choice or unclear; the description it carries is still the answer."""
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(_file_dispute())

    presented = dialogue.say(answer)

    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    assert not presented.end_session
    assert dialogue.outbox.packets == []
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-1"


def test_an_unroutable_answer_describing_nothing_is_still_asked_again(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever)
    dialogue.say(_file_dispute())

    again = dialogue.say(_plain(NluIntent.CORRECTION))

    assert again.next_expected is Slot.TRANSACTION
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None
    assert state.clarification_attempts == 1


@pytest.mark.parametrize(
    ("language", "first_message", "second_message", "reading", "transaction"),
    [
        (
            "en",
            "Hi, there's a charge on my card I don't recognize.",
            "It was at Cine Premium on June 3rd, about 1,914,215 COP.",
            {
                "merchant": "Cine Premium",
                "date_expression": "June 3rd",
                "amount": "1,914,215",
                "currency": "COP",
            },
            ("Cine Premium", date(2026, 6, 3), Decimal("1914215"), "COP"),
        ),
        (
            "pt",
            "Quero contestar uma compra",
            "Foi na Farmacia Salud, no dia 21 de abril, cerca de 99.948,89 ARS.",
            {
                "merchant": "Farmacia Salud",
                "date_expression": "21 de abril",
                "amount": "99.948,89",
                "currency": "ARS",
            },
            ("Farmacia Salud", date(2026, 4, 21), Decimal("99948.89"), "ARS"),
        ),
    ],
)
@pytest.mark.parametrize("intent", ["file_dispute", "correction", "unclear"])
def test_the_live_second_message_presents_the_transaction_under_any_intent_the_model_gives(
    policy: Policy,
    retriever: LexicalRetriever,
    *,
    language: str,
    first_message: str,
    second_message: str,
    reading: dict[str, str],
    transaction: tuple[str, date, Decimal, str],
    intent: str,
) -> None:
    """The two conversations that were sent to a person on the customer's first answer: the
    customer's own wording, read as the model might label it, ends on the transaction presented."""
    merchant, occurred_on, amount, currency = transaction
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        transactions=(
            _transaction(
                "TX-WANTED",
                merchant=merchant,
                occurred_on=occurred_on,
                amount=amount,
                currency=currency,
            ),
        )
    )
    llm = FakeLlm(
        responses=[
            {
                "intent": "file_dispute",
                "confidence": 0.9,
                "language": language,
                "mentions_second_dispute": False,
            },
            {
                "intent": intent,
                "confidence": 0.9,
                "language": language,
                "mentions_second_dispute": False,
                **reading,
            },
        ]
    )
    controller = DialogueController(
        LlmNlu(llm, model="claude-haiku-4-5-20251001"),
        store=store,
        tool_port=port,
        retriever=retriever,
        policy=policy,
        outbox=outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
    )

    asked = controller.handle_turn(_turn("turn-0001", first_message), principal=_principal())
    assert asked.next_expected is Slot.TRANSACTION

    presented = controller.handle_turn(_turn("turn-0002", second_message), principal=_principal())

    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    assert not presented.end_session
    assert outbox.packets == []


# -----------------------------------------------------------------------------
# The console's own turn history
# -----------------------------------------------------------------------------


def test_a_fresh_turn_advance_records_its_own_history(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    turn_log = FakeDialogueTurnLog()
    result = NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, language="es")
    controller, _ = _controller(
        result,
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        turn_log=turn_log,
    )

    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert len(turn_log.entries) == 1
    entry, session_id = turn_log.entries[0]
    assert session_id == _SESSION_ID
    assert entry.turn_id == "turn-0001"
    assert entry.trace_id == _SESSION_ID
    assert entry.intent is Intent.CLARIFY
    assert entry.state_before == "started"
    assert entry.state_after == "started"
    assert entry.render_mode == "template"
    assert entry.reason_code is None


def test_a_hand_off_turn_records_the_reason_code_it_was_routed_with(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    turn_log = FakeDialogueTurnLog()
    understandings = [_file_dispute(), _plain(NluIntent.UNCLEAR), _plain(NluIntent.UNCLEAR)]
    for turn, understanding in enumerate(understandings, start=1):
        controller, _ = _controller(
            understanding,
            store=store,
            tool_port=FakeToolPort(),
            policy=policy,
            outbox=FakeHandoffOutbox(),
            retriever=retriever,
            turn_log=turn_log,
        )
        controller.handle_turn(_turn(f"turn-{turn:04d}"), principal=_principal())

    assert [entry.reason_code for entry, _ in turn_log.entries] == [
        None,
        None,
        ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,
    ]


def _logged_reason_codes(
    policy: Policy,
    retriever: LexicalRetriever,
    understandings: list[NluResult],
    *,
    port: FakeToolPort,
    outbox: FakeHandoffOutbox,
) -> list[ReasonCode | None]:
    store = InMemoryDialogueStore()
    turn_log = FakeDialogueTurnLog()
    for turn, understanding in enumerate(understandings, start=1):
        controller, _ = _controller(
            understanding,
            store=store,
            tool_port=port,
            policy=policy,
            outbox=outbox,
            retriever=retriever,
            turn_log=turn_log,
        )
        controller.handle_turn(_turn(f"turn-{turn:04d}"), principal=_principal())
    return [entry.reason_code for entry, _ in turn_log.entries]


def test_a_policy_escalation_records_its_reason_code_in_the_turn_history(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(Outcome.ESCALATE, ReasonCode.ESCALATE_FRAUD_CLAIM),
    )

    recorded = _logged_reason_codes(
        policy,
        retriever,
        [
            _file_dispute(transaction=TransactionHint(merchant="Amazon")),
            _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        ],
        port=port,
        outbox=FakeHandoffOutbox(),
    )

    assert recorded == [None, ReasonCode.ESCALATE_FRAUD_CLAIM]


def test_a_hand_off_that_names_no_reason_code_records_none(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        list_transactions_result=ToolFailure(tool=ToolName.LIST_TRANSACTIONS, cause="error")
    )

    recorded = _logged_reason_codes(
        policy,
        retriever,
        [_file_dispute(transaction=TransactionHint(merchant="Amazon"))],
        port=port,
        outbox=FakeHandoffOutbox(),
    )

    assert recorded == [None]


def test_a_hand_off_that_could_not_be_registered_records_no_reason_code(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    recorded = _logged_reason_codes(
        policy,
        retriever,
        [_file_dispute(), _plain(NluIntent.UNCLEAR), _plain(NluIntent.UNCLEAR)],
        port=FakeToolPort(),
        outbox=FakeHandoffOutbox(fail=True),
    )

    assert recorded == [None, None, None]


def test_no_turn_log_configured_records_nothing_and_never_fails(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """``turn_log`` is ``None`` by default: a turn advances exactly as it would otherwise."""
    result = NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, language="es")
    controller, _ = _controller(
        result,
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )

    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert "Hola" in response.reply
    assert response.state_version == 1
    assert not response.end_session


def test_replaying_a_turn_never_records_a_second_history_entry(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    turn_log = FakeDialogueTurnLog()
    result = NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, language="es")

    controller, _ = _controller(
        result,
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        turn_log=turn_log,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    controller, _ = _controller(
        result,
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        turn_log=turn_log,
    )
    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert len(turn_log.entries) == 1


def test_two_turns_of_one_conversation_record_their_own_turn_ids(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """Every entry shares the session as its trace, so the turn id is what tells them apart."""
    store = InMemoryDialogueStore()
    turn_log = FakeDialogueTurnLog()
    result = NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, language="es")

    for turn_id in ("turn-0001", "turn-0002"):
        controller, _ = _controller(
            result,
            store=store,
            tool_port=FakeToolPort(),
            policy=policy,
            outbox=FakeHandoffOutbox(),
            retriever=retriever,
            turn_log=turn_log,
        )
        controller.handle_turn(_turn(turn_id), principal=_principal())

    assert [entry.trace_id for entry, _ in turn_log.entries] == [_SESSION_ID, _SESSION_ID]
    assert [entry.turn_id for entry, _ in turn_log.entries] == ["turn-0001", "turn-0002"]


def test_a_turn_log_failure_never_changes_the_reply(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """Losing a timeline entry degrades the console's own view of the conversation, never the
    conversation itself — the same customer reply is returned either way."""
    result = NluResult(intent=NluIntent.SMALL_TALK, confidence=0.9, language="es")
    controller, _ = _controller(
        result,
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        turn_log=FakeDialogueTurnLog(fail=True),
    )

    with caplog.at_level(logging.WARNING):
        response = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert response.reply
    assert "dialogue_turn_not_logged" in caplog.text
    assert _SESSION_ID in caplog.text


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


def _unverified_filing_dialogue(
    policy: Policy, retriever: LexicalRetriever, language: str | None = None
) -> tuple[_Dialogue, TurnResponse]:
    """A session whose filing could not be read back and was handed to a person."""
    dialogue = _Dialogue(policy, retriever, _filing_port(cases=(), verified=False))
    dialogue.language = language
    dialogue.present_amazon()
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    handed_off = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert handed_off.handoff_ticket is not None
    assert dialogue.port.create_calls == 1
    return dialogue, handed_off


@pytest.mark.parametrize(
    "evaluation",
    [
        _decision(Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True),
        _decision(Outcome.ESCALATE, ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE),
        _decision(Outcome.INELIGIBLE, ReasonCode.DUPLICATE_OPEN_CASE),
        ToolFailure(tool=ToolName.EVALUATE_DISPUTE, cause="error"),
    ],
    ids=["eligible", "escalate", "duplicate_open_case", "tool_failure"],
)
def test_a_further_yes_after_an_unverified_filing_answers_with_the_ticket(
    policy: Policy, retriever: LexicalRetriever, evaluation: object
) -> None:
    """Whatever the evaluation would say now, a conversation handed to a person files nothing,
    opens no second ticket and keeps its state."""
    dialogue, handed_off = _unverified_filing_dialogue(policy, retriever)
    dialogue.port.evaluate_result = evaluation

    again = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert dialogue.port.create_calls == 1
    assert len(dialogue.outbox.packets) == 1
    assert again.handoff_ticket == handed_off.handoff_ticket
    assert again.end_session
    saved = dialogue.store.get(_SESSION_ID)
    assert saved is not None and saved.phase is ConversationPhase.HANDED_OFF


@pytest.mark.parametrize("language", ["es", "pt", "en"])
def test_a_further_yes_after_an_unverified_filing_files_nothing_in_every_language(
    policy: Policy, retriever: LexicalRetriever, language: str
) -> None:
    """The customer's repeated yes, in any supported language, gets the ticket of the handoff
    already made: no second case is created and no second ticket opened."""
    dialogue, handed_off = _unverified_filing_dialogue(policy, retriever, language)

    again = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert dialogue.port.create_calls == 1
    assert len(dialogue.outbox.packets) == 1
    assert again.handoff_ticket == handed_off.handoff_ticket
    assert again.end_session
    assert (
        again.reply
        == {
            "es": "Un asesor debe revisar esto. Su referencia es T-0001.",
            "pt": "Um atendente precisa analisar isso. Sua referência é T-0001.",
            "en": "A person must review this. Your reference is T-0001.",
        }[language]
    )
    saved = dialogue.store.get(_SESSION_ID)
    assert saved is not None and saved.lang == language


def test_a_no_then_a_new_filing_after_an_unverified_filing_files_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue, handed_off = _unverified_filing_dialogue(policy, retriever)

    dialogue.say(_confirmation(ConfirmationAnswer.NO))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    again = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert dialogue.port.create_calls == 1
    assert len(dialogue.outbox.packets) == 1
    assert again.handoff_ticket == handed_off.handoff_ticket


def test_a_dispute_message_after_an_unverified_filing_without_confirmation_files_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The filing that needs no confirmation is stopped as well: a later message that names the
    transaction and the reason again does not file it a second time."""
    port = _filing_port(cases=(), verified=False)
    port.evaluate_result = _decision(Outcome.ELIGIBLE, ReasonCode.ELIGIBLE)
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )
    handed_off = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert handed_off.handoff_ticket is not None
    assert port.create_calls == 1

    again = dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Amazon"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )

    assert port.create_calls == 1
    assert len(dialogue.outbox.packets) == 1
    assert again.handoff_ticket == handed_off.handoff_ticket


def test_an_unreachable_understanding_dependency_after_a_handoff_opens_no_second_ticket(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue, handed_off = _unverified_filing_dialogue(policy, retriever)
    controller = DialogueController(
        UnavailableNlu(),
        store=dialogue.store,
        tool_port=dialogue.port,
        retriever=retriever,
        policy=policy,
        outbox=dialogue.outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
    )

    for turn_id in ("turn-outage-1", "turn-outage-2"):
        again = controller.handle_turn(_turn(turn_id), principal=_principal())

        assert again.handoff_ticket == handed_off.handoff_ticket
        assert again.state_version == handed_off.state_version
    assert len(dialogue.outbox.packets) == 1
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.last_ticket_ref == handed_off.handoff_ticket


def test_a_handoff_leaves_no_question_pending() -> None:
    state = DialogueState(
        session_id=_SESSION_ID,
        lang="es",
        phase=ConversationPhase.CONFIRMING,
        pending_slot=Slot.CONFIRMATION,
        clarification_attempts=1,
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
        selected_ref="TX-1",
        updated_at=_NOW,
    )

    handed_off = state.with_handed_off("T-1")

    assert handed_off.pending_slot is None
    assert handed_off.selected_ref == "TX-1"
    assert handed_off.category is DisputeCategory.UNRECOGNIZED_CHARGE


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


def _filing_port(*, cases: tuple[CaseRecord, ...], verified: bool = True) -> FakeToolPort:
    """A port that evaluates the dispute as eligible with a confirmation and files it as D-1."""
    return FakeToolPort(
        transactions=(_transaction(),),
        cases=cases,
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
        get_case_result=_UNSET if verified else None,
    )


def test_the_case_number_is_null_until_the_filing_turn_and_set_on_it(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """Only the reply that reports the verified filing carries the case number."""
    dialogue = _Dialogue(policy, retriever, _filing_port(cases=(_case(),)))
    before = [
        dialogue.present_amazon(),
        dialogue.say(_confirmation(ConfirmationAnswer.YES)),
        dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE)),
    ]
    assert before[-1].next_expected is Slot.CONFIRMATION
    assert [turn.case_number for turn in before] == [None, None, None]

    filed = dialogue.say(_confirmation(ConfirmationAnswer.YES), turn_id="turn-filing")

    assert filed.case_number == "D-1"
    assert filed.handoff_ticket is None
    assert dialogue.say(_plain(NluIntent.SMALL_TALK)).case_number is None


def test_a_retried_filing_turn_carries_the_same_case_number(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The retry reports the case that was filed, and files nothing again."""
    dialogue = _Dialogue(policy, retriever, _filing_port(cases=(_case(),)))
    dialogue.present_amazon()
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    first = dialogue.say(_confirmation(ConfirmationAnswer.YES), turn_id="turn-filing")

    retried = dialogue.say(_confirmation(ConfirmationAnswer.YES), turn_id="turn-filing")

    assert retried.case_number == first.case_number == "D-1"
    assert dialogue.port.create_calls == 1


def test_a_retried_later_turn_reports_the_session_case_after_a_fresh_read_back(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A replay reports the latest outcome, so a retry after a filing repeats the filed case."""
    dialogue = _Dialogue(policy, retriever, _filing_port(cases=(_case(),)))
    dialogue.present_amazon()
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    dialogue.say(_confirmation(ConfirmationAnswer.YES), turn_id="turn-filing")
    later = dialogue.say(_plain(NluIntent.SMALL_TALK), turn_id="turn-later")
    assert later.case_number is None

    retried = dialogue.say(_plain(NluIntent.SMALL_TALK), turn_id="turn-later")

    assert retried.case_number == "D-1"
    assert dialogue.port.create_calls == 1


def test_the_case_number_is_null_when_the_filing_could_not_be_verified(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A case the read-back did not find is handed to a person, never reported as filed."""
    dialogue = _Dialogue(policy, retriever, _filing_port(cases=(), verified=False))
    dialogue.present_amazon()
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))

    handed_off = dialogue.say(_confirmation(ConfirmationAnswer.YES))

    assert handed_off.case_number is None
    assert handed_off.handoff_ticket is not None


@pytest.mark.parametrize("closing", ["cancelled", "ineligible"])
def test_the_case_number_is_null_when_the_dispute_ends_without_a_case(
    policy: Policy, retriever: LexicalRetriever, closing: str
) -> None:
    """A cancelled or refused dispute has no case to report."""
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=(
            _decision(Outcome.INELIGIBLE, ReasonCode.FILING_WINDOW_EXPIRED)
            if closing == "ineligible"
            else _decision(Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True)
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.present_amazon()
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))

    ended = dialogue.say(_confirmation(ConfirmationAnswer.NO))

    assert ended.case_number is None
    assert port.create_calls == 0


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
    assert "transacciones más recientes" in response.reply.lower()

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


def _three_transactions() -> tuple[TransactionFact, ...]:
    return (
        _transaction("TX-1", merchant="Amazon", amount=Decimal("100.00")),
        _transaction("TX-2", merchant="Netflix", amount=Decimal("45.50")),
        _transaction("TX-3", merchant="Uber", amount=Decimal("12.25")),
    )


def test_a_list_request_numbers_each_transaction_in_the_reply_and_the_choices(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))

    listed = dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    assert [choice.number for choice in listed.choices] == [1, 2, 3]
    for number, merchant, amount in ((1, "Amazon", "100"), (2, "Netflix", "45"), (3, "Uber", "12")):
        assert f"{number}. " in listed.reply
        assert merchant in listed.reply
        assert merchant in listed.choices[number - 1].label
        assert amount in listed.choices[number - 1].label
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.offered_refs == ("TX-1", "TX-2", "TX-3")


def test_a_dispute_described_while_a_list_is_on_offer_replaces_the_list_with_its_transaction(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    presented = dialogue.say(
        _file_dispute(
            transaction=TransactionHint(merchant="Netflix"),
            category=DisputeCategory.UNRECOGNIZED_CHARGE,
        )
    )

    assert presented.next_expected is Slot.TRANSACTION_CHOICE
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.offered_refs == ()
    assert state.selected_ref == "TX-2"

    later = dialogue.say(_plain(NluIntent.CHOICE, choice=3))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-2"
    assert later.next_expected is Slot.TRANSACTION_CHOICE


def test_a_correction_while_a_list_is_on_offer_leaves_the_list_to_choose_from(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    dialogue.say(_plain(NluIntent.CORRECTION))
    picked = dialogue.say(_plain(NluIntent.CHOICE, choice=1))

    assert picked.next_expected is Slot.REASON
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-1"


def test_a_reply_that_is_not_a_list_offers_no_choices(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))

    assert dialogue.say(_plain(NluIntent.SMALL_TALK)).choices == ()
    assert dialogue.present_amazon().choices == ()


def test_a_number_chosen_from_the_list_selects_the_transaction_shown_at_that_position(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    picked = dialogue.say(_plain(NluIntent.CHOICE, choice=2))

    assert picked.next_expected is Slot.REASON
    assert picked.choices == ()
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-2"
    assert state.offered_refs == ()
    assert state.pending_slot is Slot.REASON


def test_a_number_chosen_from_the_list_evaluates_when_the_reason_is_already_known(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=_three_transactions(),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
    )
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    confirm = dialogue.say(_plain(NluIntent.CHOICE, choice=3))

    assert confirm.next_expected is Slot.CONFIRMATION
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-3"


@pytest.mark.parametrize("number", [4, 5], ids=["past-the-end", "far-past"])
def test_a_choice_outside_the_list_shown_selects_nothing_and_shows_the_list_again(
    policy: Policy, retriever: LexicalRetriever, number: int
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    again = dialogue.say(_plain(NluIntent.CHOICE, choice=number))

    assert [choice.number for choice in again.choices] == [1, 2, 3]
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None
    assert state.offered_refs == ("TX-1", "TX-2", "TX-3")


@pytest.mark.parametrize(
    ("text", "expected_ref"), [("1", "TX-1"), (" 2 ", "TX-2"), ("3", "TX-3"), ("03", "TX-3")]
)
def test_a_bare_number_sent_after_the_list_selects_that_position_without_the_model(
    policy: Policy, retriever: LexicalRetriever, text: str, expected_ref: str
) -> None:
    """Every position on the list is reachable by its number, the first and the last included."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    dialogue.understood.clear()

    picked = dialogue.say(_plain(NluIntent.SMALL_TALK), text=text)

    assert picked.next_expected is Slot.REASON
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == expected_ref
    assert state.offered_refs == ()
    assert dialogue.understood == []


@pytest.mark.parametrize("text", ["7", "0", "4", "99"])
def test_a_bare_number_past_the_end_of_the_list_shows_the_list_again(
    policy: Policy, retriever: LexicalRetriever, text: str
) -> None:
    """A number that is no position on the list is a request to see the list again."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    dialogue.understood.clear()

    again = dialogue.say(_plain(NluIntent.SMALL_TALK), text=text)

    assert [choice.number for choice in again.choices] == [1, 2, 3]
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None
    assert state.offered_refs == ("TX-1", "TX-2", "TX-3")
    assert dialogue.understood == []


@pytest.mark.parametrize("text", ["2 please", "dos", "100", "2024", "-1", "²", "2.5"])
def test_a_message_that_is_more_than_a_short_number_is_left_to_the_model(
    policy: Policy, retriever: LexicalRetriever, text: str
) -> None:
    """Only a short bare number is a position; amounts, words and decimals need the model."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    dialogue.understood.clear()

    dialogue.say(_plain(NluIntent.SMALL_TALK), text=text)

    assert dialogue.understood == [text]
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None


def test_a_number_turn_is_logged_with_no_model_cost(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """A number read without the model reports zero tokens and cost, and no message text."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    caplog.clear()

    with caplog.at_level("INFO"):
        dialogue.say(_plain(NluIntent.SMALL_TALK), text="2")

    completed = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("turn_completed")
    ]
    assert len(completed) == 1
    assert "input_tokens=0 output_tokens=0" in completed[0]
    assert "cost_usd=0" in completed[0]
    assert "model=None" in completed[0]


def test_a_replayed_out_of_range_number_shows_the_list_again_without_calling_the_model(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A retried number is answered as the first delivery was, not by the model's reading of it."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    first = dialogue.say(_plain(NluIntent.SMALL_TALK), turn_id="turn-0002", text="7")
    stored = dialogue.store.get(_SESSION_ID)
    dialogue.understood.clear()

    replay = dialogue.say(_plain(NluIntent.SMALL_TALK), turn_id="turn-0002", text="7")

    assert [choice.number for choice in replay.choices] == [1, 2, 3]
    assert replay.reply == first.reply
    assert dialogue.understood == []
    assert dialogue.store.get(_SESSION_ID) == stored


def test_a_replayed_digit_the_model_read_as_a_list_request_is_read_by_the_model_again(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """With no list on offer, a digit the model read as a list request still lists on a retry."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    first = dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS), turn_id="turn-0001", text="1")
    assert [choice.number for choice in first.choices] == [1, 2, 3]

    replay = dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS), turn_id="turn-0001", text="1")

    assert [choice.number for choice in replay.choices] == [1, 2, 3]
    assert replay.reply == first.reply
    assert replay.next_expected is None


def test_a_replayed_out_of_range_number_is_answered_while_the_model_is_unreachable(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The retry needs no model, so an outage must not end a conversation that is still open."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    first = dialogue.say(_plain(NluIntent.SMALL_TALK), turn_id="turn-0002", text="7")
    controller = DialogueController(
        UnavailableNlu(),
        store=dialogue.store,
        tool_port=dialogue.port,
        retriever=retriever,
        policy=policy,
        outbox=dialogue.outbox,
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
    )

    replay = controller.handle_turn(_turn("turn-0002", "7"), principal=_principal())

    assert not replay.end_session
    assert [choice.number for choice in replay.choices] == [1, 2, 3]
    assert replay.reply == first.reply


def test_a_bare_number_sent_with_no_list_on_offer_is_left_to_the_model(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A number means a position only while a list is on offer."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.SMALL_TALK))

    dialogue.say(_plain(NluIntent.SMALL_TALK), text="2")

    assert dialogue.understood == ["hola", "2"]
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None


def test_a_bare_number_sent_once_the_list_is_replaced_by_a_selection_is_left_to_the_model(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """Once a transaction is selected the list is gone, so a later number is not a position."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    dialogue.say(_plain(NluIntent.SMALL_TALK), text="2")
    dialogue.understood.clear()

    dialogue.say(_plain(NluIntent.SMALL_TALK), text="3")

    assert dialogue.understood == ["3"]
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-2"


def test_a_bare_number_never_selects_from_a_stored_list_once_the_conversation_is_final(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A list kept on a finished conversation is never offered again."""
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    listed = dialogue.store.get(_SESSION_ID)
    assert listed is not None
    assert len(listed.offered_refs) == 3
    dialogue.store.save(
        listed.model_copy(update={"phase": ConversationPhase.HANDED_OFF}),
        expected_version=listed.version,
        turn_id="turn-seed",
        now=_now(),
    )
    dialogue.understood.clear()

    dialogue.say(_plain(NluIntent.SMALL_TALK), text="2")

    assert dialogue.understood == ["2"]
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.phase is ConversationPhase.HANDED_OFF
    assert state.selected_ref is None


def test_a_choice_before_any_list_selects_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))

    dialogue.say(_plain(NluIntent.CHOICE, choice=1))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None


def test_a_list_request_while_a_transaction_is_awaiting_a_yes_lists_and_keeps_it_pending(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.present_amazon()

    listed = dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    assert listed.next_expected is Slot.TRANSACTION_CHOICE
    assert len(listed.choices) == 3
    stay = dialogue.store.get(_SESSION_ID)
    assert stay is not None
    assert stay.selected_ref == "TX-1"

    picked = dialogue.say(_plain(NluIntent.CHOICE, choice=2))
    assert picked.next_expected is Slot.REASON
    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-2"


def test_a_described_transaction_found_after_a_list_clears_the_offered_references(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    dialogue.say(_file_dispute(transaction=TransactionHint(merchant="Uber")))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-3"
    assert state.offered_refs == ()


def _list_filing_port() -> FakeToolPort:
    return FakeToolPort(
        transactions=_three_transactions(),
        cases=(_case(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )


def test_a_number_sent_after_the_case_is_filed_selects_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = _list_filing_port()
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.present_amazon()
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    filed = dialogue.say(_confirmation(ConfirmationAnswer.YES))
    assert "D-1" in filed.reply

    dialogue.say(_plain(NluIntent.CHOICE, choice=2))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None
    assert state.offered_refs == ()
    assert state.last_case_number == "D-1"
    assert port.create_calls == 1


def test_a_list_requested_after_a_case_is_filed_starts_the_next_dispute_from_a_number(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    port = _list_filing_port()
    dialogue = _Dialogue(policy, retriever, port)
    dialogue.present_amazon()
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))

    dialogue.say(_plain(NluIntent.CHOICE, choice=2))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref == "TX-2"
    assert state.offered_refs == ()
    assert state.pending_slot is Slot.REASON
    assert port.create_calls == 1


def test_a_number_sent_after_the_conversation_is_handed_off_selects_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    dialogue.say(_plain(NluIntent.REQUEST_PERSON))

    dialogue.say(_plain(NluIntent.CHOICE, choice=2))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.phase is ConversationPhase.HANDED_OFF
    assert state.selected_ref is None
    assert state.offered_refs == ()


def test_a_number_never_selects_from_a_stored_list_once_the_conversation_is_final(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=_three_transactions()))
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    listed = dialogue.store.get(_SESSION_ID)
    assert listed is not None
    assert len(listed.offered_refs) == 3
    dialogue.store.save(
        listed.model_copy(update={"phase": ConversationPhase.HANDED_OFF}),
        expected_version=listed.version,
        turn_id="turn-seed",
        now=_now(),
    )

    dialogue.say(_plain(NluIntent.CHOICE, choice=2))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.phase is ConversationPhase.HANDED_OFF
    assert state.selected_ref is None


def test_a_number_sent_after_the_filing_is_cancelled_selects_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    dialogue = _Dialogue(policy, retriever, _list_filing_port())
    dialogue.present_amazon()
    dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS))
    dialogue.say(_confirmation(ConfirmationAnswer.YES))
    dialogue.say(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE))
    dialogue.say(_confirmation(ConfirmationAnswer.NO))

    dialogue.say(_plain(NluIntent.CHOICE, choice=3))

    state = dialogue.store.get(_SESSION_ID)
    assert state is not None
    assert state.phase is ConversationPhase.CLOSED
    assert state.selected_ref is None


@pytest.mark.parametrize("lang", ["es", "pt", "en"])
def test_the_list_reply_and_its_choices_stay_within_their_limits_for_the_longest_transactions(
    policy: Policy, retriever: LexicalRetriever, lang: str
) -> None:
    longest = tuple(
        _transaction(f"TX-{number}", merchant=f"{number}" * 80, amount=Decimal("999999999.99"))
        for number in range(1, 6)
    )
    unnamed = _transaction("TX-6", merchant=None, amount=None)
    for transactions in (longest, (unnamed,)):
        dialogue = _Dialogue(policy, retriever, FakeToolPort(transactions=transactions))

        listed = dialogue.say(_plain(NluIntent.LIST_TRANSACTIONS, language=lang))

        assert len(listed.reply) <= MAX_TEXT_LENGTH
        assert len(listed.choices) == len(transactions)
        assert all(0 < len(choice.label) <= 200 for choice in listed.choices)


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


def test_a_policy_question_answered_by_a_section_without_a_figure_cites_the_section(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    controller, _ = _controller(
        _plain(NluIntent.POLICY_QUESTION, policy_query="que transacciones se pueden disputar"),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )

    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert response.reply == (
        "Puede consultarlo en la sección “Qué transacciones se pueden disputar” de nuestra "
        "política de disputas."
    )


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


def test_a_conflicted_turns_real_model_cost_is_still_logged(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """The LLM call already happened and cost real money before the save was even attempted; a
    409 tells the client to retry, but it must not silently undercount that spend."""
    accounting = TurnAccounting(
        model="claude-haiku-4-5-20251001",
        prompt_version="1",
        input_tokens=50,
        output_tokens=10,
        latency_ms=120.0,
    )
    store = _AlwaysConflictStore(InMemoryDialogueStore())
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=store,  # type: ignore[arg-type]
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        accounting=accounting,
    )

    with caplog.at_level(logging.INFO), pytest.raises(ProblemError):
        controller.handle_turn(_turn("turn-0001"), principal=_principal())

    logged = [r for r in caplog.records if r.getMessage().startswith("turn_completed")]
    assert len(logged) == 1
    assert "model=claude-haiku-4-5-20251001" in logged[0].getMessage()
    assert "input_tokens=50" in logged[0].getMessage()


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


def _abandoned_conversation(
    policy: Policy, retriever: LexicalRetriever
) -> tuple[
    Callable[..., TurnResponse],
    InMemoryDialogueStore,
    FakeToolPort,
    FakeDialogueTurnLog,
    list[ScriptedNlu],
]:
    """A conversation with a confirmation open, and a way to run further turns on it."""
    store = InMemoryDialogueStore()
    turn_log = FakeDialogueTurnLog()
    port = FakeToolPort(
        transactions=(_transaction(),),
        cases=(_case(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
        ),
        create_result=CreateDisputeCaseResult(created=True, case_number="D-1"),
    )
    understood: list[ScriptedNlu] = []

    def run(result: NluResult, turn_id: str, *, failing: bool = False) -> TurnResponse:
        controller, nlu = _controller(
            result,
            store=store,
            tool_port=port,
            policy=policy,
            outbox=FakeHandoffOutbox(fail=failing),
            retriever=retriever,
            turn_log=turn_log,
        )
        understood.append(nlu)
        return controller.handle_turn(_turn(turn_id), principal=_principal())

    run(_file_dispute(transaction=TransactionHint(merchant="Amazon")), "turn-0001")
    asked = run(_file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE), "turn-0002")
    assert asked.next_expected is Slot.CONFIRMATION
    return run, store, port, turn_log, understood


def test_a_conversation_whose_handoff_was_not_registered_files_nothing_on_a_later_turn(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """The confirmation question left open when the handoff failed must not be answerable: the
    conversation answers that nothing was registered and creates no case, calls no model and
    records no history."""
    run, store, port, turn_log, understood = _abandoned_conversation(policy, retriever)
    abandoned = run(_plain(NluIntent.REQUEST_PERSON), "turn-0003", failing=True)
    assert abandoned.end_session
    saved = store.get(_SESSION_ID)
    assert saved is not None
    assert saved.phase is ConversationPhase.ABANDONED
    logged_before = len(turn_log.entries)

    with caplog.at_level(logging.WARNING):
        later = run(_confirmation(ConfirmationAnswer.YES), "turn-0004")

    assert later.reply == abandoned.reply
    assert later.end_session
    assert later.case_number is None
    assert port.create_calls == 0
    assert understood[-1].calls == []
    assert len(turn_log.entries) == logged_before
    after = store.get(_SESSION_ID)
    assert after is not None
    assert after.version == saved.version
    assert after.last_case_number is None
    refused = [r for r in caplog.records if "dialogue_abandoned_turn_refused" in r.getMessage()]
    assert len(refused) == 1
    assert f"session_id={_SESSION_ID}" in refused[0].getMessage()


def test_replaying_the_turn_that_abandoned_the_conversation_returns_the_notice(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """Sending the abandoning turn again must not re-ask the confirmation it left open."""
    run, _, port, _, _ = _abandoned_conversation(policy, retriever)
    abandoned = run(_plain(NluIntent.REQUEST_PERSON), "turn-0003", failing=True)
    assert abandoned.next_expected is None

    replayed = run(_plain(NluIntent.REQUEST_PERSON), "turn-0003")

    assert replayed.reply == abandoned.reply
    assert replayed.end_session
    assert replayed.next_expected is None
    assert port.create_calls == 0


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


def test_a_save_time_race_never_records_turn_history_either(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The lost-race ``DuplicateTurn`` replay path (distinct from the fast duplicate-turn-id path
    tested elsewhere) must never record history either: nothing new was actually decided here."""
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

    turn_log = FakeDialogueTurnLog()
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=RaceStore(),  # type: ignore[arg-type]
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        turn_log=turn_log,
    )

    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert turn_log.entries == []


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


def test_a_matchless_evaluate_dispute_result_hands_off_on_first_evaluation(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """None (the reference stopped resolving, or stopped being this customer's own,
    between an earlier read and this evaluation) is routed through the same fail-closed handoff
    as a genuine ToolFailure, never re-interpreted as an ineligible or eligible decision."""
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(transactions=(_transaction(),), evaluate_result=None)
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


def test_a_matchless_evaluate_dispute_result_hands_off_on_re_evaluation_at_confirmation(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The confirmation step re-evaluates fresh rather than trusting the earlier decision; a
    reference that stopped resolving in between must hand off the same way as the first call."""
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    port = FakeToolPort(
        transactions=(_transaction(),),
        evaluate_result=_decision(
            Outcome.ELIGIBLE, ReasonCode.ELIGIBLE, requires_confirmation=True
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

    # The world moved on: the reference no longer resolves by the time confirmation arrives.
    port.evaluate_result = None

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
# Per-turn cost accounting
# -----------------------------------------------------------------------------


def test_a_turn_with_no_real_model_call_logs_zeroed_accounting(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """The log line's shape never varies: a scripted (FakeNlu-like) turn logs zero/None values,
    not a missing line."""
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )
    with caplog.at_level(logging.INFO):
        controller.handle_turn(_turn("turn-0001"), principal=_principal())

    logged = [r for r in caplog.records if r.getMessage().startswith("turn_completed")]
    assert len(logged) == 1
    message = logged[0].getMessage()
    assert f"session_id={_SESSION_ID}" in message
    assert "case_number=None" in message
    assert "model=None" in message
    assert "input_tokens=0" in message
    assert "output_tokens=0" in message
    assert "cost_usd=0" in message


def test_a_turn_with_a_real_model_call_logs_its_accounting(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    accounting = TurnAccounting(
        model="claude-haiku-4-5-20251001",
        prompt_version="1",
        input_tokens=100,
        output_tokens=20,
        latency_ms=250.0,
    )
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        accounting=accounting,
    )
    with caplog.at_level(logging.INFO):
        controller.handle_turn(_turn("turn-0001"), principal=_principal())

    logged = [r for r in caplog.records if r.getMessage().startswith("turn_completed")]
    assert len(logged) == 1
    message = logged[0].getMessage()
    assert "model=claude-haiku-4-5-20251001" in message
    assert "input_tokens=100" in message
    assert "output_tokens=20" in message
    assert "latency_ms=250.0" in message
    # 100 * $1/M + 20 * $5/M = 0.0001 + 0.0001 = 0.0002
    assert "cost_usd=0.0002" in message


def test_an_unpriced_model_never_aborts_the_turn(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """A model missing from the price table must not turn a real customer outcome into a 500:
    the turn still completes, its state is still saved, and only a warning names the gap."""
    accounting = TurnAccounting(
        model="claude-opus-4",  # not in app.llm.pricing's table
        prompt_version="1",
        input_tokens=100,
        output_tokens=20,
        latency_ms=250.0,
    )
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        accounting=accounting,
    )

    with caplog.at_level(logging.INFO):
        response = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert response.reply
    assert store.get(_SESSION_ID) is not None  # the save ran; the turn's own state is persisted
    warnings = [r for r in caplog.records if r.getMessage().startswith("turn_cost_unpriced")]
    assert len(warnings) == 1
    assert "model=claude-opus-4" in warnings[0].getMessage()
    completed = [r for r in caplog.records if r.getMessage().startswith("turn_completed")]
    assert len(completed) == 1
    assert "cost_usd=None" in completed[0].getMessage()


def test_an_exact_duplicate_turn_id_does_not_re_log_accounting(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """The early exact-duplicate-turn-id path (the same client request retried) returns the
    replayed envelope without a new understanding call, so it must not log a second
    turn_completed line — the model was only ever called once."""
    accounting = TurnAccounting(
        model="claude-haiku-4-5-20251001",
        prompt_version="1",
        input_tokens=100,
        output_tokens=20,
        latency_ms=250.0,
    )
    store = InMemoryDialogueStore()
    controller, _ = _controller(
        _plain(NluIntent.SMALL_TALK),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
        accounting=accounting,
    )

    with caplog.at_level(logging.INFO):
        first = controller.handle_turn(_turn("turn-0001"), principal=_principal())
        second = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert first.reply == second.reply
    logged = [r for r in caplog.records if r.getMessage().startswith("turn_completed")]
    assert len(logged) == 1


def test_a_turn_that_files_a_case_logs_its_case_number(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    """The filing turn's own log line already carries the new case number (the "per case" key),
    mirroring ``test_no_confirmation_required_files_immediately``'s own filing flow."""
    store = InMemoryDialogueStore()
    case = _case("D-1")
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
    with caplog.at_level(logging.INFO):
        controller.handle_turn(_turn("turn-0001"), principal=_principal())
    caplog.clear()  # only turn-0002's own log line is under test below
    controller, _ = _controller(
        _file_dispute(category=DisputeCategory.UNRECOGNIZED_CHARGE),
        store=store,
        tool_port=port,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        retriever=retriever,
    )

    with caplog.at_level(logging.INFO):
        response = controller.handle_turn(_turn("turn-0002"), principal=_principal())

    assert "D-1" in response.reply
    logged = [r for r in caplog.records if r.getMessage().startswith("turn_completed")]
    assert len(logged) == 1
    assert "case_number=D-1" in logged[0].getMessage()


# -----------------------------------------------------------------------------
# An unreachable understanding dependency: never the customer's own ambiguity
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
        max_turns=30,
    )

    response = controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert response.end_session
    assert len(outbox.packets) == 1
    assert outbox.packets[0].trigger.value == "tool_failure"


def test_an_unreachable_understanding_dependency_still_records_turn_history(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The forced handoff genuinely advances the session (a new ``HANDED_OFF`` state is saved),
    unlike a replay, so the console's own timeline must still see it — this is exactly the
    escalation-under-degradation event that timeline exists to surface."""
    turn_log = FakeDialogueTurnLog()
    controller = DialogueController(
        UnavailableNlu(),
        store=InMemoryDialogueStore(),
        tool_port=FakeToolPort(),
        retriever=retriever,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
        turn_log=turn_log,
    )

    controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert len(turn_log.entries) == 1
    entry, session_id = turn_log.entries[0]
    assert session_id == _SESSION_ID
    assert entry.turn_id == "turn-0001"
    assert entry.intent is Intent.HANDOFF
    assert entry.state_before == "started"
    assert entry.state_after == "handed_off"


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
        max_turns=30,
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
        max_turns=30,
    )

    response = replay_controller.handle_turn(_turn("turn-0001"), principal=_principal())

    assert response.end_session
    assert outbox.packets == []
    assert store.get(_SESSION_ID) == before


# -----------------------------------------------------------------------------
# Per-session turn cap
# -----------------------------------------------------------------------------


def _seed_session(
    store: InMemoryDialogueStore, *, turns: int, lang: str = "es", **changes: object
) -> DialogueState:
    """A session that has already applied ``turns`` customer turns."""
    values: dict[str, object] = {"session_id": _SESSION_ID, "lang": lang, "updated_at": _NOW}
    state = DialogueState(**{**values, **changes})
    saved = store.save(state, expected_version=turns - 1, turn_id="turn-seed", now=_now())
    assert saved.turns_applied == turns
    return saved


def _capped_controller(
    policy: Policy,
    retriever: LexicalRetriever,
    store: InMemoryDialogueStore,
    outbox: FakeHandoffOutbox,
    *,
    max_turns: int = 3,
) -> tuple[DialogueController, ScriptedNlu]:
    return _controller(
        _plain(NluIntent.SMALL_TALK),
        store=store,
        tool_port=FakeToolPort(),
        policy=policy,
        outbox=outbox,
        retriever=retriever,
        max_turns=max_turns,
    )


def test_the_turn_after_the_cap_is_handed_to_a_person_and_the_cap_turn_is_served(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    controller, nlu = _capped_controller(policy, retriever, store, outbox)

    for number in range(1, 4):
        served = controller.handle_turn(_turn(f"turn-{number:04d}"), principal=_principal())
        assert served.handoff_ticket is None
    assert len(nlu.calls) == 3
    assert outbox.packets == []

    capped = controller.handle_turn(_turn("turn-0004"), principal=_principal())

    assert capped.handoff_ticket == "T-0001"
    assert "T-0001" in capped.reply
    assert capped.end_session
    assert len(outbox.packets) == 1
    packet = outbox.packets[0]
    assert packet.trigger is HandoffTrigger.LOW_UNDERSTANDING
    assert [(a.action, a.result) for a in packet.actions] == [("turn_cap", "reached")]


def test_a_capped_turn_never_calls_the_model(policy: Policy, retriever: LexicalRetriever) -> None:
    store = InMemoryDialogueStore()
    _seed_session(store, turns=3)
    controller, nlu = _capped_controller(policy, retriever, store, FakeHandoffOutbox())

    controller.handle_turn(_turn("turn-0004"), principal=_principal())

    assert nlu.calls == []


@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("es", "Un asesor debe revisar esto."),
        ("pt", "Um atendente precisa analisar isso."),
        ("en", "A person must review this."),
    ],
)
def test_the_capped_reply_is_in_the_session_language(
    policy: Policy, retriever: LexicalRetriever, lang: str, expected: str
) -> None:
    store = InMemoryDialogueStore()
    _seed_session(store, turns=3, lang=lang)
    controller, _ = _capped_controller(policy, retriever, store, FakeHandoffOutbox())

    capped = controller.handle_turn(_turn("turn-0004"), principal=_principal())

    assert expected in capped.reply
    assert "T-0001" in capped.reply


def test_further_turns_after_the_cap_return_the_same_ticket_and_change_nothing(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    _seed_session(store, turns=3)
    controller, nlu = _capped_controller(policy, retriever, store, outbox)
    first = controller.handle_turn(_turn("turn-0004"), principal=_principal())
    version_at_handoff = store.get(_SESSION_ID)
    assert version_at_handoff is not None

    for number in range(5, 8):
        again = controller.handle_turn(_turn(f"turn-{number:04d}"), principal=_principal())
        assert again.handoff_ticket == first.handoff_ticket

    assert len(outbox.packets) == 1
    assert nlu.calls == []
    assert store.get(_SESSION_ID) == version_at_handoff


def test_repeating_the_capped_turn_replays_the_handoff_not_a_filing_result(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """A session that filed a case and then hit the cap must replay its handoff, not re-announce
    the earlier filing, when the capped turn's id arrives again."""
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    _seed_session(store, turns=3, last_case_number="D-1")
    controller, _ = _capped_controller(policy, retriever, store, outbox)
    first = controller.handle_turn(_turn("turn-0004"), principal=_principal())

    replay = controller.handle_turn(_turn("turn-0004"), principal=_principal())

    assert replay.reply == first.reply
    assert replay.handoff_ticket == first.handoff_ticket
    assert "D-1" not in replay.reply
    assert len(outbox.packets) == 1
    assert outbox.packets[0].existing_case_number == "D-1"


def test_a_duplicate_of_a_served_turn_is_replayed_even_at_the_cap(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    outbox = FakeHandoffOutbox()
    controller, _ = _capped_controller(policy, retriever, store, outbox)
    for number in range(1, 4):
        last = controller.handle_turn(_turn(f"turn-{number:04d}"), principal=_principal())

    replay = controller.handle_turn(_turn("turn-0003"), principal=_principal())

    assert replay.reply == last.reply
    assert replay.handoff_ticket is None
    assert outbox.packets == []


@dataclass
class _CappedRaceStore:
    """Serves a session at the cap and makes the cap turn's save lose to another request."""

    inner: InMemoryDialogueStore
    outcome: Exception

    def get(self, session_id: str) -> DialogueState | None:
        return self.inner.get(session_id)

    def save(
        self, state: DialogueState, *, expected_version: int, turn_id: str, now: datetime
    ) -> DialogueState:
        raise self.outcome


def test_a_capped_turn_that_loses_the_save_race_replays_the_winners_handoff(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """The packet this request recorded stays in the queue unreferenced by the session; the
    reply is the winner's ticket, so the customer sees one."""
    inner = InMemoryDialogueStore()
    _seed_session(inner, turns=3)
    winner = DialogueState(
        session_id=_SESSION_ID,
        version=4,
        lang="es",
        phase=ConversationPhase.HANDED_OFF,
        last_turn_id="turn-0004",
        last_ticket_ref="T-9999",
        updated_at=_NOW,
    )
    outbox = FakeHandoffOutbox()
    controller, nlu = _capped_controller(
        policy,
        retriever,
        _CappedRaceStore(inner, DuplicateTurn(winner)),  # type: ignore[arg-type]
        outbox,
    )

    response = controller.handle_turn(_turn("turn-0004"), principal=_principal())

    assert response.handoff_ticket == "T-9999"
    assert "T-9999" in response.reply
    assert len(outbox.packets) == 1
    assert nlu.calls == []


def test_a_capped_turn_on_a_stale_version_is_a_turn_conflict(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    inner = InMemoryDialogueStore()
    _seed_session(inner, turns=3)
    controller, nlu = _capped_controller(
        policy,
        retriever,
        _CappedRaceStore(inner, Conflict("moved on")),  # type: ignore[arg-type]
        FakeHandoffOutbox(),
    )

    with pytest.raises(ProblemError) as excinfo:
        controller.handle_turn(_turn("turn-0004"), principal=_principal())

    assert excinfo.value.code is ErrorCode.TURN_CONFLICT
    assert nlu.calls == []


def test_an_unavailable_outbox_at_the_cap_says_nothing_was_registered_and_the_next_message_retries(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    store = InMemoryDialogueStore()
    _seed_session(store, turns=3)
    outbox = FakeHandoffOutbox(fail=True)
    controller, nlu = _capped_controller(policy, retriever, store, outbox)

    failed = controller.handle_turn(_turn("turn-0004"), principal=_principal())

    assert failed.handoff_ticket is None
    assert failed.end_session
    assert outbox.packets == []

    outbox.fail = False
    retried = controller.handle_turn(_turn("turn-0005"), principal=_principal())

    assert retried.handoff_ticket == "T-0001"
    assert len(outbox.packets) == 1
    assert nlu.calls == []


def test_reaching_the_cap_is_logged_with_the_counts_and_no_message_text(
    policy: Policy, retriever: LexicalRetriever, caplog: pytest.LogCaptureFixture
) -> None:
    store = InMemoryDialogueStore()
    _seed_session(store, turns=3)
    controller, _ = _capped_controller(policy, retriever, store, FakeHandoffOutbox())

    with caplog.at_level(logging.WARNING):
        controller.handle_turn(_turn("turn-0004"), principal=_principal())

    logged = [
        r.getMessage() for r in caplog.records if "dialogue_turn_cap_reached" in r.getMessage()
    ]
    assert len(logged) == 1
    assert f"session_id={_SESSION_ID}" in logged[0]
    assert "turns_applied=3" in logged[0]
    assert "max_turns=3" in logged[0]
    assert _turn("turn-0004").text not in logged[0]


# -----------------------------------------------------------------------------
# A different transaction named while one is awaiting confirmation
# -----------------------------------------------------------------------------


def _two_transaction_controller(
    results: list[NluResult],
    *,
    store: InMemoryDialogueStore,
    policy: Policy,
    retriever: LexicalRetriever,
    port: FakeToolPort | None = None,
) -> DialogueController:
    return DialogueController(
        SequencedNlu(results),
        store=store,
        tool_port=port
        or FakeToolPort(
            transactions=(
                _transaction("TX-1", merchant="Amazon", amount=Decimal("100.00")),
                _transaction(
                    "TX-2",
                    merchant="Netflix",
                    amount=Decimal("15.99"),
                    occurred_on=date(2026, 6, 10),
                    last4="9876",
                ),
            )
        ),
        retriever=retriever,
        policy=policy,
        outbox=FakeHandoffOutbox(),
        domain_date=_DOMAIN_DATE,
        now=_now,
        max_turns=30,
    )


def _second_turn_after_naming_then_naming_again(
    first: TransactionHint,
    second: TransactionHint,
    *,
    policy: Policy,
    retriever: LexicalRetriever,
    port: FakeToolPort | None = None,
) -> tuple[str | None, TurnResponse]:
    store = InMemoryDialogueStore()
    controller = _two_transaction_controller(
        [_file_dispute(transaction=first), _file_dispute(transaction=second)],
        store=store,
        policy=policy,
        retriever=retriever,
        port=port,
    )
    controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal())
    reply = controller.handle_turn(_turn("turn-0002", "segunda"), principal=_principal())
    state = store.get(_SESSION_ID)
    assert state is not None
    return state.selected_ref, reply


def _selected_after_naming_then_naming_again(
    first: TransactionHint,
    second: TransactionHint,
    *,
    policy: Policy,
    retriever: LexicalRetriever,
    port: FakeToolPort | None = None,
) -> tuple[str | None, str]:
    selected, reply = _second_turn_after_naming_then_naming_again(
        first, second, policy=policy, retriever=retriever, port=port
    )
    return selected, reply.reply


@pytest.mark.parametrize("typed", ["\u0301", " ", "\u0301 \u0301"])
def test_a_merchant_that_names_nothing_keeps_the_presented_transaction(
    typed: str, policy: Policy, retriever: LexicalRetriever
) -> None:
    """A blank merchant is no description at all, so the transaction on offer stays selected."""
    selected, _reply = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        TransactionHint(merchant=typed),
        policy=policy,
        retriever=retriever,
    )

    assert selected == "TX-1"


@pytest.mark.parametrize(
    "other",
    [
        TransactionHint(amount=Decimal("15.99")),
        TransactionHint(date_on=date(2026, 6, 10), date_source=DateSource.ABSOLUTE),
    ],
    ids=["amount", "date"],
)
def test_a_blank_merchant_does_not_keep_the_selection_when_another_amount_or_date_is_named(
    other: TransactionHint, policy: Policy, retriever: LexicalRetriever
) -> None:
    """The amount or date beside a blank merchant still drops the transaction that was on offer."""
    selected, _reply = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        other.model_copy(update={"merchant": " "}),
        policy=policy,
        retriever=retriever,
    )

    assert selected == "TX-2"


@pytest.mark.parametrize(
    ("hint", "selected", "pending"),
    [
        (TransactionHint(merchant=" ", amount=Decimal("15.99")), "TX-2", Slot.TRANSACTION_CHOICE),
        (
            TransactionHint(
                merchant="\u0301", date_on=date(2026, 6, 10), date_source=DateSource.ABSOLUTE
            ),
            "TX-2",
            Slot.TRANSACTION_CHOICE,
        ),
        (TransactionHint(merchant=" "), None, Slot.TRANSACTION),
    ],
    ids=["amount", "date", "nothing else"],
)
def test_a_first_message_with_a_blank_merchant_searches_by_the_rest_of_the_hint(
    hint: TransactionHint,
    selected: str | None,
    pending: Slot,
    policy: Policy,
    retriever: LexicalRetriever,
) -> None:
    """A blank merchant adds nothing: the amount or date finds the transaction, and a hint with
    nothing else asks which transaction the customer means."""
    store = InMemoryDialogueStore()
    controller = _two_transaction_controller(
        [_file_dispute(transaction=hint)], store=store, policy=policy, retriever=retriever
    )

    controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal())

    state = store.get(_SESSION_ID)
    assert state is not None
    assert (state.selected_ref, state.pending_slot) == (selected, pending)


_GENERIC_MERCHANTS = [
    "transferencia",
    "transferencias",
    "transferencia bancaria",
    "transferência",
    "transferências",
    "transfer",
    "transfers",
    "bank transfer",
    "wire transfer",
    "pix",
    "transaccion",
    "transacción",
    "transacciones",
    "transacao",
    "transação",
    "transações",
    "transaction",
    "transactions",
    "movimiento",
    "movimientos",
    "movimento",
    "movimentos",
    "cargo",
    "cargos",
    "cobro",
    "cobros",
    "cobrança",
    "cobranças",
    "charge",
    "charges",
    "compra",
    "compras",
    "compra online",
    "compra en línea",
    "purchase",
    "purchases",
    "online purchase",
    "pago",
    "pagos",
    "pagamento",
    "pagamentos",
    "payment",
    "payments",
    "retiro",
    "retiros",
    "saque",
    "saques",
    "withdrawal",
    "withdrawals",
    "depósito",
    "depósitos",
    "deposit",
    "deposits",
    "tienda",
    "tiendas",
    "tienda en línea",
    "tienda online",
    "comercio",
    "establecimiento",
    "estabelecimento",
    "loja",
    "lojas",
    "loja online",
    "loja virtual",
    "store",
    "stores",
    "online store",
    "shop",
    "shops",
    "online shop",
    "merchant",
    "servicio",
    "servicios",
    "serviço",
    "serviços",
    "service",
    "services",
    "un servicio",
    "um serviço",
    "a service",
    "una transferencia",
    "uma transferência",
    "a transfer",
    "una tienda en línea",
    "the online store",
    "Transferencia ",
    "  LA TIENDA ",
]


def _transfer_controller(
    results: list[NluResult],
    *,
    store: InMemoryDialogueStore,
    policy: Policy,
    retriever: LexicalRetriever,
) -> DialogueController:
    """A customer with one purchase and one transfer, which has no merchant."""
    return _two_transaction_controller(
        results,
        store=store,
        policy=policy,
        retriever=retriever,
        port=FakeToolPort(
            transactions=(
                _transaction("TX-1", merchant="Amazon", amount=Decimal("100.00")),
                _transaction(
                    "TX-2",
                    merchant=None,
                    amount=Decimal("2763.79"),
                    occurred_on=date(2026, 6, 10),
                    last4="9876",
                ),
            )
        ),
    )


@pytest.mark.parametrize("typed", _GENERIC_MERCHANTS)
@pytest.mark.parametrize(
    "rest",
    [
        TransactionHint(amount=Decimal("2763.79"), currency="USD"),
        TransactionHint(date_on=date(2026, 6, 10), date_source=DateSource.ABSOLUTE),
        TransactionHint(
            amount=Decimal("2763.79"),
            date_on=date(2026, 6, 10),
            date_source=DateSource.ABSOLUTE,
        ),
    ],
    ids=["amount", "date", "amount-and-date"],
)
def test_a_word_for_a_kind_of_transaction_is_not_searched_for_as_a_merchant(
    typed: str, rest: TransactionHint, policy: Policy, retriever: LexicalRetriever
) -> None:
    """A transfer has no merchant, so the word the customer uses for it must not rule it out."""
    store = InMemoryDialogueStore()
    controller = _transfer_controller(
        [_file_dispute(transaction=rest.model_copy(update={"merchant": typed}))],
        store=store,
        policy=policy,
        retriever=retriever,
    )

    controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal())

    state = store.get(_SESSION_ID)
    assert state is not None
    assert (state.selected_ref, state.pending_slot) == ("TX-2", Slot.TRANSACTION_CHOICE)


@pytest.mark.parametrize("typed", _GENERIC_MERCHANTS)
def test_a_word_for_a_kind_of_transaction_alone_asks_which_transaction(
    typed: str, policy: Policy, retriever: LexicalRetriever
) -> None:
    """It names nothing to search by, so the customer is asked, not told nothing was found."""
    store = InMemoryDialogueStore()
    controller = _transfer_controller(
        [_file_dispute(transaction=TransactionHint(merchant=typed))],
        store=store,
        policy=policy,
        retriever=retriever,
    )
    nothing_found = _transfer_controller(
        [_file_dispute(transaction=_NOBODY)],
        store=InMemoryDialogueStore(),
        policy=policy,
        retriever=retriever,
    )

    asked = controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal())
    missed = nothing_found.handle_turn(_turn("turn-0001", "primera"), principal=_principal())

    assert asked.next_expected is Slot.TRANSACTION
    assert asked.reply != missed.reply


@pytest.mark.parametrize("typed", _GENERIC_MERCHANTS)
def test_a_word_for_a_kind_of_transaction_keeps_the_presented_transaction(
    typed: str, policy: Policy, retriever: LexicalRetriever
) -> None:
    selected, _reply = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        TransactionHint(merchant=typed),
        policy=policy,
        retriever=retriever,
    )

    assert selected == "TX-1"


@pytest.mark.parametrize(
    "article", ["un", "una", "el", "la", "o", "a", "um", "uma", "the", "an", "my"]
)
def test_every_leading_article_is_ignored_before_a_generic_word(
    article: str, policy: Policy, retriever: LexicalRetriever
) -> None:
    def reply_for(typed: str) -> str:
        controller = _transfer_controller(
            [_file_dispute(transaction=TransactionHint(merchant=typed))],
            store=InMemoryDialogueStore(),
            policy=policy,
            retriever=retriever,
        )
        return controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal()).reply

    assert reply_for(f"{article} transfer") == reply_for("transfer")
    assert reply_for(f"{article} transfer") != reply_for("Zzzz Unmatched")


@pytest.mark.parametrize("merchant", ["A&A", "$$", "The A"])
def test_a_merchant_made_only_of_articles_or_symbols_is_searched_for_like_any_other(
    merchant: str, policy: Policy, retriever: LexicalRetriever
) -> None:
    """Such a name is a merchant's, not a word for a kind of transaction: it is searched for and,
    matching nothing, is answered as any unmatched merchant is, rather than being dropped."""

    def reply_for(typed: str) -> str:
        controller = _two_transaction_controller(
            [_file_dispute(transaction=TransactionHint(merchant=typed))],
            store=InMemoryDialogueStore(),
            policy=policy,
            retriever=retriever,
        )
        return controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal()).reply

    assert reply_for(merchant) == reply_for("Zzzz Unmatched")
    assert reply_for(merchant) != reply_for("transfer")


def test_a_merchant_that_merely_contains_a_generic_word_is_still_searched_for(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    """Only a description made of the generic word alone is dropped."""
    store = InMemoryDialogueStore()
    controller = _two_transaction_controller(
        [_file_dispute(transaction=TransactionHint(merchant="Store Amazon"))],
        store=store,
        policy=policy,
        retriever=retriever,
    )

    controller.handle_turn(_turn("turn-0001", "primera"), principal=_principal())

    state = store.get(_SESSION_ID)
    assert state is not None
    assert state.selected_ref is None


def test_naming_a_different_merchant_while_one_is_presented_presents_that_one(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    selected, reply = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        TransactionHint(merchant="Netflix"),
        policy=policy,
        retriever=retriever,
    )

    assert selected == "TX-2"
    assert "Netflix" in reply
    assert "15,99" in reply


def test_naming_a_different_amount_while_one_is_presented_presents_that_one(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    selected, _ = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        TransactionHint(amount=Decimal("15.99")),
        policy=policy,
        retriever=retriever,
    )

    assert selected == "TX-2"


def test_naming_a_different_date_while_one_is_presented_presents_that_one(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    selected, _ = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        TransactionHint(date_on=date(2026, 6, 10), date_source=DateSource.ABSOLUTE),
        policy=policy,
        retriever=retriever,
    )

    assert selected == "TX-2"


@pytest.mark.parametrize(
    "second",
    [
        TransactionHint(),
        TransactionHint(merchant="amazon"),
        TransactionHint(merchant="Amazon", amount=Decimal("100.00"), currency="USD"),
        TransactionHint(date_on=_DOMAIN_DATE, date_source=DateSource.ABSOLUTE),
    ],
    ids=["nothing", "same-merchant", "same-merchant-and-amount", "same-date"],
)
def test_repeating_what_is_presented_keeps_it_selected(
    second: TransactionHint, policy: Policy, retriever: LexicalRetriever
) -> None:
    selected, reply = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"), second, policy=policy, retriever=retriever
    )

    assert selected == "TX-1"
    assert "Netflix" not in reply


def test_naming_something_that_matches_nothing_does_not_confirm_the_presented_one(
    policy: Policy, retriever: LexicalRetriever
) -> None:
    selected, _ = _selected_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        TransactionHint(merchant="Spotify"),
        policy=policy,
        retriever=retriever,
    )

    assert selected is None


@pytest.mark.parametrize(
    "unreadable",
    [ToolFailure(tool=ToolName.GET_TRANSACTION, cause="error"), None],
    ids=["tool-failure", "no-longer-found"],
)
def test_an_unreadable_presented_transaction_is_searched_for_again(
    unreadable: ToolFailure | None, policy: Policy, retriever: LexicalRetriever
) -> None:
    port = FakeToolPort(
        transactions=(_transaction("TX-1", merchant="Amazon"),),
        get_transaction_result=unreadable,
    )
    selected, response = _second_turn_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"),
        TransactionHint(merchant="Amazon"),
        policy=policy,
        retriever=retriever,
        port=port,
    )

    assert selected == "TX-1"
    assert response.next_expected is Slot.TRANSACTION_CHOICE


@pytest.mark.parametrize(
    "second",
    [
        TransactionHint(product_last4="9999"),
        TransactionHint(amount=Decimal("100.00"), currency="EUR"),
        TransactionHint(date_on=date(2026, 6, 10), date_source=DateSource.PARTIAL),
        TransactionHint(date_on=date(2026, 6, 10), date_source=DateSource.RELATIVE),
    ],
    ids=["other-card", "same-amount-other-currency", "partial-date", "relative-date"],
)
def test_naming_another_card_currency_or_date_does_not_confirm_the_presented_one(
    second: TransactionHint, policy: Policy, retriever: LexicalRetriever
) -> None:
    _, response = _second_turn_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"), second, policy=policy, retriever=retriever
    )

    assert response.next_expected is not Slot.REASON


@pytest.mark.parametrize(
    "second",
    [
        TransactionHint(product_last4="1234"),
        TransactionHint(amount=Decimal("100.00"), currency="USD"),
        TransactionHint(date_on=_DOMAIN_DATE, date_source=DateSource.PARTIAL),
        TransactionHint(date_on=_DOMAIN_DATE, date_source=DateSource.RELATIVE),
    ],
    ids=["same-card", "same-amount-and-currency", "same-partial-date", "same-relative-date"],
)
def test_naming_the_presented_card_amount_or_date_goes_ahead_with_it(
    second: TransactionHint, policy: Policy, retriever: LexicalRetriever
) -> None:
    selected, response = _second_turn_after_naming_then_naming_again(
        TransactionHint(merchant="Amazon"), second, policy=policy, retriever=retriever
    )

    assert selected == "TX-1"
    assert response.next_expected is Slot.REASON
