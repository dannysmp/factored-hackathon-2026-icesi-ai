"""
Scoped Reads
============

Overview
--------
The Postgres-backed ``ToolPort``: the session customer's own transactions and cases, and a fresh
policy evaluation, with every call audited. The customer identifier is bound once, at
construction, exactly as ``ToolPort`` requires; no method here takes one, so a caller cannot ask
for anyone else's data by passing a different identifier.

Scope
-----
In: the five read/evaluate methods of ``ToolPort``, the cross-customer probe distinction issue
#73 asks for, auditing every call.
Out: ``create_dispute_case`` (slice 1.4), loading the policy file (``app.domain.policy.loader``,
supplied already loaded), deciding what a tool call means (the dialogue controller, stream 2).

Design Principles
-----------------
- **A foreign reference answers exactly like a missing one** (AC-E4-06): ``get_transaction`` and
  ``get_case`` look up a reference without scoping the query by customer, then decide in Python
  whether the row belongs to the session's customer; either way the caller gets ``None``, never a
  hint that the reference exists at all. The two cases are still told apart in the audit record
  (``transaction_probed``/``case_probed`` vs. ``transaction_viewed``/``case_viewed``, issue #73),
  since the trail is not the same audience as the response.
- **Fail closed on the audit write.** Every successful read is audited before it is returned;
  ``AuditSink.record`` raising propagates instead of being swallowed, so a customer never receives
  data whose access was not recorded (extending AC-E4-25's console rule to every read here). A
  read that itself fails at the store (``AC-E4-11``) is not audited: there was no completed action
  to record, only an infrastructure failure the caller already sees as a ``ToolFailure``.
- **No customer parameter anywhere** (AC-E4-07): every SQL statement here is written to be
  incapable of returning another customer's row by construction, not merely by a value a caller
  happens to pass correctly.
- **``evaluate_dispute`` has no not-found return.** Unlike the ``get_*`` methods, its contract
  offers only ``PolicyDecision | ToolFailure``; a transaction reference that does not exist or
  belongs to another customer is reported as ``ToolFailure(cause="error", retryable=False)`` —
  the same response either way (AC-E4-06 again), distinguished only in the audit record. This is
  a genuine design choice the contract's shape forces, not a `ToolFailure` in the usual
  store-failed sense; flagged for the architect's conformance note.

Runtime Contract
-----------------
``PostgresToolPort(dsn, audit, policy, *, customer_id, session_id, trace_id, domain_date, now)``
implements ``contracts.service_v1.tools.ToolPort``.

Limitations
-----------
One connection per call, matching this codebase's other persistence modules; no pooling yet
(see ``app.persistence.audit``'s own Limitations). ``is_repeat_complainer`` is always ``False``:
the serving store's ``customers`` table carries no such column (``pipelines.ops_seed`` computes
the fact only to select which customers the seed carries, not into a queryable field), so a
policy decision at this slice never routes on it in the running service — the seed's own
selection guarantees at least one repeat complainer exists to evaluate, but its evaluation
through this port cannot yet reflect the fact. ``risk_score`` is always ``None``: risk routing is
switched off in the shipped policy, and wiring the real risk-features lookup is out of scope
here. Both are flagged in the pull request for a decision on whether they need their own slice.
``description`` on every ``TransactionFact`` is always ``None``: the serving store carries no
separate description column, only ``merchant_name``.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # The audit record's tool-result hash
import json  # Canonical form of a result before hashing
import logging  # Progress events, never print
from collections.abc import Callable  # Type of the injected clock
from dataclasses import dataclass  # Immutable resolved-reference result
from datetime import date, datetime  # Domain date and the real instant of an audit record
from decimal import Decimal  # Money is never a float

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.domain.policy.engine import evaluate_dispute as _evaluate  # The one policy rule
from app.domain.policy.models import (  # Vocabulary shared with the policy engine
    DisputeCategory,
    DisputeRequest,
    Policy,
    PolicyDecision,
    ReasonCode,
)
from app.domain.policy.models import (
    TransactionStatus as PolicyTransactionStatus,
)
from contracts.service_v1.audit import AuditAction, AuditRecord, AuditSink  # Where every call goes
from contracts.service_v1.cases import AmountProvenance as ContractAmountProvenance
from contracts.service_v1.cases import (  # Shared record shapes
    CaseRecord,
    CaseStatus,
    DisclosedAmount,
    Money,
)
from contracts.service_v1.tools import (  # The port this module implements
    EvaluateDisputeRequest,
    ProductLabel,
    ToolFailure,
    TransactionFact,
    TransactionFilters,
    TransactionPage,
)
from contracts.service_v1.tools import Tool as ToolName

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5
Clock = Callable[[], datetime]

# Open-case statuses per CaseStatus (cases.py); a resolved or rejected case is not "open".
_OPEN_CASE_STATUSES = (CaseStatus.OPEN.value, CaseStatus.IN_REVIEW.value)


def _hash(payload: object) -> str:
    """SHA-256 of ``payload``'s canonical JSON form, for the audit record's ``tool_result_hash``.

    ``None`` hashes the same every time (the literal JSON ``null``), so a caller reading the
    audit trail can tell "no match" apart from a store failure (which is never audited at all)
    without needing the response body itself.
    """
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _ResolvedTransaction:
    """A transaction row, and whether it belongs to the customer who asked for it."""

    owned: bool
    transaction_id: str
    transaction_date: datetime
    transaction_type: str | None
    merchant_name: str | None
    amount: Decimal
    currency: str
    amount_usd: Decimal | None
    amount_usd_provenance: str
    transaction_status: str
    product_type: str | None
    last4: str


@dataclass(frozen=True, slots=True)
class _ResolvedCase:
    """A case row, and whether it belongs to the customer who asked for it."""

    owned: bool
    case_number: str
    status: str
    transaction_id: str
    category: str
    amount: Decimal | None
    currency: str | None
    amount_provenance: str
    domain_date: date
    expected_first_response_date: date
    created_at_utc: datetime
    policy_version: str
    reason_code: str
    language: str


class PostgresToolPort:
    """The session customer's own reads and dispute evaluation, backed by the serving store."""

    def __init__(
        self,
        dsn: str,
        audit: AuditSink,
        policy: Policy,
        *,
        customer_id: str,
        session_id: str,
        trace_id: str,
        domain_date: date,
        now: Clock,
    ) -> None:
        self._dsn = dsn
        self._audit = audit
        self._policy = policy
        self._customer_id = customer_id
        self._session_id = session_id
        self._trace_id = trace_id
        self._domain_date = domain_date
        self._now = now

    # -------------------------------------------------------------------------------------
    # Audit
    # -------------------------------------------------------------------------------------

    def _write_audit(
        self,
        action: AuditAction,
        result: object,
        *,
        reason_code: ReasonCode | None = None,
        policy_version: str | None = None,
    ) -> None:
        """Write one audit record; propagates whatever ``AuditSink.record`` raises (fail closed)."""
        self._audit.record(
            AuditRecord(
                trace_id=self._trace_id,
                customer_id=self._customer_id,
                session_id=self._session_id,
                action=action,
                reason_code=reason_code,
                policy_version=policy_version,
                tool_result_hash=_hash(result),
                occurred_at=self._now(),
                domain_date=self._domain_date,
            )
        )

    # -------------------------------------------------------------------------------------
    # Transactions
    # -------------------------------------------------------------------------------------

    def _disclosed_amount(self, amount_usd: Decimal | None, provenance: str) -> DisclosedAmount:
        money = None if amount_usd is None else Money(amount=amount_usd, currency="USD")
        return DisclosedAmount(money=money, provenance=ContractAmountProvenance(provenance))

    def _transaction_fact(self, row: _ResolvedTransaction) -> TransactionFact:
        return TransactionFact(
            ref=row.transaction_id,
            occurred_on=row.transaction_date.date(),
            merchant=row.merchant_name,
            description=None,
            amount=self._disclosed_amount(row.amount_usd, row.amount_usd_provenance),
            product=ProductLabel(name=row.product_type or "unknown", last4=row.last4),
            status=PolicyTransactionStatus(row.transaction_status),
        )

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        """The session customer's own transactions matching ``filters``, most recent first."""
        try:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                cur.execute(
                    """
                    SELECT t.transaction_id, t.transaction_date, t.merchant_name, t.amount,
                           t.currency, t.amount_usd, t.amount_usd_provenance, t.transaction_status,
                           p.product_type, p.last4, count(*) OVER () AS total_count
                    FROM transactions AS t
                    JOIN products AS p ON p.product_id = t.product_id
                    WHERE t.customer_id = %(customer_id)s
                      AND (%(since)s::date IS NULL OR t.transaction_date::date >= %(since)s)
                      AND (%(until)s::date IS NULL OR t.transaction_date::date <= %(until)s)
                    ORDER BY t.transaction_date DESC
                    LIMIT 5
                    """,
                    {
                        "customer_id": self._customer_id,
                        "since": filters.since,
                        "until": filters.until,
                    },
                )
                rows = cur.fetchall()
        except psycopg.Error:
            logger.warning("list_transactions_failed")
            return ToolFailure(tool=ToolName.LIST_TRANSACTIONS, cause="error")

        total_count = rows[0][10] if rows else 0
        items = tuple(
            self._transaction_fact(
                _ResolvedTransaction(
                    owned=True,
                    transaction_id=r[0],
                    transaction_date=r[1],
                    transaction_type=None,
                    merchant_name=r[2],
                    amount=r[3],
                    currency=r[4],
                    amount_usd=r[5],
                    amount_usd_provenance=r[6],
                    transaction_status=r[7],
                    product_type=r[8],
                    last4=r[9],
                )
            )
            for r in rows
        )
        page = TransactionPage(items=items, total_count=total_count)
        self._write_audit(AuditAction.TRANSACTIONS_LISTED, page.model_dump(mode="json"))
        return page

    def _resolve_transaction(self, ref: str) -> _ResolvedTransaction | None:
        """The transaction ``ref`` anywhere in the store, or ``None`` if it does not exist."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """
                SELECT t.customer_id, t.transaction_id, t.transaction_date, t.transaction_type,
                       t.merchant_name, t.amount, t.currency, t.amount_usd,
                       t.amount_usd_provenance, t.transaction_status, p.product_type, p.last4
                FROM transactions AS t
                JOIN products AS p ON p.product_id = t.product_id
                WHERE t.transaction_id = %s
                """,
                (ref,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _ResolvedTransaction(
            owned=row[0] == self._customer_id,
            transaction_id=row[1],
            transaction_date=row[2],
            transaction_type=row[3],
            merchant_name=row[4],
            amount=row[5],
            currency=row[6],
            amount_usd=row[7],
            amount_usd_provenance=row[8],
            transaction_status=row[9],
            product_type=row[10],
            last4=row[11],
        )

    def get_transaction(self, ref: str) -> TransactionFact | ToolFailure | None:
        """The session customer's own transaction ``ref``, or ``None`` if there is no match."""
        try:
            resolved = self._resolve_transaction(ref)
        except psycopg.Error:
            logger.warning("get_transaction_failed")
            return ToolFailure(tool=ToolName.GET_TRANSACTION, cause="error")
        if resolved is None:
            self._write_audit(AuditAction.TRANSACTION_VIEWED, None)
            return None
        if not resolved.owned:
            self._write_audit(AuditAction.TRANSACTION_PROBED, None)
            return None
        fact = self._transaction_fact(resolved)
        self._write_audit(AuditAction.TRANSACTION_VIEWED, fact.model_dump(mode="json"))
        return fact

    # -------------------------------------------------------------------------------------
    # Cases
    # -------------------------------------------------------------------------------------

    def _case_record(self, row: _ResolvedCase) -> CaseRecord:
        return CaseRecord(
            case_number=row.case_number,
            status=CaseStatus(row.status),
            transaction_ref=row.transaction_id,
            category=DisputeCategory(row.category),
            amount=self._disclosed_amount(row.amount, row.amount_provenance),
            domain_date=row.domain_date,
            expected_first_response_date=row.expected_first_response_date,
            created_at_utc=row.created_at_utc,
            policy_version=row.policy_version,
            reason_code=ReasonCode(row.reason_code),
            language=row.language,
        )

    def list_dispute_cases(self) -> tuple[CaseRecord, ...] | ToolFailure:
        """Every dispute case the session customer has filed."""
        try:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                cur.execute(
                    """
                    SELECT case_number, status, transaction_id, category, amount, currency,
                           amount_provenance, domain_date, expected_first_response_date,
                           created_at_utc, policy_version, reason_code, language
                    FROM cases WHERE customer_id = %s ORDER BY created_at_utc DESC
                    """,
                    (self._customer_id,),
                )
                rows = cur.fetchall()
        except psycopg.Error:
            logger.warning("list_dispute_cases_failed")
            return ToolFailure(tool=ToolName.LIST_DISPUTE_CASES, cause="error")

        cases = tuple(
            self._case_record(
                _ResolvedCase(
                    owned=True,
                    case_number=r[0],
                    status=r[1],
                    transaction_id=r[2],
                    category=r[3],
                    amount=r[4],
                    currency=r[5],
                    amount_provenance=r[6],
                    domain_date=r[7],
                    expected_first_response_date=r[8],
                    created_at_utc=r[9],
                    policy_version=r[10],
                    reason_code=r[11],
                    language=r[12],
                )
            )
            for r in rows
        )
        self._write_audit(
            AuditAction.CASES_LISTED, [case.model_dump(mode="json") for case in cases]
        )
        return cases

    def _resolve_case(self, case_number: str) -> _ResolvedCase | None:
        """The case ``case_number`` anywhere in the store, or ``None`` if it does not exist."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                """
                SELECT customer_id, case_number, status, transaction_id, category, amount,
                       currency, amount_provenance, domain_date, expected_first_response_date,
                       created_at_utc, policy_version, reason_code, language
                FROM cases WHERE case_number = %s
                """,
                (case_number,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _ResolvedCase(
            owned=row[0] == self._customer_id,
            case_number=row[1],
            status=row[2],
            transaction_id=row[3],
            category=row[4],
            amount=row[5],
            currency=row[6],
            amount_provenance=row[7],
            domain_date=row[8],
            expected_first_response_date=row[9],
            created_at_utc=row[10],
            policy_version=row[11],
            reason_code=row[12],
            language=row[13],
        )

    def get_case(self, case_number: str) -> CaseRecord | ToolFailure | None:
        """The session customer's own case ``case_number``, or ``None`` if there is no match."""
        try:
            resolved = self._resolve_case(case_number)
        except psycopg.Error:
            logger.warning("get_case_failed")
            return ToolFailure(tool=ToolName.GET_CASE, cause="error")
        if resolved is None:
            self._write_audit(AuditAction.CASE_VIEWED, None)
            return None
        if not resolved.owned:
            self._write_audit(AuditAction.CASE_PROBED, None)
            return None
        record = self._case_record(resolved)
        self._write_audit(AuditAction.CASE_VIEWED, record.model_dump(mode="json"))
        return record

    # -------------------------------------------------------------------------------------
    # Evaluation
    # -------------------------------------------------------------------------------------

    def _has_open_case_for(self, transaction_ref: str) -> bool:
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM cases WHERE transaction_id = %s "
                "AND customer_id = %s AND status = ANY(%s))",
                (transaction_ref, self._customer_id, list(_OPEN_CASE_STATUSES)),
            )
            (exists,) = cur.fetchone() or (False,)
        return bool(exists)

    def evaluate_dispute(self, request: EvaluateDisputeRequest) -> PolicyDecision | ToolFailure:
        """The policy decision for ``request``, computed fresh; no side effect.

        A ``transaction_ref`` that does not exist or belongs to another customer answers with
        the same ``ToolFailure`` either way (AC-E4-06); see the module's Design Principles.
        """
        try:
            resolved = self._resolve_transaction(request.transaction_ref)
            has_open_case = (
                self._has_open_case_for(request.transaction_ref) if resolved is not None else False
            )
        except psycopg.Error:
            logger.warning("evaluate_dispute_failed")
            return ToolFailure(tool=ToolName.EVALUATE_DISPUTE, cause="error")

        if resolved is None:
            self._write_audit(AuditAction.TRANSACTION_VIEWED, None)
            return ToolFailure(tool=ToolName.EVALUATE_DISPUTE, cause="error", retryable=False)
        if not resolved.owned:
            self._write_audit(AuditAction.TRANSACTION_PROBED, None)
            return ToolFailure(tool=ToolName.EVALUATE_DISPUTE, cause="error", retryable=False)

        amount_usd = (
            None
            if resolved.amount_usd_provenance == ContractAmountProvenance.UNKNOWN.value
            else resolved.amount_usd
        )
        dispute_request = DisputeRequest(
            category=request.category,
            transaction_date=resolved.transaction_date.date(),
            transaction_status=PolicyTransactionStatus(resolved.transaction_status),
            transaction_type=resolved.transaction_type or "",
            product_type=resolved.product_type or "",
            amount_usd=amount_usd,
            nlu_confidence=1.0,
            is_repeat_complainer=False,
            has_open_case_for_transaction=has_open_case,
            risk_score=None,
        )
        decision = _evaluate(dispute_request, self._policy, today=self._domain_date)
        self._write_audit(
            AuditAction.DISPUTE_EVALUATED,
            decision.model_dump(mode="json"),
            reason_code=decision.reason_code,
            policy_version=decision.policy_version,
        )
        return decision
