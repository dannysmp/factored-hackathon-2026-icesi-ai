"""
Model Key Containment Tests
============================

Component: the repository tree and the two image build contexts (``Dockerfile`` with the root
``.dockerignore``, ``web/Dockerfile`` with ``web/.dockerignore``). The model API key is a static
third-party secret that exists only in SSM Parameter Store and is read at start-up; it must never
be in a tracked file, in a file a build can copy into an image, or in an image build instruction.

A real key is never used. The detector looks for the provider key's shape; the self-checks build a
sentinel of that shape at run time (so this file never contains a match) and prove the scan flags
it, so a clean result is not a scan that cannot fail.
"""

from __future__ import annotations

# Standard libraries
import re
import subprocess
from pathlib import Path

# Third-party libraries
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_KEY_SHAPE = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")
_SENTINEL = "sk-ant-" + "api03-" + "A1b2C3d4" * 5
_SKIPPED_DIRS = {"__pycache__", "node_modules", ".venv", ".git", "dist", "coverage"}


def _tracked_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git on PATH
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [REPO_ROOT / name for name in listing.split("\0") if name]


def _files_with_a_key(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _KEY_SHAPE.search(text):
            found.append(path)
    return found


def _walk(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file() and not _SKIPPED_DIRS.intersection(path.relative_to(root).parts)
    ]


def _dockerignore_rules(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _backend_context() -> list[Path]:
    """Every file the backend image build can copy: the root ignore file allows a short list."""
    rules = _dockerignore_rules(REPO_ROOT / ".dockerignore")
    assert rules[0] == "*", "the backend context must start from deny-all"
    assert all(rule.startswith("!") for rule in rules[1:]), (
        "after deny-all the ignore file may only re-include paths; "
        "extend this test before adding another rule shape"
    )
    allowed = [rule[1:].rstrip("/") for rule in rules[1:]]
    files: list[Path] = []
    for entry in allowed:
        target = REPO_ROOT / entry
        files.extend(_walk(target) if target.is_dir() else [target])
    return files


def test_the_scan_flags_a_key_shaped_value_and_passes_a_clean_file(tmp_path: Path) -> None:
    planted = tmp_path / "planted.txt"
    planted.write_text(f"ANTHROPIC_API_KEY={_SENTINEL}\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}\n", encoding="utf-8")

    assert _files_with_a_key([planted, clean]) == [planted]


def test_no_tracked_file_holds_a_model_key() -> None:
    assert _files_with_a_key(_tracked_files()) == []


def test_the_backend_build_context_holds_no_key_and_no_environment_file() -> None:
    context = _backend_context()

    assert context, "the backend context must not be empty"
    assert _files_with_a_key(context) == []
    assert [path for path in context if path.name.startswith(".env")] == []


def test_the_backend_context_covers_every_path_its_dockerfile_copies() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    copied = {
        token.removeprefix("./").rstrip("/")
        for match in re.finditer(r"^COPY\s+(?!--from)(.+)$", dockerfile, re.MULTILINE)
        for token in match.group(1).split()[:-1]
    }
    allowed = {
        rule[1:].rstrip("/") for rule in _dockerignore_rules(REPO_ROOT / ".dockerignore")[1:]
    }

    assert copied <= allowed, f"copied but not allowed by the ignore file: {copied - allowed}"


def test_the_web_build_context_excludes_environment_files_and_holds_no_key() -> None:
    rules = _dockerignore_rules(REPO_ROOT / "web" / ".dockerignore")
    context = _walk(REPO_ROOT / "web")

    assert {".env", ".env.*"} <= set(rules)
    assert _files_with_a_key(context) == []


@pytest.mark.parametrize("name", ["Dockerfile", "web/Dockerfile"])
def test_no_image_build_instruction_carries_a_model_key(name: str) -> None:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    instructions = [line for line in text.splitlines() if re.match(r"\s*(ARG|ENV)\b", line)]

    assert [line for line in instructions if re.search(r"ANTHROPIC|API_KEY", line)] == []


def test_the_compose_files_pass_the_key_through_without_a_value() -> None:
    offending: list[str] = []
    for path in REPO_ROOT.glob("docker-compose*.yml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "ANTHROPIC_API_KEY" in line and not line.lstrip().startswith("#"):
                value = line.split(":", 1)[1].strip()
                if value not in {"${ANTHROPIC_API_KEY}", "${ANTHROPIC_API_KEY:-}"}:
                    offending.append(f"{path.name}: {line.strip()}")

    assert offending == []


def test_the_local_environment_file_is_not_tracked_and_is_ignored() -> None:
    tracked = {path.name for path in _tracked_files()}
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],  # noqa: S607 - git on PATH
        cwd=REPO_ROOT,
        check=False,
    )

    assert ".env" not in tracked
    assert ignored.returncode == 0
