"""
Serving-Store Migrations
=========================

Overview
--------
Applies the numbered SQL files in ``migrations/`` to a Postgres database, in filename order,
each exactly once. There is one runner and it owns every migration (ADR-9): a table cannot be
created by hand against the running database and expected to match what a fresh one gets.

Scope
-----
In: discovering, ordering, applying and recording migrations.
Out: writing the SQL itself (each file is reviewed on its own), the connection pool the running
service uses for ordinary queries (a migration run is a short-lived, one-off connection).

Design Principles
-----------------
- Vanilla SQL, no ORM and no migration framework (ADR-9): a migration is a plain ``.sql`` file,
  and applying one is one transaction, committed only when the whole file and its record succeed.
- Up only. A migration already applied is never edited; a change is a new, later-numbered file.
  A previously applied file whose content has since changed is refused, not silently re-applied
  or silently ignored: a migration is immutable once shipped.
- The tracking table is created by the runner itself on first use, so a fresh database needs
  nothing prepared by hand beyond the DSN.

Runtime Contract
----------------
``apply_migrations(dsn, directory=MIGRATIONS_DIR) -> tuple[str, ...]``, the versions applied, in
order. ``main(argv) -> int`` is the command-line entry point (``DATABASE_URL`` or ``--dsn``).

Limitations
-----------
No down migrations: reverting a mistake ships as a new forward migration, matching the project's
seed and evaluation data, which are also append-only by convention.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command-line interface
import hashlib  # Detects a migration file edited after it was applied
import logging  # Progress events, never print
import sys  # Log stream
from collections.abc import Sequence  # Type of the parsed argv
from pathlib import Path  # Locate migration files

# Third-party libraries
import psycopg  # Serving-store driver

# Local modules
from app.config import ConfigError, load_settings  # The one validated source of DATABASE_URL

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_TRACKING_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _checksum(sql: str) -> str:
    """Content digest of a migration file, to detect an edit after it was applied."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def apply_migrations(dsn: str, *, directory: Path = MIGRATIONS_DIR) -> tuple[str, ...]:
    """Apply every migration in ``directory`` not yet recorded, in filename order.

    Returns
    -------
    tuple[str, ...]
        The versions applied in this call, in the order they ran; empty when already current.

    Raises
    ------
    RuntimeError
        A previously applied migration's file content no longer matches its recorded checksum.
    """
    applied: list[str] = []
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute(_TRACKING_TABLE_SQL)
        conn.commit()
        for path in sorted(directory.glob("*.sql")):
            version = path.stem
            sql = path.read_text(encoding="utf-8")
            checksum = _checksum(sql)
            with conn.cursor() as cur:
                cur.execute("SELECT checksum FROM schema_migrations WHERE version = %s", (version,))
                row = cur.fetchone()
            if row is not None:
                if row[0] != checksum:
                    raise RuntimeError(
                        f"migration {version} has changed since it was applied "
                        f"(recorded {row[0]}, file is now {checksum})"
                    )
                logger.info("migration_already_applied version=%s", version)
                continue
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (version, checksum),
                )
            conn.commit()
            logger.info("migration_applied version=%s", version)
            applied.append(version)
    return tuple(applied)


def main(argv: Sequence[str] | None = None) -> int:
    """Apply pending migrations from the command line.

    Returns
    -------
    int
        0 on success, whatever exit code an uncaught driver error would otherwise raise.
    """
    parser = argparse.ArgumentParser(description="Apply pending serving-store migrations.")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (default: DATABASE_URL)")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr
    )
    if args.dsn:
        dsn = args.dsn
    else:
        try:
            dsn = load_settings().require_database_url().get_secret_value()
        except ConfigError as exc:
            parser.error(str(exc))
    applied = apply_migrations(dsn)
    if applied:
        logger.info("migrations_applied versions=%s", ",".join(applied))
    else:
        logger.info("migrations_up_to_date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
