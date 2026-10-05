"""
Seed Reference Resolution
===========================

Overview
--------
Turns a ``Case.seed_ref`` (``"ops_seed:CLI-..."``, ``"ops_seed:TRX-..."`` or an ``eval_bank``
equivalent) into the customer id the runner mints a session for. A customer reference names
itself directly; a transaction reference names the customer through the transaction it belongs
to, looked up in the real seeded store the running system also reads from — this module never
guesses at, or trusts, a customer id a case did not actually declare through its own real data.

Scope
-----
In: parsing a ``seed_ref``'s source and identifier, and resolving a transaction or customer
reference to its owning customer id from the store — ``ops_seed`` and ``eval_bank`` alike, since
both sources land in the same ``transactions`` table by the time a case runs, so a reader treats
an ``eval_bank`` row exactly like a seed row.
Out: minting the session itself (the runner's HTTP layer calls the sandbox login
with the id this module returns); loading the seed data in the first place — ``ops_seed`` via
``app.persistence.load_seed`` and ``eval_bank`` via ``app.persistence.load_eval_bank``, both of
which the caller runs before any case executes.

Design Principles
-----------------
- **One query, the store's own schema, source-agnostic.** The transaction lookup reads the same
  ``transactions`` table the running system's own tool layer reads (``app.persistence.reads``),
  by ``transaction_id`` alone, with no ``source`` filter — so a case's expectation and the
  system's own view of the data can never silently diverge onto two different sources of truth,
  and an ``eval_bank`` row resolves through the exact same query an ``ops_seed`` row does, once
  it exists in that table.
- **A malformed reference or a missing row fails loudly, before any session is minted.** A
  transaction id absent from the store raises immediately rather than falling back to a guessed
  customer id.

Runtime Contract
-----------------
``parse_seed_ref(seed_ref) -> (source, identifier)``.
``resolve_customer_id(dsn, seed_ref) -> str``, a synchronous, one-shot connection per call (the
runner resolves every case's customer once, at the start of that case's run, never per turn).

Limitations
-----------
A customer reference (``CLI-...``), from either source, is returned exactly as given: it is not
looked up in the store here, so a customer id that does not exist is not caught by this module.
Only a transaction reference is checked against the store.
"""

from __future__ import annotations

# Standard libraries
from typing import Literal, cast

# Third-party libraries
import psycopg

#: Which half of the golden set's two seed sources a reference names.
SeedSource = Literal["ops_seed", "eval_bank"]

_SOURCES: frozenset[str] = frozenset({"ops_seed", "eval_bank"})


def parse_seed_ref(seed_ref: str) -> tuple[SeedSource, str]:
    """Split ``seed_ref`` into its source and identifier.

    Raises
    ------
    ValueError
        ``seed_ref`` has no ``source:identifier`` shape, or names a source outside
        ``{"ops_seed", "eval_bank"}``.
    """
    source, separator, identifier = seed_ref.partition(":")
    if not separator or source not in _SOURCES or not identifier:
        raise ValueError(f"malformed seed_ref: {seed_ref!r}")
    return cast(SeedSource, source), identifier


def resolve_customer_id(dsn: str, seed_ref: str) -> str:
    """The customer id ``seed_ref`` names or resolves to, read fresh from the real seeded store.

    Raises
    ------
    ValueError
        ``seed_ref`` is malformed, names an identifier that is neither a customer id (``CLI-``)
        nor a transaction id (``TRX-``), or names a transaction absent from the store.
    """
    _source, identifier = parse_seed_ref(seed_ref)
    if identifier.startswith("CLI-"):
        return identifier
    if identifier.startswith("TRX-"):
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id FROM transactions WHERE transaction_id = %s", (identifier,)
            )
            row = cur.fetchone()
        if row is None:
            raise ValueError(f"seed_ref {seed_ref!r} names no transaction in the store")
        return cast(str, row[0])
    raise ValueError(f"seed_ref {seed_ref!r} names neither a customer nor a transaction id")
