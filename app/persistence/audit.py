"""
Audit Sink
==========

Overview
--------
Writes one ``AuditRecord`` (``contracts.service_v1.audit``) as one row of ``audit_log``. The
table itself refuses ``UPDATE`` and ``DELETE`` at the database (migration 0003): this module only
appends, and never tries to do anything the store would refuse anyway.

Scope
-----
In: the one write, ``AuditSink.record``.
Out: creating the table (``app.persistence.migrate``), reading a timeline back (a later slice),
deciding what a tool call must audit (``app.persistence.reads``).

Design Principles
------------------
- Fails closed (``AuditSink``'s own contract): a write that cannot complete raises; nothing here
  swallows a ``psycopg.Error`` into a dropped entry.
- One connection per call. There is no connection pool yet in this codebase; a call that opens,
  writes and closes is simple and correct, at the cost of a new TCP handshake per audited action
  (see Limitations).

Runtime Contract
-----------------
``PostgresAuditSink(dsn).record(entry) -> None``

Limitations
-----------
No connection pooling: acceptable at this codebase's current traffic, not at scale. Pooling is a
later, purely internal change — ``AuditSink``'s interface does not need to change for it.
"""

from __future__ import annotations

# Standard libraries
import logging  # Progress events, never print

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from contracts.service_v1.audit import AuditRecord  # The record this sink writes

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5

_INSERT_SQL = """
INSERT INTO audit_log (
    trace_id, customer_id, session_id, action, reason_code, policy_version,
    tool_result_hash, occurred_at_utc, domain_date, agent_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class PostgresAuditSink:
    """Writes every ``AuditRecord`` as one append-only row."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def record(self, entry: AuditRecord) -> None:
        """Append ``entry``.

        Raises
        ------
        psycopg.Error
            The write did not complete; the caller must not treat this as a dropped entry.
        """
        with (
            psycopg.connect(self._dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                _INSERT_SQL,
                (
                    entry.trace_id,
                    entry.customer_id,
                    entry.session_id,
                    entry.action.value,
                    entry.reason_code.value if entry.reason_code is not None else None,
                    entry.policy_version,
                    entry.tool_result_hash,
                    entry.occurred_at,
                    entry.domain_date,
                    entry.agent_id,
                ),
            )
            conn.commit()
