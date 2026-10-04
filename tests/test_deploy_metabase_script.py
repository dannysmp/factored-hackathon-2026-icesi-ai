"""
Metabase Provisioning Command Tests
===================================

Component: the remote command ``infra/scripts/08-deploy-metabase.sh`` sends to the host over
SSM. The tests run the real script with stub ``aws`` and the real ``jq`` on the path, capture
the ``--parameters`` payload the script hands to ``aws ssm send-command``, and check the
command text the host would execute. No Docker, no network and no AWS account are needed.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "infra" / "scripts" / "08-deploy-metabase.sh"
OVERLAY = REPO_ROOT / "docker-compose.metabase.yml"

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

_OVERLAY_COMPOSE_FLAG = "-f docker-compose.metabase.yml"


@pytest.fixture(scope="module")
def remote_command(tmp_path_factory: pytest.TempPathFactory) -> str:
    """The command text the script sends to the host, captured from a real run of the script."""
    if shutil.which("jq") is None or shutil.which("bash") is None:
        pytest.skip("jq and bash are required to run the script")

    work = tmp_path_factory.mktemp("metabase-script")
    bin_dir = work / "bin"
    bin_dir.mkdir()
    aws = bin_dir / "aws"
    aws.write_text(_AWS_STUB)
    aws.chmod(aws.stat().st_mode | stat.S_IXUSR)
    capture = work / "payload.json"

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CAPTURE_PATH": str(capture),
    }
    env.pop("AWS_PROFILE", None)
    env.pop("AWS_REGION", None)
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", str(SCRIPT)],  # noqa: S607 - resolved through PATH by design
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    commands = json.loads(capture.read_text())["commands"]
    assert len(commands) == 1
    return str(commands[0])


def test_the_rendered_command_is_valid_shell(remote_command: str) -> None:
    result = subprocess.run(
        ["bash", "-n"],  # noqa: S607 - resolved through PATH by design
        input=remote_command,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_overlay_is_written_before_any_compose_call_that_names_it(
    remote_command: str,
) -> None:
    lines = remote_command.splitlines()
    write_lines = [
        i
        for i, line in enumerate(lines)
        if "base64 -d" in line and line.rstrip().endswith("/docker-compose.metabase.yml")
    ]
    compose_lines = [
        i
        for i, line in enumerate(lines)
        if "docker compose" in line and _OVERLAY_COMPOSE_FLAG in line
    ]
    assert len(write_lines) == 1, "the overlay must be written exactly once"
    assert compose_lines, "no compose call names the overlay"
    assert write_lines[0] < min(compose_lines)


def test_the_written_overlay_is_the_repository_file_byte_for_byte(remote_command: str) -> None:
    match = re.search(
        r"^echo '(?P<payload>[A-Za-z0-9+/=]+)' \| base64 -d "
        r">/opt/dispute-intake/docker-compose\.metabase\.yml$",
        remote_command,
        re.M,
    )
    assert match, "the overlay write line was not found"
    assert base64.b64decode(match.group("payload")) == OVERLAY.read_bytes()


def test_metabase_starts_without_its_dependencies(remote_command: str) -> None:
    start_lines = [line for line in remote_command.splitlines() if " up -d " in line]
    assert len(start_lines) == 1
    assert start_lines[0].endswith(" up -d --no-deps metabase")


def test_every_compose_call_to_the_proxy_names_the_service_files(remote_command: str) -> None:
    proxy_lines = [line for line in remote_command.splitlines() if "exec -T caddy" in line]
    assert len(proxy_lines) == 1
    assert proxy_lines[0].startswith(
        "docker compose -f docker-compose.yml -f docker-compose.prod.yml "
        "-f docker-compose.metabase.yml exec"
    )
