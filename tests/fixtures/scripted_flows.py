"""
Scripted Envelope Flows
=======================

Overview
--------
Recorded envelopes for the conversations the system is built and evaluated against: filing a
dispute, an ineligible request, a policy answer, an abstention, a refusal and a fraud handoff,
each in Spanish, Portuguese and English. Code that consumes envelopes (the renderer tests, the
evaluation harness) reads the JSON files written from these builders instead of building
envelopes by hand. The files hold full envelopes, agent-only detail included; a client reads
replies, not envelopes.

Scope
-----
In: the builders and the writer of ``scripted_flows.<lang>.json``.
Out: rendered replies; those come from the renderer.

Design Principles
-----------------
- One source: the JSON files are generated, and a test fails when a committed file differs from
  what the builders produce, so a fixture cannot drift from the contract.
- Synthetic content only: the merchant, amounts and identifiers are invented.

Runtime Contract
----------------
``flows(lang) -> dict[str, list[dict]]`` and ``python -m tests.fixtures.scripted_flows`` to
rewrite the files. ``all_envelopes()`` reads the three committed files back and yields every step
as a validated ``Envelope``, the one entry point other code (the model-renderer smoke test, a
future evaluation harness) should use rather than re-parsing the JSON files by hand.

Limitations
-----------
The flows cover the main paths, not every branch of the state machine.
"""

from __future__ import annotations

# Standard libraries
import json  # Fixture files
from collections.abc import Iterator  # Type of the committed-file reader
from datetime import date  # Absolute dates
from decimal import Decimal  # Money
from pathlib import Path  # Output location
from typing import Any  # Loosely typed JSON

# Local modules
from app.domain.policy.models import DisputeCategory, Outcome, ReasonCode, TransactionStatus
from contracts.service_v1.envelope import (
    LANGUAGES,
    AgentDecision,
    AgentOnly,
    CaseFact,
    CustomerReason,
    Decision,
    DisputeFacts,
    Envelope,
    Intent,
    Lang,
    LocalizedTitle,
    Money,
    PolicyValue,
    ProductLabel,
    Slot,
    SourceRef,
    TemplateId,
    TransactionFact,
    WindowFact,
)

DOMAIN_DATE = date(2026, 6, 18)
_DIRECTORY = Path(__file__).parent


def _transaction() -> TransactionFact:
    return TransactionFact(
        ref="tx-1001",
        occurred_on=date(2026, 6, 12),
        merchant="Tienda Sol",
        amount=Money(amount=Decimal("250.00"), currency="MXN"),
        product=ProductLabel(name="Visa Classic", last4="4321"),
        status=TransactionStatus.APPROVED,
    )


def _source() -> SourceRef:
    return SourceRef(
        section_id="filing-windows",
        titles=(
            LocalizedTitle(lang="es", text="Plazos para disputar"),
            LocalizedTitle(lang="pt", text="Prazos para contestar"),
            LocalizedTitle(lang="en", text="Filing windows"),
        ),
        corpus_version="2",
    )


def _eligible() -> Decision:
    return Decision(
        outcome=Outcome.ELIGIBLE,
        customer_reason=CustomerReason.ELIGIBLE,
        policy_version="2",
        requires_confirmation=True,
    )


def _envelope(lang: Lang, **changes: Any) -> Envelope:
    values: dict[str, Any] = {
        "session_id": "session-demo",
        "lang": lang,
        "domain_date": DOMAIN_DATE,
    }
    return Envelope(**{**values, **changes})


def _file_dispute(lang: Lang) -> list[Envelope]:
    transaction = _transaction()
    shown = DisputeFacts(transactions=(transaction,), candidate_count=1)
    selected = DisputeFacts(
        transactions=(transaction,),
        candidate_count=1,
        selected_ref=transaction.ref,
        category=DisputeCategory.UNRECOGNIZED_CHARGE,
    )
    case = CaseFact(
        case_number="D-2001",
        status="Open",
        filed_on=DOMAIN_DATE,
        transaction_ref=transaction.ref,
        expected_response_on=date(2026, 6, 25),
    )
    return [
        _envelope(
            lang,
            intent=Intent.CLARIFY,
            next_expected=Slot.TRANSACTION,
            template_id=TemplateId.CLARIFY_TRANSACTION,
        ),
        _envelope(
            lang,
            intent=Intent.PRESENT_TRANSACTIONS,
            next_expected=Slot.TRANSACTION_CHOICE,
            template_id=TemplateId.PRESENT_ONE,
            facts=shown,
        ),
        _envelope(
            lang,
            intent=Intent.CLARIFY,
            next_expected=Slot.REASON,
            template_id=TemplateId.CLARIFY_REASON,
            facts=shown,
        ),
        _envelope(
            lang,
            intent=Intent.CONFIRM_FILING,
            next_expected=Slot.CONFIRMATION,
            template_id=TemplateId.CONFIRM_FILING,
            facts=selected,
            decisions=(_eligible(),),
        ),
        _envelope(
            lang,
            intent=Intent.FILING_RESULT,
            template_id=TemplateId.FILING_RESULT,
            facts=DisputeFacts(cases=(case,), expected_response_on=case.expected_response_on),
            decisions=(_eligible(),),
        ),
        _envelope(lang, intent=Intent.FAREWELL, end_session=True, template_id=TemplateId.FAREWELL),
    ]


def _policy_answer(lang: Lang) -> list[Envelope]:
    return [
        _envelope(
            lang,
            intent=Intent.POLICY_ANSWER,
            template_id=TemplateId.POLICY_ANSWER,
            facts=DisputeFacts(policy_values=(PolicyValue(name="filing_window_days", value="60"),)),
            sources=(_source(),),
        )
    ]


def _abstain(lang: Lang) -> list[Envelope]:
    return [_envelope(lang, intent=Intent.ABSTAIN, template_id=TemplateId.ABSTAIN_POLICY)]


def _ineligible_window(lang: Lang) -> list[Envelope]:
    transaction = _transaction().model_copy(update={"occurred_on": date(2026, 2, 1)})
    return [
        _envelope(
            lang,
            intent=Intent.INELIGIBLE,
            template_id=TemplateId.INELIGIBLE,
            facts=DisputeFacts(
                transactions=(transaction,),
                candidate_count=1,
                selected_ref=transaction.ref,
                category=DisputeCategory.UNRECOGNIZED_CHARGE,
                window=WindowFact(days_allowed=60, age_days=137, deadline=date(2026, 4, 2)),
            ),
            decisions=(
                Decision(
                    outcome=Outcome.INELIGIBLE,
                    customer_reason=CustomerReason.WINDOW_EXPIRED,
                    policy_version="2",
                ),
            ),
            sources=(_source(),),
            agent_only=AgentOnly(
                decisions=(AgentDecision(reason_code=ReasonCode.FILING_WINDOW_EXPIRED),)
            ),
        )
    ]


def _refusal(lang: Lang) -> list[Envelope]:
    return [_envelope(lang, intent=Intent.REFUSE, template_id=TemplateId.REFUSE_UNSUPPORTED)]


def _fraud_handoff(lang: Lang) -> list[Envelope]:
    return [
        _envelope(
            lang,
            intent=Intent.HANDOFF,
            end_session=True,
            template_id=TemplateId.HANDOFF_FRAUD,
            facts=DisputeFacts(ticket_ref="T-100"),
            decisions=(
                Decision(
                    outcome=Outcome.ESCALATE,
                    customer_reason=CustomerReason.NEEDS_REVIEW,
                    policy_version="2",
                ),
            ),
            agent_only=AgentOnly(
                decisions=(
                    AgentDecision(
                        reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
                        triggers=(ReasonCode.ESCALATE_FRAUD_CLAIM,),
                    ),
                ),
                nlu_confidence=0.95,
            ),
        )
    ]


def flows(lang: Lang) -> dict[str, list[dict[str, Any]]]:
    """The scripted flows in ``lang``, each step as JSON-ready data."""
    built = {
        "file_dispute": _file_dispute(lang),
        "policy_answer": _policy_answer(lang),
        "abstain": _abstain(lang),
        "ineligible_window": _ineligible_window(lang),
        "refusal": _refusal(lang),
        "fraud_handoff": _fraud_handoff(lang),
    }
    return {
        name: [envelope.model_dump(mode="json") for envelope in steps]
        for name, steps in built.items()
    }


def render(lang: Lang) -> str:
    """The text of the fixture file for ``lang``."""
    return json.dumps(flows(lang), indent=2, ensure_ascii=False) + "\n"


def path_for(lang: Lang) -> Path:
    """Where the fixture file of ``lang`` lives."""
    return _DIRECTORY / f"scripted_flows.{lang}.json"


def all_envelopes() -> Iterator[tuple[Lang, str, Envelope]]:
    """Every step of every committed flow, as ``(language, flow name, envelope)``.

    Reads the committed JSON files back rather than the in-memory builders, so a caller also
    exercises the same file the "one source" check pins against — the same file a client, an
    evaluation harness or another consumer would read.
    """
    for lang in LANGUAGES:
        flows_in_lang = json.loads(path_for(lang).read_text(encoding="utf-8"))
        for name, steps in flows_in_lang.items():
            for step in steps:
                yield lang, name, Envelope.model_validate(step)


def main() -> None:
    """Rewrite every fixture file."""
    for lang in LANGUAGES:
        path_for(lang).write_text(render(lang), encoding="utf-8")


if __name__ == "__main__":
    main()
