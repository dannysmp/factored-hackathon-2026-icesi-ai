"""
Deploy Step Order Tests
=======================

Component: the order of the commands in the remote script ``infra/scripts/05-deploy.sh`` sends
to the host. The database is started alone, rotated, migrated and seeded first, and the rest of
the stack is brought up (or replaced with the new image) only afterwards, so a new backend
release never serves requests against the previous release's schema or an empty database.

The tests read the real script, so an edit that moves a step changes what they check. They need
no Docker and run on every pull request.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "scripts" / "05-deploy.sh"

_COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.prod.yml"


def _remote_commands() -> list[str]:
    """The non-comment command lines of the remote script, from the image pull onwards."""
    lines = DEPLOY_SCRIPT.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{_COMPOSE} pull"))
    end = next(i for i, line in enumerate(lines) if line == "SCRIPT")
    return [line.strip() for line in lines[start:end] if line.strip()]


def _index(commands: list[str], fragment: str) -> int:
    matches = [i for i, line in enumerate(commands) if fragment in line]
    assert matches, f"no command contains {fragment!r}: has the step been removed or renamed?"
    return matches[0]


def test_the_database_is_migrated_and_seeded_before_the_stack_is_updated() -> None:
    commands = _remote_commands()

    pull = _index(commands, f"{_COMPOSE} pull")
    postgres_only = _index(commands, f"{_COMPOSE} up -d postgres")
    rotation = _index(commands, "ALTER ROLE")
    migrate = _index(commands, "app.persistence.migrate")
    load_seed = _index(commands, "app.persistence.load_seed")
    stack = commands.index(f"{_COMPOSE} up -d")
    restart = _index(commands, f"{_COMPOSE} restart backend")

    assert pull < postgres_only < rotation < migrate < load_seed < stack < restart


def test_nothing_before_the_migration_starts_the_backend() -> None:
    commands = _remote_commands()
    migrate = _index(commands, "app.persistence.migrate")

    starting = [
        line
        for line in commands[:migrate]
        if " up " in f" {line} " and line != f"{_COMPOSE} up -d postgres"
    ]

    assert starting == []
