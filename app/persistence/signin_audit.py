"""
Sign-In Audit Store
====================

Overview
--------
Writes one ``SignInAuditRecord`` (``app.security.signin_audit``) as one row of ``signin_audit``.
The table refuses ``UPDATE``, ``DELETE`` and ``TRUNCATE`` at the database (migration 0006, reusing
0003's ``audit_log_forbid_mutation`` trigger function): this module only appends.

Scope
-----
In: the one write, ``SignInAuditSink.record``.
Out: creating the table (``app.persistence.migrate``), reading attempts back (not needed yet).

Design Principles
------------------
- Fails closed: a write that cannot complete raises; nothing here swallows a ``psycopg.Error``
  into a dropped entry.
- One connection per call, matching this codebase's other persistence modules; no pooling yet.

Runtime Contract
-----------------
``PostgresSignInAuditSink(dsn).record(entry) -> None``
"""

from __future__ import annotations

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.security.signin_audit import SignInAuditRecord  # The record this sink writes

_CONNECT_TIMEOUT_SECONDS = 5

_INSERT_SQL = """
INSERT INTO signin_audit (
    trace_id, occurred_at_utc, audience, persona_slug, resolved_customer_id,
    client_address_hash, outcome, reason_code, session_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


class PostgresSignInAuditSink:
    """Writes every ``SignInAuditRecord`` as one append-only row."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def record(self, entry: SignInAuditRecord) -> None:
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
                    entry.occurred_at,
                    entry.audience.value,
                    entry.persona_slug,
                    entry.resolved_customer_id,
                    entry.client_address_hash,
                    entry.outcome.value,
                    entry.reason_code.value,
                    entry.session_id,
                ),
            )
            conn.commit()
