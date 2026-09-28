"""
Seed Reference Resolution
===========================

Overview
--------
Turns a ``Case.seed_ref`` (``"ops_seed:CLI-..."`` or ``"ops_seed:TRX-..."``) into the customer id
the runner mints a session for. A customer reference names itself directly; a transaction
reference names the customer through the transaction it belongs to, looked up in the real seeded
store the running system also reads from — this module never guesses at, or trusts, a customer
id a case did not actually declare through its own real data.

Scope
-----
In: parsing a ``seed_ref``'s source and identifier, and resolving an ``ops_seed`` reference to its
owning customer id from the store.
Out: resolving an ``eval_bank`` reference (no golden-set case needs it yet; every case that does
is adversarial, out of this increment's scope — see Limitations); minting the session itself (the
runner's HTTP layer, a later module, calls the sandbox login with the id this module returns);
loading the seed data in the first place (``app.persistence.load_seed``, run once per harness run,
before any case executes).

Design Principles
-----------------
- **One query, the store's own schema.** The transaction lookup reads the same ``transactions``
  table the running system's own tool layer reads (``app.persistence.reads``), by
  ``transaction_id``, so a case's expectation and the system's own view of the data can never
  silently diverge onto two different sources of truth.
- **A malformed or unsupported reference fails loudly, before any session is minted.** A
  transaction id absent from the store, or a source this module does not resolve yet, raises
  immediately rather than falling back to a guessed customer id.

Runtime Contract
-----------------
``parse_seed_ref(seed_ref) -> (source, identifier)``.
``resolve_customer_id(dsn, seed_ref) -> str``, a synchronous, one-shot connection per call (the
runner resolves every case's customer once, at the start of that case's run, never per turn).

Limitations
-----------
``eval_bank`` references are not resolved here: every existing golden-set case that names one is
adversarial, and adversarial cases are a later increment of this same slice. Building the
resolution now, for no case that needs it yet, would be exactly the kind of speculative work this
project's own engineering standard argues against.
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
    NotImplementedError
        ``seed_ref``'s source is ``eval_bank`` (see the module's own Limitations).
    """
    source, identifier = parse_seed_ref(seed_ref)
    if source == "eval_bank":
        raise NotImplementedError(f"resolving an eval_bank seed_ref is not built yet: {seed_ref!r}")
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
