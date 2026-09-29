"""
Scoped Reads
============

Overview
--------
The Postgres-backed ``ToolPort``: the session customer's own transactions and cases, a fresh
policy evaluation, and filing a case, with every call audited. The customer identifier is bound
once, at construction, exactly as ``ToolPort`` requires; no method here takes one, so a caller
cannot ask for anyone else's data by passing a different identifier.

Scope
-----
In: all six methods of ``ToolPort``, the cross-customer probe distinction issue #73 asks for,
auditing every call, the create tool's own permission invariants (ADR-3).
Out: loading the policy file (``app.domain.policy.loader``, supplied already loaded), evaluating
policy inside the create tool (the controller's job, never this module's, per ADR-3), deciding
what a tool call means (the dialogue controller, stream 2).

Design Principles
-----------------
- **A foreign reference answers exactly like a missing one** (AC-E4-06): ``get_transaction`` and
  ``get_case`` look up a reference without scoping the query by customer, then decide in Python
  whether the row belongs to the session's customer; either way the caller gets ``None``, never a
  hint that the reference exists at all. The two cases are still told apart in the audit record
  (``transaction_probed``/``case_probed`` vs. ``transaction_viewed``/``case_viewed``, issue #73),
  since the trail is not the same audience as the response.
- **Fail closed on the audit write.** Every successful read or write is audited before it is
  returned; ``AuditSink.record`` raising propagates instead of being swallowed, so a customer
  never receives data, or an outcome, whose access or filing was not recorded (extending
  AC-E4-25's console rule to every call here). A call that itself fails at the store
  (``AC-E4-11``) is not audited: there was no completed action to record, only an infrastructure
  failure the caller already sees as a ``ToolFailure``.
- **No customer parameter anywhere** (AC-E4-07): every SQL statement here is written to be
  incapable of returning or changing another customer's row by construction, not merely by a
  value a caller happens to pass correctly.
- **``evaluate_dispute`` has no not-found return.** Unlike the ``get_*`` methods, its contract
  offers only ``PolicyDecision | ToolFailure``; a transaction reference that does not exist or
  belongs to another customer is reported as ``ToolFailure(cause="error", retryable=False)`` —
  the same response either way (AC-E4-06 again), distinguished only in the audit record. This is
  a genuine design choice the contract's shape forces, not a `ToolFailure` in the usual
  store-failed sense; flagged for the architect's conformance note.
- **The create tool enforces permission invariants only, never policy** (ADR-3):
  ``confirmation_required``, ``confirmation_mismatch``, ``idempotency_conflict``,
  ``duplicate_open_case`` and ``session_cap_reached`` are all this tool can verify itself; a
  request the policy would refuse is fail-closed instead on ``decision_missing`` (no decision was
  passed at all), never re-evaluated. ``PolicyDecision`` carries ``transaction_ref`` and
  ``category`` (added in this slice) precisely so ``confirmation_mismatch`` (AC-E4-14) can compare
  a filing call against what was actually decided, without trusting the caller and without
  re-running the policy.
- **Idempotency: a proactive check for the ordinary sequential replay, the unique constraint for
  the true race** (AC-E4-15). A lookup by ``(customer_id, idempotency_key)`` before the insert
  handles a call repeated in sequence, including the case where an earlier call's own case is now
  the "open case" a naive duplicate-open-case check would otherwise wrongly refuse; the insert
  itself still relies on ``cases_customer_idempotency_key_unique`` (migration 0001) and
  ``cases_transaction_id_open_unique`` (migration 0004) to resolve two calls arriving at the same
  moment, exactly as a check-then-insert without that fallback could not.
- **Never trusts caller-stated fields** (AC-E4-13): the transaction is re-resolved from the store
  inside ``create_dispute_case`` itself, exactly as ``evaluate_dispute`` does, and the amount,
  currency and provenance stored on the case row come from that fresh read, never from
  ``request`` or from the (possibly stale) ``decision`` object.
- **A ``duplicate_open_case`` refusal names the case it collided with** (AC-E4-16):
  ``CreateDisputeCaseResult.existing_case_number`` is looked up fresh, both on the proactive path
  (``_creation_limit_refusal``) and on the store-level race (``_insert_case``'s
  ``UniqueViolation`` handler) — never trusted from an earlier read.
- **A replay is its own audit record, not only a log line** (AC-E4-15): ``_replay_or_conflict``
  writes ``AuditAction.CASE_CREATION_REPLAYED``, a compatible addition after this contract froze,
  the same way ``TRANSACTION_PROBED``/``CASE_PROBED`` were — so a trace built from audit records
  alone accounts for every filing call the customer actually made, including a repeat.

Runtime Contract
-----------------
``PostgresToolPort(dsn, audit, policy, *, customer_id, session_id, trace_id, domain_date, now,
language, case_create_session_cap)`` implements ``contracts.service_v1.tools.ToolPort``.
``language`` and ``case_create_session_cap`` are bound at construction like every other
session-scoped fact this class holds: ``CreateDisputeCaseRequest`` (``contracts.service_v1.tools``)
has no field for either, since a case's language is a fact of the session filing it, not of one
call, and the cap is a permission invariant this tool enforces itself, not caller-supplied data.

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
separate description column, only ``merchant_name``. The case-insert transaction and the audit
write are two separate store connections, not one atomic transaction (matching
``app.persistence.audit``'s own one-connection-per-call design): a process crash in the narrow
window after the audit write commits but before the case insert's own connection commits could
leave an audit record for a case that does not exist; there is no cross-connection two-phase
commit in this codebase to close that window.
"""

from __future__ import annotations

# Standard libraries
import hashlib  # The audit record's tool-result hash
import json  # Canonical form of a result before hashing
import logging  # Progress events, never print
import secrets  # Unguessable suffix of a generated case number
from collections.abc import Callable  # Type of the injected clock
from dataclasses import dataclass  # Immutable resolved-reference result
from datetime import date, datetime  # Domain date and the real instant of an audit record
from decimal import Decimal  # Money is never a float

# Third-party libraries
import psycopg  # Serving-store driver
import psycopg.errors  # Distinguishing a unique-constraint race from any other store failure

# Local modules
from app.domain.policy.engine import evaluate_dispute as _evaluate  # The one policy rule
from app.domain.policy.engine import expected_first_response  # Case's own first-response date
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
from app.security.middleware import current_request_id  # Correlates a failure log to its request
from contracts.service_v1.audit import AuditAction, AuditRecord, AuditSink  # Where every call goes
from contracts.service_v1.cases import AmountProvenance as ContractAmountProvenance
from contracts.service_v1.cases import (  # Shared record shapes
    CaseRecord,
    CaseStatus,
    DisclosedAmount,
    Lang,
    Money,
)
from contracts.service_v1.tools import (  # The port this module implements
    CreateDisputeCaseRequest,
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

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5
Clock = Callable[[], datetime]

# Open-case statuses per CaseStatus (cases.py); a resolved or rejected case is not "open".
_OPEN_CASE_STATUSES = (CaseStatus.OPEN.value, CaseStatus.IN_REVIEW.value)

# Named at the store (migration 0004); read here to tell a lost idempotency race from a lost
# duplicate-open-case race without guessing at a generic unique-violation's own message text.
_IDEMPOTENCY_CONSTRAINT = "cases_customer_idempotency_key_unique"
_OPEN_CASE_CONSTRAINT = "cases_transaction_id_open_unique"


def _new_case_number(domain_date: date) -> str:
    """A short, readable case number: what a customer quotes on the phone (E4-F3)."""
    return f"CASE-{domain_date:%Y%m%d}-{secrets.token_hex(4).upper()}"


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


@dataclass(frozen=True, slots=True)
class _ExistingCase:
    """The case already on file for a customer's idempotency key, if any."""

    case_number: str
    transaction_id: str
    category: str


class PostgresToolPort:
    """The session customer's own reads, dispute evaluation and filing, backed by the store."""

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
        language: Lang,
        case_create_session_cap: int,
    ) -> None:
        self._dsn = dsn
        self._audit = audit
        self._policy = policy
        self._customer_id = customer_id
        self._session_id = session_id
        self._trace_id = trace_id
        self._domain_date = domain_date
        self._now = now
        self._language = language
        self._case_create_session_cap = case_create_session_cap

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

    def _log_failure(self, event: str) -> None:
        """A store-failure log line, correlated to its conversation trace and its HTTP request
        (SECURITY.md: every operational log line carries both)."""
        logger.warning("%s trace_id=%s request_id=%s", event, self._trace_id, current_request_id())

    def _log_replay(self, case_number: str, idempotency_key: str) -> None:
        """A replayed filing call (AC-E4-15), for immediate operational grep alongside its own
        ``case_creation_replayed`` audit record (``_replay_or_conflict``)."""
        logger.info(
            "case_replay trace_id=%s request_id=%s session_id=%s case_number=%s idempotency_key=%s",
            self._trace_id,
            current_request_id(),
            self._session_id,
            case_number,
            idempotency_key,
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
            self._log_failure("list_transactions_failed")
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
            self._log_failure("get_transaction_failed")
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
            self._log_failure("list_dispute_cases_failed")
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
            self._log_failure("get_case_failed")
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

    def _open_case_number_for(self, transaction_ref: str) -> str | None:
        """The number of an open case already on file for ``transaction_ref``, if any."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT case_number FROM cases WHERE transaction_id = %s "
                "AND customer_id = %s AND status = ANY(%s) LIMIT 1",
                (transaction_ref, self._customer_id, list(_OPEN_CASE_STATUSES)),
            )
            row = cur.fetchone()
        return row[0] if row is not None else None

    def evaluate_dispute(
        self, request: EvaluateDisputeRequest
    ) -> PolicyDecision | ToolFailure | None:
        """The policy decision for ``request``, computed fresh; no side effect.

        A ``transaction_ref`` that does not exist or belongs to another customer answers with
        the same ``None`` either way (AC-E4-06); see the module's Design Principles. ``None`` is a
        normal matchless result, never a ``ToolFailure`` — reserved for what the store itself
        could not do.
        """
        try:
            resolved = self._resolve_transaction(request.transaction_ref)
        except psycopg.Error:
            self._log_failure("evaluate_dispute_failed")
            return ToolFailure(tool=ToolName.EVALUATE_DISPUTE, cause="error")

        # Resolved but not owned by this session's customer, and resolved to nothing at all,
        # must cost the same number of store round-trips: a query run only for an owned
        # transaction is a timing side-channel that tells a caller a foreign reference exists,
        # even though the response body is identical either way (AC-E4-06, issue #73). That
        # symmetry rests on the shared code path above, not on which value each branch returns.
        if resolved is None:
            self._write_audit(AuditAction.TRANSACTION_VIEWED, None)
            return None
        if not resolved.owned:
            self._write_audit(AuditAction.TRANSACTION_PROBED, None)
            return None

        try:
            has_open_case = self._open_case_number_for(request.transaction_ref) is not None
        except psycopg.Error:
            self._log_failure("evaluate_dispute_failed")
            return ToolFailure(tool=ToolName.EVALUATE_DISPUTE, cause="error")

        amount_usd = (
            None
            if resolved.amount_usd_provenance == ContractAmountProvenance.UNKNOWN.value
            else resolved.amount_usd
        )
        dispute_request = DisputeRequest(
            transaction_ref=request.transaction_ref,
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

    # -------------------------------------------------------------------------------------
    # Case creation (ADR-3): permission invariants only, never policy.
    # -------------------------------------------------------------------------------------

    def _refuse(
        self,
        refusal: ToolRefusalCode,
        decision: PolicyDecision,
        *,
        existing_case_number: str | None = None,
    ) -> CreateDisputeCaseResult:
        """Audit and return a permission refusal; ``AuditSink.record`` raising still propagates."""
        result = CreateDisputeCaseResult(
            created=False, refusal=refusal, existing_case_number=existing_case_number
        )
        self._write_audit(
            AuditAction.CASE_CREATION_REFUSED,
            result.model_dump(mode="json"),
            reason_code=decision.reason_code,
            policy_version=decision.policy_version,
        )
        return result

    def _find_by_idempotency_key(self, idempotency_key: str) -> _ExistingCase | None:
        """The case already on file for this customer's ``idempotency_key``, if any."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT case_number, transaction_id, category FROM cases "
                "WHERE customer_id = %s AND idempotency_key = %s",
                (self._customer_id, idempotency_key),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return _ExistingCase(case_number=row[0], transaction_id=row[1], category=row[2])

    def _replay_or_conflict(
        self,
        existing: _ExistingCase,
        request: CreateDisputeCaseRequest,
        decision: PolicyDecision,
    ) -> CreateDisputeCaseResult:
        """The same key with the same payload replays; a different payload is a real conflict."""
        if (
            existing.transaction_id == request.transaction_ref
            and existing.category == request.category.value
        ):
            self._log_replay(existing.case_number, request.idempotency_key)
            result = CreateDisputeCaseResult(created=True, case_number=existing.case_number)
            self._write_audit(
                AuditAction.CASE_CREATION_REPLAYED,
                result.model_dump(mode="json"),
                reason_code=decision.reason_code,
                policy_version=decision.policy_version,
            )
            return result
        return self._refuse(ToolRefusalCode.IDEMPOTENCY_CONFLICT, decision)

    def _session_case_count(self) -> int:
        """How many cases this session has already filed, of any status."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT count(*) FROM cases WHERE customer_id = %s AND session_id = %s",
                (self._customer_id, self._session_id),
            )
            (count,) = cur.fetchone() or (0,)
        return int(count)

    def create_dispute_case(
        self, request: CreateDisputeCaseRequest
    ) -> CreateDisputeCaseResult | ToolFailure:
        """File a case, or refuse for a permission reason; never a policy reason (ADR-3).

        A ``ToolFailure`` covers what neither party to the decision controls: the store cannot be
        reached, or the audit record for the filing cannot be written. Either fails the filing
        closed — no case is created uncounted, and the customer is told it could not be
        completed, never that it succeeded (AC-E4-19).
        """
        validated = self._validate_permission(request)
        if isinstance(validated, CreateDisputeCaseResult):
            return validated
        decision = validated

        try:
            existing = self._find_by_idempotency_key(request.idempotency_key)
        except psycopg.Error:
            self._log_failure("create_dispute_case_failed")
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error")
        if existing is not None:
            return self._replay_or_conflict(existing, request, decision)

        resolved = self._resolve_owned_transaction_or_failure(request.transaction_ref)
        if isinstance(resolved, ToolFailure):
            return resolved

        limit_refusal = self._creation_limit_refusal(request.transaction_ref, decision)
        if limit_refusal is not None:
            return limit_refusal

        return self._insert_case(request, resolved, decision)

    def _validate_permission(
        self, request: CreateDisputeCaseRequest
    ) -> PolicyDecision | CreateDisputeCaseResult:
        """The request's own decision, once confirmed and matching; otherwise the refusal itself.

        No store access: every check here reads only the request and the decision it carries.
        """
        decision = request.decision
        if decision is None:
            result = CreateDisputeCaseResult(
                created=False, refusal=ToolRefusalCode.DECISION_MISSING
            )
            self._write_audit(AuditAction.CASE_CREATION_REFUSED, result.model_dump(mode="json"))
            return result

        if not request.confirmed:
            return self._refuse(ToolRefusalCode.CONFIRMATION_REQUIRED, decision)

        if (
            decision.transaction_ref != request.transaction_ref
            or decision.category != request.category
        ):
            return self._refuse(ToolRefusalCode.CONFIRMATION_MISMATCH, decision)

        return decision

    def _resolve_owned_transaction_or_failure(
        self, transaction_ref: str
    ) -> _ResolvedTransaction | ToolFailure:
        """The owned, resolved transaction for a filing call, or a fail-closed ``ToolFailure``.

        Defensive only: unreachable while every caller evaluates the dispute (which already
        requires an owned, resolved transaction) immediately before filing it, in the same
        request. Audited the same way ``evaluate_dispute`` audits it, on the chance this ever
        changes.
        """
        try:
            resolved = self._resolve_transaction(transaction_ref)
        except psycopg.Error:
            self._log_failure("create_dispute_case_failed")
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error")
        if resolved is None:
            self._write_audit(AuditAction.TRANSACTION_VIEWED, None)
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error", retryable=False)
        if not resolved.owned:
            self._write_audit(AuditAction.TRANSACTION_PROBED, None)
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error", retryable=False)
        return resolved

    def _creation_limit_refusal(
        self, transaction_ref: str, decision: PolicyDecision
    ) -> CreateDisputeCaseResult | ToolFailure | None:
        """A refusal for the duplicate-open-case or session-cap invariant; ``None`` to proceed."""
        try:
            open_case_number = self._open_case_number_for(transaction_ref)
        except psycopg.Error:
            self._log_failure("create_dispute_case_failed")
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error")
        if open_case_number is not None:
            return self._refuse(
                ToolRefusalCode.DUPLICATE_OPEN_CASE,
                decision,
                existing_case_number=open_case_number,
            )

        try:
            session_case_count = self._session_case_count()
        except psycopg.Error:
            self._log_failure("create_dispute_case_failed")
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error")
        if session_case_count >= self._case_create_session_cap:
            return self._refuse(ToolRefusalCode.SESSION_CAP_REACHED, decision)

        return None

    def _insert_case(
        self,
        request: CreateDisputeCaseRequest,
        resolved: _ResolvedTransaction,
        decision: PolicyDecision,
    ) -> CreateDisputeCaseResult | ToolFailure:
        """Insert the case row and its creation audit record (AC-E4-19's fail-closed insert)."""
        case_number = _new_case_number(self._domain_date)
        unknown = ContractAmountProvenance.UNKNOWN.value
        amount_usd = None if resolved.amount_usd_provenance == unknown else resolved.amount_usd
        currency = None if amount_usd is None else "USD"
        expected_date = expected_first_response(self._policy, request.category, self._domain_date)
        created_at = self._now()

        try:
            with (
                psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
                conn.cursor() as cur,
            ):
                cur.execute(
                    """
                    INSERT INTO cases (
                        case_number, customer_id, transaction_id, session_id, idempotency_key,
                        status, category, amount, currency, amount_provenance, domain_date,
                        expected_first_response_date, created_at_utc, policy_version,
                        reason_code, language
                    ) VALUES (
                        %(case_number)s, %(customer_id)s, %(transaction_id)s, %(session_id)s,
                        %(idempotency_key)s, 'Open', %(category)s, %(amount)s, %(currency)s,
                        %(amount_provenance)s, %(domain_date)s, %(expected_first_response_date)s,
                        %(created_at_utc)s, %(policy_version)s, %(reason_code)s, %(language)s
                    )
                    """,
                    {
                        "case_number": case_number,
                        "customer_id": self._customer_id,
                        "transaction_id": resolved.transaction_id,
                        "session_id": self._session_id,
                        "idempotency_key": request.idempotency_key,
                        "category": request.category.value,
                        "amount": amount_usd,
                        "currency": currency,
                        "amount_provenance": resolved.amount_usd_provenance,
                        "domain_date": self._domain_date,
                        "expected_first_response_date": expected_date,
                        "created_at_utc": created_at,
                        "policy_version": decision.policy_version,
                        "reason_code": decision.reason_code.value,
                        "language": self._language,
                    },
                )
                # The audit sink commits on its own, separate connection (app.persistence.audit),
                # not this one — but it is still called before this ``with`` block exits: a
                # failure here propagates and rolls this (still uncommitted) case insert back, so
                # a *raised exception* on the audit write creates no case (AC-E4-19). This is not
                # a single atomic transaction across both connections; see the module's
                # Limitations for the separate, narrower crash-window gap that leaves open.
                record = self._case_record(
                    _ResolvedCase(
                        owned=True,
                        case_number=case_number,
                        status=CaseStatus.OPEN.value,
                        transaction_id=resolved.transaction_id,
                        category=request.category.value,
                        amount=amount_usd,
                        currency=currency,
                        amount_provenance=resolved.amount_usd_provenance,
                        domain_date=self._domain_date,
                        expected_first_response_date=expected_date,
                        created_at_utc=created_at,
                        policy_version=decision.policy_version,
                        reason_code=decision.reason_code.value,
                        language=self._language,
                    )
                )
                self._write_audit(
                    AuditAction.CASE_CREATED,
                    record.model_dump(mode="json"),
                    reason_code=decision.reason_code,
                    policy_version=decision.policy_version,
                )
        except psycopg.errors.UniqueViolation as exc:
            constraint = exc.diag.constraint_name
            if constraint == _IDEMPOTENCY_CONSTRAINT:
                raced = self._find_by_idempotency_key(request.idempotency_key)
                if raced is not None:
                    return self._replay_or_conflict(raced, request, decision)
            elif constraint == _OPEN_CASE_CONSTRAINT:
                try:
                    open_case_number = self._open_case_number_for(request.transaction_ref)
                except psycopg.Error:
                    open_case_number = None
                # Defensive only: the violation just raised guarantees a matching open row
                # exists; ``None`` here would mean it closed in the instant between the
                # violation and this query, an unreachable window in practice. Fails closed as
                # a store error rather than a refusal missing a field the contract requires.
                if open_case_number is None:
                    self._log_failure("create_dispute_case_failed")
                    return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error")
                return self._refuse(
                    ToolRefusalCode.DUPLICATE_OPEN_CASE,
                    decision,
                    existing_case_number=open_case_number,
                )
            self._log_failure("create_dispute_case_failed")
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error")
        except psycopg.Error:
            self._log_failure("create_dispute_case_failed")
            return ToolFailure(tool=ToolName.CREATE_DISPUTE_CASE, cause="error")

        return CreateDisputeCaseResult(created=True, case_number=case_number)
