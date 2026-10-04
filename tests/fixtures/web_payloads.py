"""
Backend Payloads for the Web Client
===================================

Overview
--------
Builds, from the service's real response models, one representative wire payload for every
response the web client parses with a strict schema, and writes them to the JSON file the web
tests read. The web tests parse each payload with the client's own schema, so a field the backend
adds or renames fails there; a Python test fails when the committed file no longer matches what the
models produce, so the file cannot go stale.

Regenerate with ``uv run python -m tests.fixtures.web_payloads`` (the path is fixed below).
"""

from __future__ import annotations

# Standard libraries
import json  # The committed fixture format
from datetime import UTC, date, datetime  # Fixed instants: the payloads must be reproducible
from pathlib import Path  # Where the web tests read the payloads from

# Local modules
from app.api.auth import SessionResponse
from app.api.demo_signin import DemoPersonaDirectory, DemoPersonaSummary
from app.domain.policy.models import DisputeCategory, ReasonCode, TransactionStatus
from contracts.service_v1.api import Choice, ReferenceDateOrigin, TurnResponse
from contracts.service_v1.console import (
    Note,
    QueueItem,
    QueueResponse,
    TicketDetail,
    TicketStatus,
    TimelineEntry,
)
from contracts.service_v1.envelope import (
    Intent,
    LocalizedTitle,
    Money,
    ProductLabel,
    RiskEvidence,
    Slot,
    SourceRef,
    TransactionFact,
)
from contracts.service_v1.handoff import (
    ActionRecord,
    CustomerLabel,
    Evidence,
    HandoffPacket,
    HandoffTrigger,
    OpenQuestion,
)

PAYLOADS_PATH = Path(__file__).resolve().parents[2] / "web" / "test" / "backend-payloads.json"

_CREATED = datetime(2026, 6, 18, 14, 5, tzinfo=UTC)
_TODAY = date(2026, 6, 18)


def _item(
    ticket_ref: str, trigger: HandoffTrigger, *, claimed_by: str | None, priority: bool
) -> QueueItem:
    return QueueItem(
        ticket_ref=ticket_ref,
        trigger=trigger,
        language="pt",
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
        status=TicketStatus.IN_REVIEW if claimed_by else TicketStatus.OPEN,
        created_at=_CREATED,
        reference_date=_TODAY,
        promised_contact_by=date(2026, 6, 19),
        age_days=0,
        priority=priority,
        claimed_by=claimed_by,
    )


def _packet(item: QueueItem) -> HandoffPacket:
    return HandoffPacket(
        ticket_ref=item.ticket_ref,
        reference_date=item.reference_date,
        created_at=item.created_at,
        language=item.language,
        needs_language_routing=True,
        trigger=item.trigger,
        customer=CustomerLabel(first_name="Ana", masked_id="****34"),
        category=item.category,
        request_summary="The customer reported a charge they do not recognize.",
        verified_facts=(
            TransactionFact(
                ref="TX-1",
                occurred_on=_TODAY,
                merchant="Farmacia Salud",
                amount=Money(amount="250.00", currency="USD"),
                product=ProductLabel(name="Visa Gold", last4="1234"),
                status=TransactionStatus.APPROVED,
            ),
        ),
        actions=(ActionRecord(action="evaluate_dispute", result="escalate"),),
        attempted_action=ActionRecord(action="create_dispute_case", result="not_attempted"),
        existing_case_number="D-1",
        evidence=Evidence(
            reason_codes=(ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,),
            policy_version="2",
            sources=(
                SourceRef(
                    section_id="window.30d",
                    titles=(
                        LocalizedTitle(lang="es", text="Plazo"),
                        LocalizedTitle(lang="pt", text="Prazo"),
                        LocalizedTitle(lang="en", text="Window"),
                    ),
                    corpus_version="1",
                ),
            ),
            risk=RiskEvidence(
                score=0.31, interval_low=0.2, interval_high=0.4, base_rate=0.05, threshold=0.5
            ),
        ),
        open_questions=(OpenQuestion(slot=Slot.REASON, attempts=2),),
    )


def build_payloads() -> dict[str, object]:
    """Every payload keyed by the web schema that parses it."""
    unclaimed = _item(
        "T-20260618-AAAAAAAA", HandoffTrigger.FRAUD_REPORT, claimed_by=None, priority=True
    )
    claimed = _item(
        "T-20260618-BBBBBBBB", HandoffTrigger.LOW_UNDERSTANDING, claimed_by="AGT-1", priority=False
    )
    detail = TicketDetail(
        item=claimed,
        packet=_packet(claimed),
        timeline=(
            TimelineEntry(
                occurred_at=_CREATED,
                trace_id="trace-1",
                intent=Intent.HANDOFF,
                state_before="clarifying",
                state_after="handed_off",
                render_mode="template",
                reason_code=ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE,
                policy_version="2",
            ),
        ),
        notes=(Note(agent_id="AGT-1", note_text="Called the customer back.", created_at=_CREATED),),
    )
    turn = TurnResponse(
        turn_id="turn-0001",
        conversation_id="conv-1",
        state_version=2,
        lang="es",
        reply="¿Es esta la transacción?",
        reference_date_line="Fecha de referencia: 18 de junio de 2026.",
        demo_notice="Demostración.",
        choices=(Choice(number=1, label="Amazon"),),
        next_expected=Slot.TRANSACTION_CHOICE,
        end_session=False,
        handoff_ticket="T-20260618-AAAAAAAA",
    )
    models: dict[str, object] = {
        "QueueResponse": QueueResponse(
            reference_date=_TODAY,
            reference_date_origin=ReferenceDateOrigin.SETTING,
            items=(unclaimed, claimed),
        ),
        "TicketDetail": detail,
        "TurnResponse": turn,
        "DemoPersonaDirectory": DemoPersonaDirectory(
            personas=(
                DemoPersonaSummary(
                    slug="ana", display_name="Ana", language="es", audience="customer"
                ),
                DemoPersonaSummary(
                    slug="agent-beatriz", display_name="Beatriz", language="pt", audience="agent"
                ),
            )
        ),
        "SessionResponse": SessionResponse(
            access_token="token", expires_at=_CREATED, expires_in=1800
        ),
    }
    return {
        name: model.model_dump(mode="json")  # type: ignore[attr-defined]
        for name, model in models.items()
    }


def render_payloads() -> str:
    """The committed file's exact text."""
    return json.dumps(build_payloads(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


if __name__ == "__main__":
    PAYLOADS_PATH.write_text(render_payloads(), encoding="utf-8")
