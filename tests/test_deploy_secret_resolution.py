"""
Secret Export and Optional-Secret Resolution Tests
====================================================

Component: the secret-resolution block ``infra/scripts/05-deploy.sh``'s remote script runs
before ``docker compose ... up -d`` -- the three mandatory secrets (``ANTHROPIC_API_KEY``,
``SESSION_SIGNING_KEY``, ``POSTGRES_PASSWORD``), ``resolve_optional_secret()``, and the three
optional secrets it resolves. A genuine SSM lookup failure on any of the six
secrets must refuse the deploy rather than continue toward ``docker compose up`` with an empty
value. A prior version silently swallowed this: ``export VAR="$(cmd)"`` masks a failing command
substitution's own exit status behind ``export``'s own success under ``set -euo pipefail``, so a
non-``ParameterNotFound`` SSM error logged and then kept going. These tests extract the real
block from the script and execute it under a stub ``aws`` CLI, so a revert or edit of the
export-splitting fix changes what this test executes, not a hand-copied duplicate of it. Needs
only ``bash``; no Docker or real AWS access required.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "infra" / "scripts" / "05-deploy.sh"

_STUB_AWS = """#!/usr/bin/env bash
# Stub aws CLI: understands only "ssm get-parameter --name <name> ...". Behavior per parameter
# name is driven by env vars the test sets, so one stub covers every scenario below.
set -euo pipefail
if [[ "${1:-}" != "ssm" || "${2:-}" != "get-parameter" ]]; then
  echo "stub aws: unsupported invocation: $*" >&2
  exit 98
fi
name=""
prev=""
for arg in "$@"; do
  if [[ "${prev}" == "--name" ]]; then name="${arg}"; fi
  prev="${arg}"
done
case "${name}" in
  */anthropic-api-key)
    if [[ "${STUB_MANDATORY_FAIL:-}" == "1" ]]; then
      echo "An error occurred (AccessDeniedException): User is not authorized" >&2
      exit 254
    fi
    echo "dummy-anthropic-key" ;;
  */session-signing-key) echo "dummy-session-key" ;;
  */postgres-password) echo "dummy-postgres-password" ;;
  */demo-signin-access-code)
    case "${STUB_OPTIONAL_MODE:-not_found}" in
      access_denied)
        echo "An error occurred (AccessDeniedException): User is not authorized" >&2
        exit 254 ;;
      *)
        echo "An error occurred (ParameterNotFound) when calling the GetParameter operation:" >&2
        exit 254 ;;
    esac ;;
  */demo-agent-access-code|*/agent-session-signing-key)
    echo "An error occurred (ParameterNotFound) when calling the GetParameter operation:" >&2
    exit 254 ;;
  *)
    echo "stub aws: unexpected parameter name: ${name}" >&2
    exit 99 ;;
esac
"""


def _extract_secret_export_block() -> str:
    """The exact secret-resolution block from 05-deploy.sh's remote script.

    Reads the real file rather than a hand-copied duplicate, so a revert or edit of the
    export-splitting fix changes what this test executes, not just what it would display.
    """
    content = DEPLOY_SCRIPT.read_text()
    match = re.search(
        r"cd /opt/dispute-intake\n"
        r'(?P<block>.+?echo "demo sign-in state: DEMO_SIGNIN_ENABLED=\\\$\{DEMO_SIGNIN_ENABLED\} '
        r'DEMO_AGENT_SIGNIN_ENABLED=\\\$\{DEMO_AGENT_SIGNIN_ENABLED\}"\n)',
        content,
        re.S,
    )
    assert match, (
        "the secret-resolution block was not found in 05-deploy.sh -- has it moved or been removed?"
    )
    block = match.group("block")
    assert "export ANTHROPIC_API_KEY" in block and "resolve_optional_secret" in block, (
        "the extracted range no longer contains the secret exports -- has the block layout changed?"
    )
    # Undo the unquoted heredoc's own escaping of command substitutions ("\$" -> "$"), the same
    # processing bash performs when 05-deploy.sh builds the remote script it sends over SSM.
    # "set -euo pipefail" is the remote script's own first line, well before this block -- it
    # stays in effect throughout, so it is prepended here too; running the block without it
    # would test a different (non-failing-fast) shell than the one this actually runs under.
    return "set -euo pipefail\n" + block.replace("\\$", "$")


@pytest.fixture
def stub_aws(tmp_path: Path) -> Path:
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    stub_path = stub_dir / "aws"
    stub_path.write_text(_STUB_AWS)
    stub_path.chmod(0o755)
    return stub_dir


def _run_block(stub_bin: Path, script: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PATH": f"{stub_bin}:{os.environ.get('PATH', '')}",
        "SSM_SECRET_PREFIX": "/dispute-intake/demo",
        "ECR_REGISTRY": "dummy-registry",
        "IMAGE_TAG": "dummy-tag",
        "HOST_NAME": "dummy-host",
        **extra_env,
    }
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["bash", "-c", script],  # noqa: S607 - resolved through PATH by design
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_a_mandatory_secret_access_failure_refuses_the_deploy(stub_aws: Path) -> None:
    """The core regression: a genuine SSM error must never look like a clean deploy."""
    result = _run_block(stub_aws, _extract_secret_export_block(), STUB_MANDATORY_FAIL="1")

    assert result.returncode != 0
    assert "demo sign-in state:" not in result.stdout, (
        "the script reached the post-secrets log line despite a mandatory secret lookup "
        "failing -- the export-masking bug is back"
    )


def test_an_optional_secret_access_failure_refuses_the_deploy(stub_aws: Path) -> None:
    result = _run_block(
        stub_aws, _extract_secret_export_block(), STUB_OPTIONAL_MODE="access_denied"
    )

    assert result.returncode != 0
    assert "demo sign-in state:" not in result.stdout, (
        "the script reached the post-secrets log line despite an optional secret lookup "
        "failing with a non-ParameterNotFound error -- the export-masking bug is back"
    )
    assert "refusing: unexpected error reading optional secret" in result.stderr


def test_the_healthy_path_logs_the_derived_signin_state_and_succeeds(stub_aws: Path) -> None:
    result = _run_block(stub_aws, _extract_secret_export_block())

    assert result.returncode == 0, result.stderr
    assert (
        "demo sign-in state: DEMO_SIGNIN_ENABLED=false DEMO_AGENT_SIGNIN_ENABLED=false"
        in result.stdout
    )
