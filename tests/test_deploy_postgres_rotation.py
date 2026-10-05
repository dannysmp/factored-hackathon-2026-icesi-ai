"""
Postgres Password Rotation Tests
=================================

Component: the wait-then-rotate step ``infra/scripts/05-deploy.sh`` runs after
``docker compose ... up -d postgres``. ``POSTGRES_PASSWORD`` only takes effect on Postgres's own
first init of an empty data directory; a redeploy against an already-initialized volume
silently keeps the old live password unless something explicitly rotates it. These tests
read the exact shell block out of the real script and run it against real, disposable
Postgres containers, so a revert or edit of the rotation step changes what the test
executes, not a hand-copied duplicate of it. Needs Docker; skipped when it is not
available.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "scripts" / "05-deploy.sh"


def _extract_rotation_block() -> str:
    """The exact wait-then-rotate block from 05-deploy.sh's remote script.

    Reads the real file rather than a hand-copied duplicate, so a revert or edit of the
    rotation step changes what this test executes, not just what it would display.
    """
    content = DEPLOY_SCRIPT.read_text()
    match = re.search(
        r"docker compose -f docker-compose\.yml -f docker-compose\.prod\.yml up -d postgres\n"
        r'(?P<block>.+?-d "\\\$\{pg_db\}"\n)',
        content,
        re.S,
    )
    assert match, (
        "the wait/rotate block was not found in 05-deploy.sh -- has it moved or been removed?"
    )
    assert "ALTER ROLE" in match.group("block"), (
        "the block between 'up -d postgres' and the rotation statement's own closing line no "
        "longer contains a password rotation statement -- has it been removed?"
    )
    # Undo the unquoted heredoc's own escaping of command substitutions ("\$" -> "$"), the
    # same processing bash performs when 05-deploy.sh builds the remote script it sends over SSM.
    return match.group("block").replace("\\$", "$")


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _wait_until(
    predicate: Callable[[], bool], timeout: float = 30.0, interval: float = 1.0
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def _can_connect(port: int, password: str) -> bool:
    try:
        with psycopg.connect(
            host="127.0.0.1",
            port=port,
            user="dispute_intake",
            password=password,
            dbname="dispute_intake",
            connect_timeout=5,
        ):
            return True
    except psycopg.Error:
        return False


@pytest.fixture
def postgres_project(tmp_path: Path) -> Iterator[dict[str, Any]]:
    """A disposable, single-service compose project mirroring the deployed Postgres service.

    Bound to a published host port (not exercised over ``docker exec``, which uses the
    container's local Unix socket and is trust-authenticated by default regardless of the
    role's real password): connections here traverse Postgres's actual network auth path,
    the same one the deployed backend uses.
    """
    if not _docker_available():
        pytest.skip("docker is not available")

    project = f"pwrotate-{uuid.uuid4().hex[:8]}"
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        textwrap.dedent(
            """\
            services:
              postgres:
                image: postgres:16-alpine
                environment:
                  POSTGRES_USER: dispute_intake
                  POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
                  POSTGRES_DB: dispute_intake
                ports:
                  - "127.0.0.1:0:5432"
                volumes:
                  - pgdata:/var/lib/postgresql/data
            volumes:
              pgdata:
            """
        )
    )

    def up(password: str) -> None:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "compose", "-p", project, "-f", str(compose_file), "up", "-d"],  # noqa: S607 - resolved through PATH by design
            cwd=tmp_path,
            env={**os.environ, "POSTGRES_PASSWORD": password},
            check=True,
            capture_output=True,
            text=True,
        )

    def port() -> int:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [  # noqa: S607 - resolved through PATH by design
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(compose_file),
                "port",
                "postgres",
                "5432",
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip().rsplit(":", 1)[-1])

    def rotate(new_password: str) -> None:
        # The extracted block runs bare "docker compose exec", exactly as 05-deploy.sh's
        # own remote script does (no -p flag, relying on the project it's already cd'd
        # into) -- COMPOSE_PROJECT_NAME steers that bare invocation at this fixture's
        # project without altering the extracted command text itself.
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["bash", "-c", _extract_rotation_block()],  # noqa: S607 - resolved through PATH by design
            cwd=tmp_path,
            env={**os.environ, "POSTGRES_PASSWORD": new_password, "COMPOSE_PROJECT_NAME": project},
            check=True,
            capture_output=True,
            text=True,
        )

    try:
        yield {"up": up, "port": port, "rotate": rotate}
    finally:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "compose", "-p", project, "-f", str(compose_file), "down", "-v"],  # noqa: S607 - resolved through PATH by design
            cwd=tmp_path,
            check=False,
            capture_output=True,
        )


def test_rotation_converges_the_live_password_on_an_already_initialized_volume(
    postgres_project: dict[str, Any],
) -> None:
    """The scenario the rotation step exists for: a redeploy reusing the existing volume."""
    up = postgres_project["up"]
    port = postgres_project["port"]
    rotate = postgres_project["rotate"]

    up("old-password-123")
    assert _wait_until(lambda: _can_connect(port(), "old-password-123"))

    # Redeploy against the SAME (already-initialized) volume with a new SSM value.
    # POSTGRES_PASSWORD alone is silently ignored past first init -- reproduce that premise
    # before proving the fix, so a change to the Postgres image's own behavior would be
    # caught here rather than silently invalidating this test.
    up("new-password-456")
    assert _wait_until(lambda: _can_connect(port(), "old-password-123")), (
        "expected POSTGRES_PASSWORD alone to be ignored on a non-fresh volume -- if this "
        "fails, the premise behind the rotation step no longer holds and needs re-deriving"
    )
    assert not _can_connect(port(), "new-password-456")

    rotate("new-password-456")

    assert _can_connect(port(), "new-password-456")
    assert not _can_connect(port(), "old-password-123")


def test_rotation_is_a_harmless_noop_on_a_freshly_initialized_volume(
    postgres_project: dict[str, Any],
) -> None:
    """On first init, POSTGRES_PASSWORD already set the role; rotation must not break it."""
    up = postgres_project["up"]
    port = postgres_project["port"]
    rotate = postgres_project["rotate"]

    up("first-init-password")
    assert _wait_until(lambda: _can_connect(port(), "first-init-password"))

    rotate("first-init-password")

    assert _can_connect(port(), "first-init-password")
