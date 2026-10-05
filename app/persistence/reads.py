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
In: all six methods of ``ToolPort``, the distinction between a probe of another customer's
reference and a plain miss (kept in the audit trail only), auditing every call, the create tool's
own permission invariants.
Out: loading the policy file (``app.domain.policy.loader``, supplied already loaded), evaluating
policy inside the create tool (the controller's job, never this module's), deciding what a tool
call means (the dialogue controller).

Design Principles
-----------------
- **A foreign reference answers exactly like a missing one:** ``get_transaction`` and
  ``get_case`` look up a reference without scoping the query by customer, then decide in Python
  whether the row belongs to the session's customer; either way the caller gets ``None``, never a
  hint that the reference exists at all. The two cases are still told apart in the audit record
  (``transaction_probed``/``case_probed`` vs. ``transaction_viewed``/``case_viewed``), since the
  trail is not the same audience as the response.
- **Fail closed on the audit write.** Every successful read or write is audited before it is
  returned; ``AuditSink.record`` raising propagates instead of being swallowed, so a customer
  never receives data, or an outcome, whose access or filing was not recorded. A call that itself
  fails at the store is not audited: there was no completed action to record, only an
  infrastructure failure the caller already sees as a ``ToolFailure``.
- **No customer parameter anywhere:** no tool method accepts a customer id. The signed-in
  customer is fixed when the port is built, listings filter by that customer in SQL, and the
  transaction and case resolvers check that a referenced row belongs to that customer before
  anything is returned or changed.
- **``evaluate_dispute`` has no not-found return in its failure type.** Its contract offers
  ``PolicyDecision | ToolFailure | None``; a transaction reference that does not exist or belongs
  to another customer answers ``None``, the same response either way, distinguished only in the
  audit record. ``ToolFailure`` is reserved for what the store itself could not do.
- **The create tool enforces permission invariants only, never policy:**
  ``confirmation_required``, ``confirmation_mismatch``, ``idempotency_conflict``,
  ``duplicate_open_case`` and ``session_cap_reached`` are all this tool can verify itself; a
  request carrying no decision is refused with ``decision_missing``, and a request the policy
  would refuse is never re-evaluated here. ``PolicyDecision`` carries ``transaction_ref`` and
  ``category`` precisely so ``confirmation_mismatch`` can compare a filing call against what was
  actually decided, without trusting the caller and without re-running the policy.
- **Idempotency: a proactive check for the ordinary sequential replay, the unique constraint for
  the true race.** A lookup by ``(customer_id, idempotency_key)`` before the insert handles a
  call repeated in sequence, including the case where an earlier call's own case is now the
  "open case" a naive duplicate-open-case check would otherwise wrongly refuse; the insert itself
  still relies on the ``cases_customer_idempotency_key_unique`` and
  ``cases_transaction_id_open_unique`` constraints to resolve two calls arriving at the same
  moment, which a check-then-insert without that fallback could not.
- **Never trusts caller-stated fields:** the transaction is re-resolved from the store inside
  ``create_dispute_case`` itself, exactly as ``evaluate_dispute`` does, and the amount, currency
  and provenance stored on the case row come from that fresh read, never from ``request`` or from
  the (possibly stale) ``decision`` object.
- **A ``duplicate_open_case`` refusal names the case it collided with:**
  ``CreateDisputeCaseResult.existing_case_number`` is looked up fresh, both on the proactive path
  (``_creation_limit_refusal``) and on the store-level race (``_insert_case``'s
  ``UniqueViolation`` handler), never trusted from an earlier read.
- **A replay is its own audit record, not only a log line:** ``_replay_or_conflict`` writes
  ``AuditAction.CASE_CREATION_REPLAYED``, so a trace built from audit records alone accounts for
  every filing call the customer actually made, including a repeat.

Runtime Contract
----------------
``PostgresToolPort(dsn, audit, policy, *, customer_id, session_id, trace_id, domain_date, now,
language, case_create_session_cap)`` implements ``contracts.service_v1.tools.ToolPort``.
``language`` and ``case_create_session_cap`` are bound at construction like every other
session-scoped fact this class holds: ``CreateDisputeCaseRequest`` (``contracts.service_v1.tools``)
has no field for either, since a case's language is a fact of the session filing it, not of one
call, and the cap is a permission invariant this tool enforces itself, not caller-supplied data.
``clamp_merchant(value)``, which fits a merchant to ``MERCHANT_MAX_LENGTH``, is shared with
``app.persistence.ticket_detail``.

Limitations
-----------
One connection per call, matching the other persistence modules; there is no pooling (see
``app.persistence.audit``). ``risk_score`` is always ``None``: risk routing is switched off in the
shipped policy and no risk-features lookup is wired in here. ``description`` on every
``TransactionFact`` is always ``None``: the serving store carries no separate description column,
only ``merchant_name``. The case-insert transaction and the audit write are two separate store
connections, not one atomic transaction (matching ``app.persistence.audit``'s
one-connection-per-call design): a process crash in the narrow window after the audit write
commits but before the case insert's own connection commits could leave an audit record for a
case that does not exist; there is no cross-connection two-phase commit to close that window.
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
from app.llm.masking import safe_hex_suffix  # A suffix that can't look card-shaped once joined
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

# Statuses of a case that is still open (``CaseStatus``); a resolved or rejected case is not.
_OPEN_CASE_STATUSES = (CaseStatus.OPEN.value, CaseStatus.IN_REVIEW.value)

# Constraint names as declared in the store; read here to tell a lost idempotency race from a lost
# duplicate-open-case race without guessing at a generic unique-violation's own message text.
_IDEMPOTENCY_CONSTRAINT = "cases_customer_idempotency_key_unique"
_OPEN_CASE_CONSTRAINT = "cases_transaction_id_open_unique"

# Both contracts.service_v1.tools.TransactionFact.merchant and its distinct, same-named sibling
# contracts.service_v1.envelope.TransactionFact.merchant share this bound; the store's own column
# (VARCHAR(150)) is wider, so a stored value can exceed either contract's shape.
# Shared with app.persistence.ticket_detail, the only other module that re-hydrates this field
# from the same table straight into a TransactionFact of its own.
MERCHANT_MAX_LENGTH = 80


def clamp_merchant(value: str | None) -> str | None:
    """A stored ``merchant_name`` fit to either ``TransactionFact.merchant``'s own bound.

    Truncates a value over the contract's length, the same repair
    ``app.conversation.llm_understanding``'s ``_LENGTH_REPAIRS`` applies to a model's own
    overlong guess at this same field; merchant text is inert descriptive data to every reader of
    it (never a policy input, never an instruction channel), so shortening it changes nothing
    about correctness or safety, only how much of it a customer sees. A blank or whitespace-only
    value normalizes to ``None`` (absent), matching the contract's own "absent, not empty" rule.
    A truncation logs a warning naming only the lengths involved and the request id, never the
    value, so there is evidence of how often a stored merchant name exceeds the contract's bound.
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > MERCHANT_MAX_LENGTH:
        # The value itself is never logged (it may be long for any reason, injected or not; PII
        # minimization applies regardless), only that a clamp fired and by how much.
        logger.warning(
            "merchant_name_truncated original_length=%d kept_length=%d request_id=%s",
            len(stripped),
            MERCHANT_MAX_LENGTH,
            current_request_id(),
        )
    return stripped[:MERCHANT_MAX_LENGTH]


def _new_case_number(domain_date: date) -> str:
    """A short, readable case number (``CASE-YYYYMMDD-<hex>``): what a customer quotes on the
    phone. The hex suffix is chosen so that, joined to the date digits, it cannot look card-shaped.
    """
    date_digits = domain_date.strftime("%Y%m%d")
    return f"CASE-{date_digits}-{safe_hex_suffix(preceding_digits=len(date_digits))}"


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
    """The case already on file for a customer's idempotency key, if any.

    Holds only what a replay needs to compare against the new request: the case number, its
    transaction and its category.
    """

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
        """Bind the session's customer and the facts every call is audited under.

        ``audit`` receives one record per completed call; ``policy`` is the loaded policy a fresh
        evaluation runs against; ``domain_date`` is the domain calendar's date for the whole
        session and ``now`` the injected clock for audit and case timestamps; ``language`` is
        stored on each case this session files and ``case_create_session_cap`` bounds how many
        cases it may file.
        """
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
        (every operational log line carries both)."""
        logger.warning("%s trace_id=%s request_id=%s", event, self._trace_id, current_request_id())

    def _log_replay(self, case_number: str, idempotency_key: str) -> None:
        """A replayed filing call, for immediate operational grep alongside its own
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
        """A USD amount (or none) paired with how the figure was obtained."""
        money = None if amount_usd is None else Money(amount=amount_usd, currency="USD")
        return DisclosedAmount(money=money, provenance=ContractAmountProvenance(provenance))

    def _transaction_fact(self, row: _ResolvedTransaction) -> TransactionFact:
        """The tool contract's ``TransactionFact`` for a resolved row (``description`` is
        always ``None``: the store has no such column)."""
        return TransactionFact(
            ref=row.transaction_id,
            occurred_on=row.transaction_date.date(),
            merchant=clamp_merchant(row.merchant_name),
            description=None,
            amount=self._disclosed_amount(row.amount_usd, row.amount_usd_provenance),
            original_amount=Money(amount=row.amount, currency=row.currency),
            product=ProductLabel(name=row.product_type or "unknown", last4=row.last4),
            status=PolicyTransactionStatus(row.transaction_status),
        )

    def list_transactions(self, filters: TransactionFilters) -> TransactionPage | ToolFailure:
        """The session customer's own transactions matching ``filters``, most recent first.

        At most five items are returned; ``total_count`` carries the number of matches before that
        cut. The query is scoped to the session customer in SQL. A store failure is logged and
        answered as ``ToolFailure`` without an audit record; otherwise the page is audited
        (``transactions_listed``) before it is returned.
        """
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
        """The session customer's own transaction ``ref``, or ``None`` if there is no match.

        A reference that does not exist and one that belongs to another customer both answer
        ``None``; the audit record tells them apart (``transaction_viewed`` with a ``null``
        result versus ``transaction_probed``). A store failure answers ``ToolFailure`` and is not
        audited.
        """
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
        """The shared ``CaseRecord`` contract for a resolved case row."""
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
        """Every dispute case the session customer has filed, most recently created first.

        Scoped to the session customer in SQL and audited (``cases_listed``) before it is
        returned; a store failure answers ``ToolFailure`` and is not audited.
        """
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
        """The session customer's own case ``case_number``, or ``None`` if there is no match.

        A case that does not exist and one that belongs to another customer both answer ``None``;
        the audit record tells them apart (``case_viewed`` with a ``null`` result versus
        ``case_probed``). A store failure answers ``ToolFailure`` and is not audited.
        """
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

    def _is_repeat_complainer(self) -> bool:
        """This session's own customer's point-in-time repeat-complainer flag, as the seed carried
        it: never recomputed here from complaint records, which this module does not read."""
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT is_repeat_complainer FROM customers WHERE customer_id = %s",
                (self._customer_id,),
            )
            row = cur.fetchone()
        return bool(row[0]) if row is not None else False

    def evaluate_dispute(
        self, request: EvaluateDisputeRequest
    ) -> PolicyDecision | ToolFailure | None:
        """The policy decision for ``request``, computed fresh; no side effect.

        A ``transaction_ref`` that does not exist or belongs to another customer answers with
        the same ``None`` either way; see the module's Design Principles. ``None`` is a normal
        matchless result, never a ``ToolFailure``, which is reserved for what the store itself
        could not do.

        The request is built from the freshly resolved row (amount unknown when its provenance
        says so, NLU confidence fixed at 1.0, no risk score) and evaluated against the loaded
        policy on the session's domain date. The decision is audited, with its reason code and
        policy version, before it is returned.
        """
        try:
            resolved = self._resolve_transaction(request.transaction_ref)
        except psycopg.Error:
            self._log_failure("evaluate_dispute_failed")
            return ToolFailure(tool=ToolName.EVALUATE_DISPUTE, cause="error")

        # Resolved but not owned by this session's customer, and resolved to nothing at all,
        # must cost the same number of store round-trips: a query run only for an owned
        # transaction is a timing side-channel that tells a caller a foreign reference exists,
        # even though the response body is identical either way. That symmetry rests on the shared
        # code path above, not on which value each branch returns.
        if resolved is None:
            self._write_audit(AuditAction.TRANSACTION_VIEWED, None)
            return None
        if not resolved.owned:
            self._write_audit(AuditAction.TRANSACTION_PROBED, None)
            return None

        try:
            has_open_case = self._open_case_number_for(request.transaction_ref) is not None
            is_repeat_complainer = self._is_repeat_complainer()
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
            is_repeat_complainer=is_repeat_complainer,
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
    # Case creation: permission invariants only, never policy.
    # -------------------------------------------------------------------------------------

    def _refuse(
        self,
        refusal: ToolRefusalCode,
        decision: PolicyDecision,
        *,
        existing_case_number: str | None = None,
    ) -> CreateDisputeCaseResult:
        """Audit and return a permission refusal; ``AuditSink.record`` raising still propagates.

        The audit record (``case_creation_refused``) carries the decision's reason code and policy
        version. ``existing_case_number`` is set only for a duplicate-open-case refusal.
        """
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
        """The same key with the same payload replays; a different payload is a real conflict.

        A replay (same transaction and category) returns the original case number as created and
        is audited as ``case_creation_replayed``; a different payload is refused with
        ``idempotency_conflict``.
        """
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
        """File a case, or refuse for a permission reason; never a policy reason.

        Checks run in order: the request's own permission invariants (decision present, confirmed,
        matching), a replay by idempotency key, the freshly re-resolved owned transaction, the
        duplicate-open-case and session-cap limits, then the insert. A ``ToolFailure`` covers
        a store error (the store cannot be reached, or a statement fails) and fails the filing
        closed: no case is created uncounted, and the customer is told it could not be
        completed, never that it succeeded. A failure raised by the audit sink that is not a
        store error is not converted: it propagates, and the uncommitted insert is rolled back.
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
        """A refusal for the duplicate-open-case or session-cap invariant; ``None`` to proceed.

        A store failure while checking either limit answers ``ToolFailure`` (fail closed).
        """
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
        """Insert the case row and its creation audit record, failing closed.

        The case number, amount, currency, provenance and dates come from the freshly resolved
        transaction and the session, never from the request. The audit record is written while
        the insert is still uncommitted, so an error raised by the audit write rolls the insert
        back. A lost race on the store's unique constraints is reclassified: the idempotency
        constraint becomes a replay or conflict, the open-case constraint a duplicate-open-case
        refusal naming the winning case. Any other store error answers ``ToolFailure``.
        """
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
                # not this one, but it is still called before this ``with`` block exits: a failure
                # here propagates and rolls this (still uncommitted) case insert back, so a
                # *raised exception* on the audit write creates no case. This is not a single
                # atomic transaction across both connections; see the module's Limitations for
                # the separate, narrower crash-window gap that leaves open.
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
