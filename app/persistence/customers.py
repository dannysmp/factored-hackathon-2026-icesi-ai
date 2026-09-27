"""
Customer Lookup
===============

Overview
--------
Answers one question against the serving store: does this customer identifier exist, and what is
its status? The sandbox login uses it to check existence before issuing a session (AC-E4-47,
follow-up issue #36); the reads adapter does not need it, since every reference it resolves
already carries the owning customer's identifier in the row itself.

Scope
-----
In: one read of one customer's status by identifier.
Out: everything else about a customer (the reads adapter reads transactions and cases directly),
deciding what a missing or a particular status means (the caller).

Design Principles
-----------------
- Answers ``None`` for "not found" and for "the store could not be reached" alike: a customer
  identifier that does not resolve is, from the caller's side, indistinguishable from a store
  that is briefly down, and both are handled the same way by the caller (refuse to sign in).
- No status gate here: this module reports the status as read, never decides whether a status
  should be allowed to sign in or dispute (AC-E4-48 — the policy applies no status gate; the
  service layer does not invent one at the login boundary either).

Runtime Contract
-----------------
``customer_status(dsn, customer_id) -> str | None``
"""

from __future__ import annotations

# Standard libraries
import logging  # Progress events, never print

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.security.middleware import current_request_id  # Correlates a failure log to its request

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 5


def customer_status(dsn: str, customer_id: str) -> str | None:
    """The customer's status as the store records it, or ``None`` when there is no match or the
    store could not be reached."""
    try:
        with (
            psycopg.connect(dsn, connect_timeout=_CONNECT_TIMEOUT_SECONDS) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT customer_status FROM customers WHERE customer_id = %s", (customer_id,)
            )
            row = cur.fetchone()
    except psycopg.Error:
        logger.warning("customer_lookup_failed request_id=%s", current_request_id())
        return None
    return None if row is None else str(row[0])
