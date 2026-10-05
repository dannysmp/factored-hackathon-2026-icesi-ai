"""
Data Command Guard Tests
========================

Component: the ``require-raw-data`` and ``require-silver`` prerequisites of the ``Makefile``
data commands. The tests run the real ``Makefile`` from a temporary directory with the command
runner replaced by ``echo``, so a command that gets past its guard is visible in the output and
nothing is ever executed or written. No data, no network and no ``uv`` are needed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

MAKE = shutil.which("make")

pytestmark = pytest.mark.skipif(MAKE is None, reason="make is not installed")

RAW_COMMANDS = ("profile", "pipeline")
SILVER_COMMANDS = ("analyze", "features")


def _make(workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the repository Makefile in ``workdir`` with every recipe line printed, not run."""
    shutil.copy(REPO_ROOT / "Makefile", workdir / "Makefile")
    return subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [str(MAKE), "RUN=echo RAN", *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("command", RAW_COMMANDS)
def test_a_command_that_needs_the_raw_data_stops_before_its_recipe_when_it_is_absent(
    tmp_path: Path, command: str
) -> None:
    result = _make(tmp_path, command, f"DATA_DIR={tmp_path / 'absent'}")

    assert result.returncode != 0
    assert "no raw data at" in result.stderr
    assert "Nothing was changed." in result.stderr
    assert "RAN python" not in result.stdout


@pytest.mark.parametrize("command", SILVER_COMMANDS)
def test_a_command_that_needs_the_cleaned_layer_stops_before_its_recipe_when_it_is_absent(
    tmp_path: Path, command: str
) -> None:
    result = _make(tmp_path, command, f"SILVER_DIR={tmp_path / 'absent'}")

    assert result.returncode != 0
    assert "no cleaned layer at" in result.stderr
    assert "Nothing was changed." in result.stderr
    assert "RAN python" not in result.stdout


@pytest.mark.parametrize("command", RAW_COMMANDS)
def test_a_command_that_needs_the_raw_data_runs_its_recipe_when_the_directory_exists(
    tmp_path: Path, command: str
) -> None:
    present = tmp_path / "raw"
    present.mkdir()

    result = _make(tmp_path, command, f"DATA_DIR={present}")

    assert result.returncode == 0
    assert "RAN python -m pipelines." in result.stdout


@pytest.mark.parametrize("command", SILVER_COMMANDS)
def test_a_command_that_needs_the_cleaned_layer_runs_its_recipe_when_the_directory_exists(
    tmp_path: Path, command: str
) -> None:
    present = tmp_path / "silver"
    present.mkdir()

    result = _make(tmp_path, command, f"SILVER_DIR={present}")

    assert result.returncode == 0
    assert "RAN python -m pipelines." in result.stdout
