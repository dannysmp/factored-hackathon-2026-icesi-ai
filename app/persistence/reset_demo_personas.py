"""
Demo Persona Reset
===================

Overview
--------
Restores every demo persona's customer to its seeded state before a demonstration (ADR-18):
deletes the case rows a previous demo run created for a persona's customer, so the
duplicate-open-case rule cannot block a scripted flow that was already exercised once. An
operator command, run before a demonstration, not an HTTP route — the sign-in broker itself never
needs to reset anything, so this is not new public attack surface.

Scope
-----
In: reading the persona file, deleting the persona customers' case rows, the command line
``python -m app.persistence.reset_demo_personas``.
Out: creating or validating the persona file (``app.security.demo_personas``), the seed itself
(``pipelines.ops_seed``, ``app.persistence.load_seed``), which never carries case rows to begin
with.

Design Principles
------------------
- Deletes only ``cases`` rows for the persona file's own customer_ids: customers, products and
  transactions are seed content this command never touches.
- One transaction: every persona's cases are deleted together, or none are.

Runtime Contract
-----------------
``reset_demo_personas(dsn, personas) -> int`` (rows deleted).
The command line ``python -m app.persistence.reset_demo_personas``.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command-line interface
import logging  # Progress events, never print
from collections.abc import Sequence  # Type of the parsed argv

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.config import ConfigError, load_settings  # The one validated source of DATABASE_URL
from app.observability.logging import configure_logging  # Structured logging, installed once
from app.security.demo_personas import PersonaList, load_personas  # The persona list to reset

logger = logging.getLogger(__name__)


def reset_demo_personas(dsn: str, personas: PersonaList) -> int:
    """Delete every case row for a demo persona's customer.

    Returns
    -------
    int
        The number of case rows deleted.
    """
    customer_ids = [persona.customer_id for persona in personas.customers]
    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM cases WHERE customer_id = ANY(%s)", (customer_ids,))
        deleted = cur.rowcount
        conn.commit()
    return deleted


def main(argv: Sequence[str] | None = None) -> int:
    """Reset every demo persona's customer from the command line.

    Returns
    -------
    int
        0 on success, whatever exit code an uncaught driver error would otherwise raise.
    """
    parser = argparse.ArgumentParser(
        description="Delete the case rows demo personas accumulated, before a demonstration."
    )
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: DATABASE_URL)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        parser.error(str(exc))
    configure_logging(
        settings.log_level,
        service_version=settings.service_version,
        environment=settings.app_env.value,
    )
    if args.dsn:
        dsn = args.dsn
    else:
        try:
            dsn = settings.require_database_url().get_secret_value()
        except ConfigError as exc:
            parser.error(str(exc))
    personas = load_personas()
    deleted = reset_demo_personas(dsn, personas)
    logger.info("demo_personas_reset cases_deleted=%s", deleted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
