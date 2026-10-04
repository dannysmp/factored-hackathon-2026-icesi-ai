"""
Model Key Containment Tests
============================

Component: the repository tree and the two image build contexts (``Dockerfile`` with the root
``.dockerignore``, ``web/Dockerfile`` with ``web/.dockerignore``). The model API key is a static
third-party secret that lives only in SSM Parameter Store; the deploy script fetches it on the
host and passes it to the container as an environment variable. It must never be in a tracked
file, in a file a build can copy into an image, or in an image build instruction.

Design Principles
-----------------
- A real key is never used. The detector looks for the provider key's shape; the self-checks build
  a sentinel of that shape at run time (so this file never contains a match) and prove the scan
  flags it, so a clean result is not a scan that cannot fail.
- The scan reads raw bytes and fails on a file it cannot read: "cannot inspect" is never "clean".
- Each build context is modelled the way Docker applies its ignore file, and the helpers take a
  root path so a temporary tree can prove each rule can fail.

Limitations
-----------
- Detection is by shape (``sk-ant-`` plus at least twenty key characters), in ASCII and UTF-16
  (either byte order). A key that is base64-encoded, split across lines or otherwise transformed
  is not found.
- Untracked files are only seen in the two build contexts and only on the disk running the test.
- The built image layers are not inspected; history is covered separately by the secret scan.
- Only the instruction forms the repository uses are modelled: ``ARG``, ``ENV``, ``COPY`` and
  ``ADD`` (any case, continuation lines, exec form), and ``KEY: value`` or ``- KEY=value`` in
  compose files. The ignore files must be deny-all plus re-includes (backend) or plain names
  (web); any other shape fails the test rather than being guessed at.
"""

from __future__ import annotations

# Standard libraries
import fnmatch
import json
import re
import subprocess
from pathlib import Path

# Third-party libraries
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# The provider key's shape, as ASCII and as UTF-16 in both byte orders.
_KEY_SHAPE = re.compile(
    rb"sk-ant-[A-Za-z0-9_-]{20,}"
    rb"|s\x00k\x00-\x00a\x00n\x00t\x00-\x00(?:[A-Za-z0-9_-]\x00){20,}"
    rb"|\x00s\x00k\x00-\x00a\x00n\x00t\x00-(?:\x00[A-Za-z0-9_-]){20,}"
)
# Built at run time from two halves so this file never contains a match for its own pattern.
_SENTINEL = "sk-ant-" + "api03-" + "A1b2C3d4" * 5

_COMPOSE_PATTERNS = ("docker-compose*.yml", "docker-compose*.yaml", "compose*.yml", "compose*.yaml")
_PASS_THROUGH = {"${ANTHROPIC_API_KEY}", "${ANTHROPIC_API_KEY:-}"}


def _tracked_files() -> list[Path]:
    """Every file git tracks that exists on disk."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git on PATH
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    paths = (REPO_ROOT / name for name in listing.split("\0") if name)
    return [path for path in paths if path.is_file()]


def _files_with_a_key(paths: list[Path]) -> list[Path]:
    """The files among ``paths`` holding a key-shaped value; an unreadable file raises."""
    return [path for path in paths if _KEY_SHAPE.search(path.read_bytes())]


def _walk(root: Path, *, excluded: tuple[str, ...] = ()) -> list[Path]:
    """Every file under ``root`` except those whose first path part matches an ``excluded`` name."""
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(fnmatch.fnmatchcase(path.relative_to(root).parts[0], rule) for rule in excluded)
    ]


def _dockerignore_rules(path: Path) -> list[str]:
    """The rules of an ignore file, without blank lines and comments."""
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _backend_allowed(root: Path) -> list[str]:
    """The paths the root ignore file re-includes after its deny-all rule."""
    rules = _dockerignore_rules(root / ".dockerignore")
    assert rules[0] == "*", "the backend context must start from deny-all"
    assert all(rule.startswith("!") for rule in rules[1:]), (
        "after deny-all the ignore file may only re-include paths; "
        "extend this test before adding another rule shape"
    )
    allowed = [rule[1:].rstrip("/") for rule in rules[1:]]
    assert not [entry for entry in allowed if re.search(r"[*?\[]", entry)], (
        "a re-include with a wildcard is not modelled; extend this test before adding one"
    )
    missing = [entry for entry in allowed if not (root / entry).exists()]
    assert not missing, f"the ignore file re-includes paths that do not exist: {missing}"
    return allowed


def _backend_context(root: Path) -> list[Path]:
    """Every file the backend image build can copy; a re-included directory brings its subtree."""
    files: list[Path] = []
    for entry in _backend_allowed(root):
        target = root / entry
        files.extend(_walk(target) if target.is_dir() else [target])
    return files


def _web_context(root: Path) -> list[Path]:
    """Every file the web image build can copy: all of ``root`` except its ignored root entries."""
    rules = _dockerignore_rules(root / ".dockerignore")
    assert not [rule for rule in rules if "/" in rule or rule.startswith("!")], (
        "the web ignore file is modelled as plain root-level names; extend this test first"
    )
    return _walk(root, excluded=tuple(rules))


def _instructions(text: str) -> list[tuple[str, str]]:
    """``(KEYWORD, arguments)`` for each Dockerfile instruction, continuation lines joined."""
    joined = re.sub(r"\\[ \t]*\n", " ", text)
    found: list[tuple[str, str]] = []
    for line in joined.splitlines():
        match = re.match(r"\s*([A-Za-z]+)\s+(.*)$", line)
        if match and not line.lstrip().startswith("#"):
            found.append((match.group(1).upper(), match.group(2).strip()))
    return found


def _copy_sources(text: str) -> set[str]:
    """The build-context paths ``COPY`` and ``ADD`` read; copies from another stage are skipped."""
    sources: set[str] = set()
    for keyword, arguments in _instructions(text):
        if keyword not in {"COPY", "ADD"} or "--from" in arguments:
            continue
        tokens = (
            json.loads(arguments)
            if arguments.startswith("[")
            else [token for token in arguments.split() if not token.startswith("--")]
        )
        sources.update(token.removeprefix("./").rstrip("/") or "." for token in tokens[:-1])
    return sources


def _compose_values(text: str) -> list[str]:
    """The value given to ``ANTHROPIC_API_KEY`` on each non-comment line that sets it."""
    values: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if "ANTHROPIC_API_KEY" not in line or line.startswith("#"):
            continue
        entry = line.removeprefix("-").strip()
        if entry == "ANTHROPIC_API_KEY":
            continue
        values.append(re.split(r"[:=]", entry, maxsplit=1)[1].strip().strip("\"'"))
    return values


def test_the_scan_flags_a_key_shaped_value_and_passes_a_clean_file(tmp_path: Path) -> None:
    """The detector can fail: a planted sentinel is flagged and a pass-through line is not."""
    planted = tmp_path / "planted.txt"
    planted.write_text(f"ANTHROPIC_API_KEY={_SENTINEL}\n", encoding="utf-8")
    clean = tmp_path / "clean.txt"
    clean.write_text("ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}\n", encoding="utf-8")

    assert _files_with_a_key([planted, clean]) == [planted]


@pytest.mark.parametrize(
    "content",
    [
        b"caf\xe9 KEY=" + _SENTINEL.encode(),
        b"\x89PNG\r\n\x1a\n\x00\x00" + _SENTINEL.encode() + b"\x00\xff",
        _SENTINEL.encode("utf-16"),
        _SENTINEL.encode("utf-16-be"),
    ],
    ids=["latin1-byte", "binary", "utf16-with-bom", "utf16-big-endian"],
)
def test_the_scan_flags_a_key_in_a_file_that_is_not_valid_utf8(
    tmp_path: Path, content: bytes
) -> None:
    """A key is found in a file text decoding would reject, so such a file is never skipped."""
    planted = tmp_path / "planted.bin"
    planted.write_bytes(content)

    assert _files_with_a_key([planted]) == [planted]


def test_the_scan_fails_on_a_file_it_cannot_read_instead_of_skipping_it(tmp_path: Path) -> None:
    """An unreadable path raises: not being able to inspect a file is not a clean result."""
    with pytest.raises(OSError):
        _files_with_a_key([tmp_path / "missing.txt"])


def test_the_tracked_file_list_is_not_empty() -> None:
    """The tracked-file scan has input: it includes this test and the backend Dockerfile."""
    tracked = _tracked_files()

    assert Path(__file__).resolve() in tracked
    assert REPO_ROOT / "Dockerfile" in tracked


def test_no_tracked_file_holds_a_model_key() -> None:
    """No file in the repository holds a key-shaped value."""
    assert _files_with_a_key(_tracked_files()) == []


def test_the_backend_build_context_holds_no_key_and_no_environment_file() -> None:
    """Nothing the backend build can copy holds a key, and no environment file is among it."""
    context = _backend_context(REPO_ROOT)

    assert context, "the backend context must not be empty"
    assert _files_with_a_key(context) == []
    assert [path for path in context if path.name.startswith(".env")] == []


def test_the_backend_context_catches_untracked_files_at_any_depth(tmp_path: Path) -> None:
    """A key in a bytecode cache inside a copied directory is found, and so is an environment
    file; files outside the re-included paths are not part of the context."""
    (tmp_path / ".dockerignore").write_text("*\n!app/\n", encoding="utf-8")
    cache = tmp_path / "app" / "pkg" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "mod.pyc").write_bytes(b"\x00" + _SENTINEL.encode())
    (tmp_path / "app" / ".env").write_text("A=1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text(_SENTINEL, encoding="utf-8")

    context = _backend_context(tmp_path)

    assert _files_with_a_key(context) == [cache / "mod.pyc"]
    assert [path.name for path in context if path.name.startswith(".env")] == [".env"]
    assert tmp_path / "notes.txt" not in context


@pytest.mark.parametrize(
    "ignore", ["*\n!app/**\n", "*\n!gone/\n", "app/\n", "*\n!app/\nnode_modules\n"]
)
def test_an_ignore_file_the_model_cannot_follow_fails_instead_of_passing(
    tmp_path: Path, ignore: str
) -> None:
    """A wildcard re-include, a missing path or another rule shape is rejected, not guessed."""
    (tmp_path / "app").mkdir()
    (tmp_path / ".dockerignore").write_text(ignore, encoding="utf-8")

    with pytest.raises(AssertionError):
        _backend_context(tmp_path)


def test_the_backend_context_covers_every_path_its_dockerfile_copies() -> None:
    """Every path the backend Dockerfile copies from the context is re-included by the ignore
    file, so the model of the context does not miss a copied directory."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    copied = _copy_sources(dockerfile)

    assert copied, "the Dockerfile copies nothing: the parser has failed"
    assert copied <= set(_backend_allowed(REPO_ROOT)), "copied but not re-included"


def test_the_copy_parser_reads_every_form_it_claims_to() -> None:
    """ADD, any case, continuation lines, flags and exec form are read; stage copies are not."""
    text = (
        "COPY pyproject.toml \\\n  uv.lock ./\n"
        "copy --chown=app:app app ./app\n"
        "ADD . /src\n"
        'COPY ["data", "/data"]\n'
        "COPY --from=builder /app/dist /out\n"
    )

    assert _copy_sources(text) == {"pyproject.toml", "uv.lock", "app", ".", "data"}


def test_the_web_build_context_excludes_environment_files_and_holds_no_key() -> None:
    """The web ignore file drops environment files, and what remains holds no key."""
    rules = _dockerignore_rules(REPO_ROOT / "web" / ".dockerignore")
    context = _web_context(REPO_ROOT / "web")

    assert {".env", ".env.*"} <= set(rules)
    assert context, "the web context must not be empty"
    assert _files_with_a_key(context) == []


def test_the_web_context_excludes_only_what_its_ignore_file_names(tmp_path: Path) -> None:
    """A key at the root of an ignored directory is outside the context; the same name deeper
    down, and any non-ignored directory such as a hidden one, is inside it."""
    (tmp_path / ".dockerignore").write_text("node_modules\n.env\n.env.*\n", encoding="utf-8")
    for relative in ("node_modules/k.txt", "src/node_modules/k.txt", ".venv/k.txt", ".env.local"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_SENTINEL, encoding="utf-8")

    context = _web_context(tmp_path)

    assert tmp_path / "node_modules" / "k.txt" not in context
    assert tmp_path / ".env.local" not in context
    assert tmp_path / "src" / "node_modules" / "k.txt" in context
    assert tmp_path / ".venv" / "k.txt" in context


@pytest.mark.parametrize("name", ["Dockerfile", "web/Dockerfile"])
def test_no_image_build_instruction_carries_a_model_key(name: str) -> None:
    """No ARG or ENV in either Dockerfile names the key, so it cannot be baked into a layer."""
    instructions = _instructions((REPO_ROOT / name).read_text(encoding="utf-8"))

    assert instructions, "the Dockerfile has no instructions: the parser has failed"
    assert [
        arguments
        for keyword, arguments in instructions
        if keyword in {"ARG", "ENV"} and re.search(r"ANTHROPIC|API_KEY", arguments, re.IGNORECASE)
    ] == []


def test_an_env_instruction_continued_over_two_lines_is_still_read() -> None:
    """A key named on a continuation line is attributed to its ENV instruction."""
    instructions = _instructions("ENV A=1 \\\n    ANTHROPIC_API_KEY=abc\n")

    assert len(instructions) == 1
    keyword, arguments = instructions[0]
    assert keyword == "ENV"
    assert "ANTHROPIC_API_KEY=abc" in arguments


def test_the_compose_files_pass_the_key_through_without_a_value() -> None:
    """Every compose file sets the key only to a pass-through of the host's variable."""
    files = sorted({path for pattern in _COMPOSE_PATTERNS for path in REPO_ROOT.glob(pattern)})

    assert files, "no compose file found: the pattern has failed"
    offending = [
        f"{path.name}: {value}"
        for path in files
        for value in _compose_values(path.read_text(encoding="utf-8"))
        if value not in _PASS_THROUGH
    ]
    assert offending == []


@pytest.mark.parametrize(
    ("line", "values"),
    [
        ("      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}", ["${ANTHROPIC_API_KEY}"]),
        ("      - ANTHROPIC_API_KEY=literal", ["literal"]),
        ('      ANTHROPIC_API_KEY: "literal"', ["literal"]),
        ("      - ANTHROPIC_API_KEY", []),
        ("      # ANTHROPIC_API_KEY: literal", []),
    ],
)
def test_the_compose_reader_handles_mapping_and_list_forms(line: str, values: list[str]) -> None:
    """Mapping and list forms are both read; a bare name and a comment set no value."""
    assert _compose_values(line) == values


def test_the_local_environment_file_is_not_tracked_and_is_ignored() -> None:
    """The local environment file, where a developer may hold a key, is untracked and ignored."""
    tracked = {path.name for path in _tracked_files()}
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"],  # noqa: S607 - git on PATH
        cwd=REPO_ROOT,
        check=False,
    )

    assert ".env" not in tracked
    assert ignored.returncode == 0
