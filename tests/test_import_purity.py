"""
Import Purity Tests
===================

Component: every module under ``app``. Hermetic: spawns a fresh interpreter per check.
Protects the rule "loading a module never reads configuration or opens resources" — the
engineering lesson that makes tests need neither network nor environment.
"""

from __future__ import annotations

# Standard libraries
import os  # Build a controlled child environment
import subprocess  # Fresh interpreter so already-imported modules cannot mask a violation
import sys  # Interpreter path of the running environment
from pathlib import Path  # Repository root and a temp working directory

# Third-party libraries
import pytest  # Test runner and fixtures

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", ["app", "app.config", "app.main"])
def test_import_succeeds_with_invalid_configuration(module: str, tmp_path: Path) -> None:
    """Importing must not validate settings: invalid env values only fail in load_settings()."""
    env = {
        **{k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SYSTEMROOT"}},
        "PYTHONPATH": str(_REPO_ROOT),
        "LOG_LEVEL": "definitely-not-a-level",
        "NLU_MODEL": "not-a-model",
    }

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted interpreter path
        [sys.executable, "-c", f"import {module}"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
