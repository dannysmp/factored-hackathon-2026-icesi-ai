"""
Policy Corpus Chunk Tests
=========================

Component: ``app.retrieval.corpus_index``. Hermetic: reads only files written to a temporary
directory in the test, and the committed corpus as a real-data sanity check.
"""

from __future__ import annotations

# Standard libraries
from pathlib import Path  # Temporary corpus folders

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from app.retrieval.corpus_index import DEFAULT_CORPUS_ROOT, CorpusIndexError, load_chunks


def _write_corpus(root: Path, lang: str, body: str) -> None:
    directory = root / lang
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "dispute-policy.md").write_text(body, encoding="utf-8")


_MINIMAL = (
    '---\nlang: en\npolicy_version: "2"\ngenerated: true\ngenerated_from: "x"\n---\n\n'
    "# Title\n\n"
    "## First Section {#first-section}\n\n"
    "Body of the first section.\n\n"
    "## Second Section {#second-section}\n\n"
    "Body of the second section, over\nmore than one line.\n"
)


_EXPECTED_SECTION_IDS = (
    "overview",
    "who-can-dispute",
    "filing-windows",
    "response-time",
    "evidence",
    "confirmation",
    "human-review",
    "fraud-claims",
    "decision-codes",
)


def test_the_committed_corpus_loads_in_every_language() -> None:
    """A real-data sanity check: the shipped corpus parses into its known 9 sections, in the
    same order and under the same identifiers, in all three languages."""
    for lang in ("es", "pt", "en"):
        chunks = load_chunks(lang, root=DEFAULT_CORPUS_ROOT)
        assert tuple(chunk.section_id for chunk in chunks) == _EXPECTED_SECTION_IDS
        assert all(chunk.corpus_version for chunk in chunks)
        assert all(chunk.title and chunk.body for chunk in chunks)


def test_a_minimal_document_parses_into_its_sections(tmp_path: Path) -> None:
    """Section identifier, title and body are read from the heading and the text under it."""
    _write_corpus(tmp_path, "en", _MINIMAL)

    chunks = load_chunks("en", root=tmp_path)

    assert [c.section_id for c in chunks] == ["first-section", "second-section"]
    assert chunks[0].title == "First Section"
    assert chunks[0].body == "Body of the first section."
    assert chunks[1].body == "Body of the second section, over\nmore than one line."
    assert all(c.corpus_version == "2" for c in chunks)


def test_a_missing_file_is_a_corpus_index_error(tmp_path: Path) -> None:
    """The service must not start retrieval on a corpus it cannot read."""
    with pytest.raises(CorpusIndexError, match="cannot be read"):
        load_chunks("en", root=tmp_path)


def test_a_file_with_no_policy_version_is_refused(tmp_path: Path) -> None:
    """A document without the expected front matter is not silently half-read."""
    _write_corpus(tmp_path, "en", "# Title\n\n## Section {#s}\n\nBody.\n")

    with pytest.raises(CorpusIndexError, match="no policy_version"):
        load_chunks("en", root=tmp_path)


def test_a_file_with_no_sections_is_refused(tmp_path: Path) -> None:
    """A document with a version but no headings has nothing to index."""
    _write_corpus(tmp_path, "en", '---\npolicy_version: "2"\n---\n\n# Title\n\nJust a paragraph.\n')

    with pytest.raises(CorpusIndexError, match="no sections"):
        load_chunks("en", root=tmp_path)
