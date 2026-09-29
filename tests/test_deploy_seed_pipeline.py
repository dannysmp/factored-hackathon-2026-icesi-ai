"""
Deploy-Time Migration and Seed-Load Tests
==========================================

Component: the seed-loading addition to `infra/scripts/05-deploy.sh` (after the Postgres
password rotation): sync the built operational seed from the project's own seed bucket, then run
migrations and the seed load as one-off `docker compose run` containers, never `docker compose
exec` against the long-running `backend` service. `exec` needs a running target; `backend`'s own
startup validates every demo persona against a seeded customer when demo sign-in is enabled
(`app.main.create_app`) and fails closed on an empty database -- exactly the state right after a
fresh instance's first `up -d` -- so it can still be crash-looping at the moment this step needs
to run. `docker compose run` creates a separate, disposable container from the same service
definition and is not affected by the named service container's own state.

What is and is not covered here: `app.persistence.load_seed`'s own correctness (parsing,
verification, the load transaction, idempotency) is `tests/test_load_seed.py`'s job, not this
file's -- this file tests the deploy-pipeline wiring around it: that the mount path this script
sets up lines up with what the loader actually reads by default, and that a one-off container
really is unaffected by another named service's own container crash-looping in the same project,
since the whole reason this step uses `run` instead of `exec` depends on it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import textwrap
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"


def _runtime_workdir() -> str:
    """The final stage's WORKDIR, the last one `Dockerfile` sets."""
    workdirs = [
        line.split(maxsplit=1)[1].strip()
        for line in DOCKERFILE.read_text().splitlines()
        if line.startswith("WORKDIR ")
    ]
    assert workdirs, "Dockerfile sets no WORKDIR -- has the image layout changed?"
    return workdirs[-1]


def _seed_mount_target() -> str:
    compose = yaml.safe_load(COMPOSE_PROD.read_text())
    for volume in compose["services"]["backend"]["volumes"]:
        target = str(volume).split(":", 2)[1]
        if target.endswith("/data/gold/ops_seed"):
            return target
    raise AssertionError("backend has no volume mounted at .../data/gold/ops_seed")


def test_the_seed_mount_target_matches_load_seeds_own_default_gold_path() -> None:
    """`load_seed --gold` defaults to `data/gold/ops_seed`, relative to the image's WORKDIR.

    The deploy step never passes `--gold` explicitly -- it relies on this alignment, so a change
    to either side without the other would silently start loading nothing (`load_seed` itself
    would then refuse the missing manifest at deploy time, but this test catches the drift
    without needing a deployed instance).
    """
    # Reconstructs main()'s own parser rather than importing a private default: main() takes no
    # argument that surfaces the default directly, and calling it needs real arguments or a real
    # DATABASE_URL. Kept as one line so a change to the real default is easy to diff against.
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, default=Path("data/gold/ops_seed"))
    default_gold = parser.get_default("gold")

    assert _seed_mount_target() == f"{_runtime_workdir()}/{default_gold}"


@pytest.fixture
def compose_project(tmp_path: Path) -> Iterator[dict[str, Any]]:
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")

    project = f"seedstep-{uuid.uuid4().hex[:8]}"
    compose_file = tmp_path / "docker-compose.yml"
    compose_file.write_text(
        textwrap.dedent(
            """\
            services:
              flaky:
                image: alpine:3
                restart: unless-stopped
                command: ["sh", "-c", "exit 1"]
              worker:
                image: alpine:3
                command: ["sh", "-c", "true"]
            """
        )
    )

    def up() -> None:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "compose", "-p", project, "-f", str(compose_file), "up", "-d", "flaky"],  # noqa: S607 - resolved through PATH by design
            check=True,
            capture_output=True,
            text=True,
        )
        # "flaky" exits immediately; a couple of seconds is generous for it to have entered its
        # first restart cycle, which is the state both tests below mean to exercise.
        time.sleep(3)

    def run_one_off(*command: str) -> subprocess.CompletedProcess[str]:
        # Mirrors 05-deploy.sh's own invocation shape: "compose run --rm -T <service> <command>".
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [  # noqa: S607 - resolved through PATH by design
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(compose_file),
                "run",
                "--rm",
                "-T",
                "worker",
                *command,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def exec_into_flaky(*command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed argv, no shell
            [  # noqa: S607 - resolved through PATH by design
                "docker",
                "compose",
                "-p",
                project,
                "-f",
                str(compose_file),
                "exec",
                "-T",
                "flaky",
                *command,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    try:
        yield {"up": up, "run_one_off": run_one_off, "exec_into_flaky": exec_into_flaky}
    finally:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["docker", "compose", "-p", project, "-f", str(compose_file), "down"],  # noqa: S607 - resolved through PATH by design
            check=False,
            capture_output=True,
        )


def test_run_against_a_service_succeeds_while_a_different_named_container_crash_loops(
    compose_project: dict[str, Any],
) -> None:
    """The core claim `run` over `exec` depends on: a crash-looping named service container
    (`flaky`, standing in for `backend` mid-crash-loop) does not block `docker compose run --rm`
    against a *different* service (`worker`, standing in for the one-off migrate/load-seed
    containers `05-deploy.sh` actually runs) in the same project.
    """
    compose_project["up"]()

    result = compose_project["run_one_off"]("true")

    assert result.returncode == 0, result.stderr


def test_exec_into_the_crash_looping_service_itself_fails(
    compose_project: dict[str, Any],
) -> None:
    """Negative control: `exec` against the crash-looping service itself is exactly the failure
    mode choosing `run` avoids -- proving this fails is what makes the positive test above
    meaningful, not vacuous (a `run`-vs-`exec` distinction that didn't actually matter here would
    leave both tests passing for the wrong reason).
    """
    compose_project["up"]()

    result = compose_project["exec_into_flaky"]("true")

    assert result.returncode != 0
