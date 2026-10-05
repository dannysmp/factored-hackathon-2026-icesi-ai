"""
Migration Prefix Collision Check
===================================

Overview
--------
Fails when a pull request introduces a new migration file (``app/persistence/migrations/*.sql``)
whose leading numeric prefix already belongs to a migration file already on the target branch.
``app.persistence.migrate`` orders and applies migrations by full filename, not by numeric prefix
alone, so a same-prefix collision between two files that both survive review and whose SQL
does not actually conflict is not unsafe by itself — but it is exactly the kind of coincidence that
should be caught and looked at before merge, not discovered by luck twice in a row.

Scope
-----
In: comparing the migration files a pull request adds against the migration files already on its
target branch, and reporting every added file whose prefix collides with one of them.
Out: checking two files the *same* pull request adds against each other (a reviewer already reads
every file in a single diff together); validating migration SQL content or applying migrations
(``app.persistence.migrate``'s own job); resolving which of two colliding files should be renumbered
(a decision for whoever wrote them).

Design Principles
-----------------
- **The comparison is against the target branch's real tip, not the merge base.** A prefix that was
  free when this branch started but has since been claimed by another merged pull request is exactly
  the case this check exists to catch — the same scenario that has already happened twice in this
  project's history (the ``0004`` and ``0006`` prefixes).
- **Only files this pull request adds are checked.** A file already on the target branch sharing a
  prefix with another file already on the target branch is prior, already-reviewed history; this
  check has nothing new to say about it.
- **The git plumbing is a thin, separately testable layer over a pure function.**
  ``find_collisions`` takes plain filename sequences and has no git dependency at all, so its logic
  is covered by fast, hermetic unit tests; only the thin ``main`` wrapper needs an actual git
  repository to exercise.

Runtime Contract
-----------------
``find_collisions(added, target_branch_files) -> tuple[str, ...]``, the added filenames (in the
order given) whose numeric prefix already exists in ``target_branch_files``.
``main(argv) -> int``: ``python -m scripts.check_migration_prefixes --base origin/main``. Logs one
error per collision and returns 1 when any exist; 0 otherwise, including when git or the migrations
directory is unavailable in a way that yields no comparable files (nothing to flag).

Limitations
-----------
Renamed files are invisible to this check by construction: ``git diff --diff-filter=A`` reports a
rename as a rename, not an add, so a file moved to a new, colliding prefix without changing content
is not flagged. This matches the check's own narrow purpose (a *new* migration colliding with an
*existing* one) rather than a general filename-uniqueness linter.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command-line interface
import logging  # Progress and error events, never print
import re  # Numeric-prefix extraction
import subprocess  # git plumbing
from collections.abc import Sequence  # Type of the parsed argv
from pathlib import PurePosixPath  # Filename from a git-reported path

logger = logging.getLogger(__name__)

DEFAULT_MIGRATIONS_DIR = "app/persistence/migrations"

_PREFIX_RE = re.compile(r"^(\d+)_")


def _prefix(filename: str) -> str | None:
    """The leading numeric prefix of a migration filename, or None when it has none."""
    match = _PREFIX_RE.match(filename)
    return match.group(1) if match else None


def find_collisions(added: Sequence[str], target_branch_files: Sequence[str]) -> tuple[str, ...]:
    """Filenames in ``added`` whose numeric prefix already exists in ``target_branch_files``."""
    target_prefixes = {prefix for name in target_branch_files if (prefix := _prefix(name))}
    return tuple(name for name in added if (prefix := _prefix(name)) and prefix in target_prefixes)


def _git_lines(*arguments: str) -> tuple[str, ...]:
    """Non-empty stdout lines of a git command; empty when git or the ref is unavailable."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *arguments],  # noqa: S607 - resolved through PATH by design
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    return tuple(line for line in result.stdout.splitlines() if line)


def _added_migration_filenames(base: str, migrations_dir: str) -> tuple[str, ...]:
    """Filenames this branch adds under ``migrations_dir`` relative to ``base``'s merge base."""
    lines = _git_lines(
        "diff", "--name-status", "--diff-filter=A", f"{base}...HEAD", "--", migrations_dir
    )
    # git diff --name-status always emits "STATUS\tpath" for a matched line; no other shape occurs.
    return tuple(PurePosixPath(line.split("\t", 1)[1]).name for line in lines)


def _target_branch_migration_filenames(base: str, migrations_dir: str) -> tuple[str, ...]:
    """Filenames already under ``migrations_dir`` at ``base``'s current tip."""
    lines = _git_lines("ls-tree", "-r", "--name-only", base, "--", migrations_dir)
    return tuple(PurePosixPath(line).name for line in lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Check this branch's added migrations against its target branch; return the exit code."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", default="origin/main", help="Target branch ref (default: origin/main)"
    )
    parser.add_argument(
        "--migrations-dir",
        default=DEFAULT_MIGRATIONS_DIR,
        help=f"Migrations directory, repo-relative (default: {DEFAULT_MIGRATIONS_DIR})",
    )
    args = parser.parse_args(argv)

    added = _added_migration_filenames(args.base, args.migrations_dir)
    target_branch_files = _target_branch_migration_filenames(args.base, args.migrations_dir)
    collisions = find_collisions(added, target_branch_files)

    for filename in collisions:
        logger.error(
            "migration_prefix_collision file=%s base=%s hint=%s",
            filename,
            args.base,
            "give it the next free prefix instead",
        )
    return 1 if collisions else 0


if __name__ == "__main__":
    raise SystemExit(main())
