"""
Policy Corpus Generation
========================

Overview
--------
Writes the multilingual policy corpus (``policy/corpus/``) from the dispute policy, or checks
that the committed files are exactly what the policy would generate. The check is what stops the
text customers read from drifting away from the rules the engine enforces.

Scope
-----
In: reading the policy, writing or comparing files, the command line
``python -m pipelines.policy_corpus [--check]``.
Out: the text itself (``app.domain.policy.corpus``).

Design Principles
-----------------
- The generated files are committed, and a test plus ``--check`` fail on any difference, so a
  policy change that forgets to regenerate the corpus cannot be merged.
- Files are replaced atomically and only when their content changes.
- A file in the corpus folder that the policy does not generate is reported as drift, so a
  stale language or a hand-added file is noticed.

Runtime Contract
----------------
``write_corpus(policy, directory) -> list[str]`` returns the files that changed.
``check_corpus(policy, directory) -> list[str]`` returns the files that differ, are missing or
are not generated.
``main(argv) -> int``: 0 on success, 1 when ``--check`` finds drift or the policy is invalid.

Limitations
-----------
Only the ``es``, ``pt`` and ``en`` documents are generated; other formats are not.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import logging  # Progress events
import os  # Atomic file replacement
from collections.abc import Sequence  # Argument type of main
from pathlib import Path  # Locations

# Local modules
from app.domain.policy import DEFAULT_POLICY_PATH, Policy, PolicyError, load_policy
from app.domain.policy.corpus import render_corpus  # The text to write

logger = logging.getLogger(__name__)

DEFAULT_DIRECTORY = DEFAULT_POLICY_PATH.parent / "corpus"


def _source_name(policy_path: Path) -> str:
    """The policy path as quoted in the documents: relative to the repository when it is inside."""
    try:
        return policy_path.relative_to(DEFAULT_POLICY_PATH.parents[1]).as_posix()
    except ValueError:
        return policy_path.name


def write_corpus(policy: Policy, directory: Path, *, source: str) -> list[str]:
    """Write every document under ``directory``; return the relative paths that changed.

    Files that already hold the right text are left untouched, so a rerun changes nothing.
    """
    changed = []
    for relative, text in render_corpus(policy, source=source).items():
        target = directory / relative
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".md.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
        changed.append(relative)
    return changed


def check_corpus(policy: Policy, directory: Path, *, source: str) -> list[str]:
    """Relative paths of documents that are missing, differ from the policy or are not generated."""
    expected = render_corpus(policy, source=source)
    drift = []
    for relative, text in expected.items():
        target = directory / relative
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            drift.append(relative)
    if directory.is_dir():
        present = {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file()
        }
        drift.extend(sorted(present - set(expected)))
    return drift


def main(argv: Sequence[str] | None = None) -> int:
    """Generate the corpus, or verify it with ``--check``; return the exit code."""
    parser = argparse.ArgumentParser(description="Generate or check the policy corpus.")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--check", action="store_true", help="fail when the files have drifted")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        policy = load_policy(args.policy)
    except PolicyError as error:
        logger.error("policy_corpus_failed reason=%s", error)
        return 1
    source = _source_name(args.policy)

    if args.check:
        drift = check_corpus(policy, args.out, source=source)
        for relative in drift:
            logger.error("policy_corpus_drift file=%s", relative)
        if drift:
            return 1
        logger.info("policy_corpus_current")
        return 0

    for relative in write_corpus(policy, args.out, source=source):
        logger.info("policy_corpus_written file=%s", relative)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
