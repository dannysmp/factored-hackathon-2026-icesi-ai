"""
Deploy-Time Analytics Load Tests
================================

Component: the analytics load in ``infra/scripts/05-deploy.sh``, the compose mount that feeds it,
the post-deploy table check ``infra/scripts/13-verify-analytics.sh`` and the rule that no deploy
command can remove the dashboard container as an orphan of the base compose project.

The deploy tests read the real scripts and compose file, so an edit that moves, drops or renames
a step changes what they check. The check script is run for real against stubs of the ``aws``
CLI and of ``docker``: the stubbed ``aws`` returns the command the script would send, which is
then run with a stubbed ``docker`` that answers the row counts. No Docker, AWS or database is
needed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from pipelines.analytics_load import DEFAULT_GOLD

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "scripts" / "05-deploy.sh"
VERIFY_SCRIPT = REPO_ROOT / "infra" / "scripts" / "13-verify-analytics.sh"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile"

_COMPOSE = "docker compose -f docker-compose.yml -f docker-compose.prod.yml"
_SEED_DIR = "seed/dispute_demand"


def _remote_commands() -> list[str]:
    lines = DEPLOY_SCRIPT.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{_COMPOSE} pull"))
    end = next(i for i, line in enumerate(lines) if line == "SCRIPT")
    return [line.strip() for line in lines[start:end] if line.strip()]


def _index(commands: list[str], fragment: str) -> int:
    matches = [i for i, line in enumerate(commands) if fragment in line]
    assert matches, f"no command contains {fragment!r}: has the step been removed or renamed?"
    return matches[0]


def test_the_marts_load_after_the_seed_and_before_the_stack_is_brought_up() -> None:
    commands = _remote_commands()

    load_seed = _index(commands, "app.persistence.load_seed")
    analytics = _index(commands, "pipelines.analytics_load")
    stack = commands.index(f"{_COMPOSE} up -d")

    assert load_seed < analytics < stack
    assert commands[analytics - 1].startswith(f"{_COMPOSE} run --rm -T backend")


def test_the_marts_are_synced_to_the_directory_the_backend_mounts() -> None:
    commands = _remote_commands()

    sync = commands[_index(commands, "s3://")]
    marts_sync = next(line for line in commands if "dispute_demand/" in line and "s3 sync" in line)
    assert sync  # the operational seed sync is still there
    assert marts_sync.endswith(f"/opt/dispute-intake/{_SEED_DIR}/")
    assert f"mkdir -p /opt/dispute-intake/{_SEED_DIR}" in commands


def test_the_mounted_marts_land_where_the_loader_reads_by_default() -> None:
    workdirs = [
        line.split(maxsplit=1)[1].strip()
        for line in DOCKERFILE.read_text().splitlines()
        if line.startswith("WORKDIR ")
    ]
    volumes = yaml.safe_load(COMPOSE_PROD.read_text())["services"]["backend"]["volumes"]

    source, target, mode = f"./{_SEED_DIR}", f"{workdirs[-1]}/{DEFAULT_GOLD}", "ro"
    assert f"{source}:{target}:{mode}" in volumes


_AWS_STUB = """#!/usr/bin/env bash
case "$*" in
  *"sts get-caller-identity"*) exit 0 ;;
  *"ec2 describe-instances"*) echo i-0stub; exit 0 ;;
  *"ssm send-command"*)
    for arg in "$@"; do
      case "$arg" in file://*) cp "${arg#file://}" "$CAPTURE_PATH" ;; esac
    done
    echo cmd-stub
    exit 0 ;;
  *"get-command-invocation"*) echo Success; exit 0 ;;
esac
exit 1
"""

# Answers `SELECT count(*) FROM analytics.<mart>` from COUNTS_FILE ("mart=rows" lines); a mart
# it does not list behaves like a table that does not exist.
_DOCKER_STUB = """#!/usr/bin/env bash
query="${@: -1}"
mart="${query##*analytics.}"
count="$(grep "^${mart}=" "$COUNTS_FILE" | cut -d= -f2 || true)"
if [[ -z "${count}" ]]; then
  echo "ERROR:  relation \\"analytics.${mart}\\" does not exist" >&2
  exit 1
fi
echo "${count}"
"""

_PYTHON_STUB_BAD_NAME = """#!/usr/bin/env bash
printf 'dispute_cases_monthly\\nbad;name\\n'
"""


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _marts() -> list[str]:
    result = subprocess.run(
        ["python3", "lib/theme_metabase_dashboard.py", "--list-marts"],  # noqa: S607
        cwd=REPO_ROOT / "infra" / "scripts",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()


@pytest.fixture
def stubbed(tmp_path: Path) -> Path:
    if shutil.which("jq") is None or shutil.which("bash") is None:
        pytest.skip("jq and bash are required to run the script")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "aws", _AWS_STUB)
    _executable(bin_dir / "docker", _DOCKER_STUB)
    return tmp_path


def _run_check(work: Path, *, python_stub: str | None = None) -> subprocess.CompletedProcess[str]:
    bin_dir = work / "bin"
    if python_stub is not None:
        _executable(bin_dir / "python3", python_stub)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE_PATH": str(work / "payload.json"),
    }
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_REGION", None)
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", str(VERIFY_SCRIPT)],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_remote(work: Path, counts: dict[str, int]) -> subprocess.CompletedProcess[str]:
    """Run the command the check sends to the host, against stubbed row counts."""
    sent = _run_check(work)
    assert sent.returncode == 0, sent.stderr
    command = json.loads((work / "payload.json").read_text())["commands"][0]
    host_dir = work / "host"
    host_dir.mkdir(exist_ok=True)
    counts_file = work / "counts.txt"
    counts_file.write_text("".join(f"{mart}={rows}\n" for mart, rows in counts.items()))
    env = {
        **os.environ,
        "PATH": f"{work / 'bin'}{os.pathsep}{os.environ['PATH']}",
        "COUNTS_FILE": str(counts_file),
    }
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", "-c", command.replace("cd /opt/dispute-intake", f"cd {host_dir}")],  # noqa: S607
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_check_passes_and_prints_only_counts_when_every_table_has_rows(stubbed: Path) -> None:
    marts = _marts()
    result = _run_remote(stubbed, {mart: 37 for mart in marts})

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [f"analytics.{mart}: 37 rows" for mart in marts]


def test_the_check_fails_naming_a_table_with_no_rows(stubbed: Path) -> None:
    marts = _marts()
    counts = {mart: 5 for mart in marts}
    counts[marts[1]] = 0
    result = _run_remote(stubbed, counts)

    assert result.returncode == 1
    assert f"analytics.{marts[1]}: 0 rows" in result.stdout
    assert "holds no rows" in result.stderr


def test_the_check_fails_on_a_table_that_does_not_exist(stubbed: Path) -> None:
    marts = _marts()
    result = _run_remote(stubbed, {mart: 5 for mart in marts[1:]})

    assert result.returncode != 0
    assert marts[0] in result.stderr


def test_the_check_refuses_a_table_name_that_is_not_plain(stubbed: Path) -> None:
    result = _run_check(stubbed, python_stub=_PYTHON_STUB_BAD_NAME)

    assert result.returncode == 1
    assert "not a plain table name" in result.stderr
    assert not (stubbed / "payload.json").exists()


_ORPHAN_RULE_FILES = (
    "infra",
    ".github",
    "docs",
    "Makefile",
    "README.md",
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.metabase.yml",
)
_OVERLAY_FLAG = "-f docker-compose.metabase.yml"
_ORPHAN_REMOVAL = re.compile(r"--remove-orphans|COMPOSE_REMOVE_ORPHANS")


def _logical_lines(text: str) -> list[str]:
    return re.sub(r"\\\n\s*", " ", text).splitlines()


def _tracked_texts() -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    for name in _ORPHAN_RULE_FILES:
        root = REPO_ROOT / name
        paths = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
        for path in paths:
            try:
                found.append((path, path.read_text()))
            except UnicodeDecodeError:
                continue
    return found


def test_no_command_can_remove_the_dashboard_container_as_an_orphan() -> None:
    """The dashboard container belongs to the third compose file, so a command that names only
    the base and prod files would delete it if it asked compose to remove orphans."""
    offending = [
        f"{path.relative_to(REPO_ROOT)}: {line.strip()}"
        for path, text in _tracked_texts()
        for line in _logical_lines(text)
        if _ORPHAN_REMOVAL.search(line) and _OVERLAY_FLAG not in line
    ]

    assert offending == []
