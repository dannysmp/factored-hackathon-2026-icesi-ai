"""
Import Purity Tests
===================

Component: every module under ``app``. Hermetic: spawns a fresh interpreter per check.
Protects the rule "loading a module never reads configuration or opens resources" — the
engineering lesson that makes tests need neither network nor environment.

Two probes run at import time inside the child interpreter:
- an environment probe records every read of the service's own variables, so even a
  non-validating read (a value cached in a module constant) is caught;
- an audit hook records every attempt to open a ``.env`` file placed in the working directory.
"""

from __future__ import annotations

# Standard libraries
import json  # Child interpreter reports its observations as JSON
import os  # Build a controlled child environment
import subprocess  # Fresh interpreter so already-imported modules cannot mask a violation
import sys  # Interpreter path of the running environment
import textwrap  # Readable child-interpreter program
from pathlib import Path  # Repository root and a temp working directory

# Third-party libraries
import pytest  # Test runner and fixtures

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Variables owned by app.config; an import that touches any of them is not pure.
_SERVICE_KEYS = [
    "APP_ENV",
    "LOG_LEVEL",
    "SERVICE_VERSION",
    "LLM_PROVIDER",
    "NLU_MODEL",
    "RENDER_MODEL",
    "ANTHROPIC_API_KEY",
]

# Program run in the child: install the probes, import the module, print what was observed.
_PROBE = textwrap.dedent(
    """
    import importlib, json, os, sys

    keys = set(json.loads(sys.argv[2]))
    env_reads, env_files = [], []

    class Probe(dict):
        def __getitem__(self, key):
            if key in keys:
                env_reads.append(key)
            return super().__getitem__(key)

        def get(self, key, default=None):
            if key in keys:
                env_reads.append(key)
            return super().get(key, default)

        def __contains__(self, key):
            if key in keys:
                env_reads.append(key)
            return super().__contains__(key)

    os.environ = Probe(os.environ)

    def hook(event, args):
        if event == "open" and str(args[0]).endswith(".env"):
            env_files.append(str(args[0]))

    sys.addaudithook(hook)
    importlib.import_module(sys.argv[1])
    print(json.dumps({"env_reads": sorted(set(env_reads)), "env_files": env_files}))
    """
)


def _import_in_fresh_interpreter(
    module: str, workdir: Path, extra_env: dict[str, str]
) -> dict[str, list[str]]:
    """Import ``module`` in a clean child process and return the probes' observations."""
    env = {
        **{k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT"}},
        "PYTHONPATH": str(_REPO_ROOT),
        **extra_env,
    }
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted interpreter path
        [sys.executable, "-c", _PROBE, module, json.dumps(_SERVICE_KEYS)],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    observed: dict[str, list[str]] = json.loads(result.stdout.strip().splitlines()[-1])
    return observed


@pytest.mark.parametrize("module", ["app", "app.config", "app.main"])
def test_import_succeeds_with_invalid_configuration(module: str, tmp_path: Path) -> None:
    """Importing must not validate settings: invalid env values only fail in load_settings()."""
    observed = _import_in_fresh_interpreter(
        module, tmp_path, {"LOG_LEVEL": "definitely-not-a-level", "NLU_MODEL": "not-a-model"}
    )

    assert observed["env_reads"] == []


@pytest.mark.parametrize("module", ["app", "app.config", "app.main"])
def test_import_never_reads_service_variables_or_env_file(module: str, tmp_path: Path) -> None:
    """With *valid* values and a real .env present, import still reads none of them."""
    (tmp_path / ".env").write_text("SERVICE_VERSION=from-file\n")

    observed = _import_in_fresh_interpreter(
        module, tmp_path, {"LOG_LEVEL": "INFO", "APP_ENV": "local", "ANTHROPIC_API_KEY": "x"}
    )

    assert observed == {"env_reads": [], "env_files": []}


def test_probe_detects_a_non_validating_environment_read(tmp_path: Path) -> None:
    """Revert check: a module that only caches a service variable is caught by the probe."""
    package = tmp_path / "impure"
    package.mkdir()
    (package / "__init__.py").write_text("import os\nCACHED = os.environ.get('LOG_LEVEL')\n")

    observed = _import_in_fresh_interpreter(
        "impure", tmp_path, {"PYTHONPATH": f"{tmp_path}:{_REPO_ROOT}"}
    )

    assert observed["env_reads"] == ["LOG_LEVEL"]


def test_probe_detects_a_dotenv_file_read(tmp_path: Path) -> None:
    """Revert check: a module that opens a dotenv file at import is caught by the audit hook."""
    (tmp_path / ".env").write_text("SERVICE_VERSION=x\n")
    package = tmp_path / "impure_file"
    package.mkdir()
    (package / "__init__.py").write_text("DATA = open('.env').read()  # noqa: SIM115\n")

    observed = _import_in_fresh_interpreter(
        "impure_file", tmp_path, {"PYTHONPATH": f"{tmp_path}:{_REPO_ROOT}"}
    )

    assert observed["env_files"] == [".env"]
