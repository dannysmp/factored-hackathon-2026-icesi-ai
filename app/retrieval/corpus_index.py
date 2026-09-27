"""
Policy Corpus Chunks
====================

Overview
--------
Parses the generated corpus documents (``policy/corpus/<lang>/dispute-policy.md``) into the
sections retrieval searches over: one chunk per section, per language, with the section's
readable title and body text. The section identifier and order are the same in every language
(the corpus generator's own invariant, checked by its own tests), so a chunk found in one
language's search can cite the same section's title in either of the other two.

Scope
-----
In: reading the committed corpus files and splitting them into typed chunks.
Out: searching them (``lexical``), generating them (``app.domain.policy.corpus``).

Design Principles
-----------------
- Reads the corpus as data, the same files a human or the drift check reads; it does not call
  back into the policy engine or the corpus generator, so retrieval stays a read-only consumer
  of a versioned artifact, never a second implementation of what the corpus says.
- A chunk's body is the section's prose exactly as committed; nothing is summarized or reworded,
  so a citation always resolves to text a person can read in full.

Runtime Contract
----------------
``PolicyChunk`` (``section_id``, ``title``, ``body``, ``corpus_version``).
``load_chunks(lang, *, root) -> tuple[PolicyChunk, ...]`` raises ``CorpusIndexError``.

Limitations
-----------
Assumes the corpus file's own format (a YAML front matter, then ``## Title {#id}`` headings);
a hand-edited file that breaks this shape is refused rather than partially read.
"""

from __future__ import annotations

# Standard libraries
import re  # Splits the document into sections
from dataclasses import dataclass  # Immutable chunk
from pathlib import Path  # Corpus file location

# Local modules
from contracts.service_v1.envelope import Lang  # Closed set of languages

DEFAULT_CORPUS_ROOT = Path(__file__).resolve().parents[2] / "policy" / "corpus"
_DOCUMENT_NAME = "dispute-policy.md"

_FRONT_MATTER = re.compile(r"^---\n(?P<meta>.*?)\n---\n", re.DOTALL)
_VERSION_LINE = re.compile(r'^policy_version:\s*"(?P<version>[^"]+)"\s*$', re.MULTILINE)
_SECTION = re.compile(
    r"^## (?P<title>.+?) \{#(?P<id>[a-z-]+)\}\n\n(?P<body>.*?)(?=\n## |\Z)",
    re.MULTILINE | re.DOTALL,
)


class CorpusIndexError(Exception):
    """The corpus file for a language could not be read or does not have the expected shape."""


@dataclass(frozen=True, slots=True)
class PolicyChunk:
    """One section of the policy corpus, ready to be searched and cited."""

    section_id: str
    title: str
    body: str
    corpus_version: str


def load_chunks(lang: Lang, *, root: Path = DEFAULT_CORPUS_ROOT) -> tuple[PolicyChunk, ...]:
    """Every section of ``lang``'s corpus document, in reading order.

    Raises
    ------
    CorpusIndexError
        When the file is missing, unreadable, or does not have a policy version or any section.
    """
    path = root / lang / _DOCUMENT_NAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CorpusIndexError(f"corpus file {path} cannot be read") from error

    front_matter = _FRONT_MATTER.match(text)
    version_match = _VERSION_LINE.search(front_matter.group("meta")) if front_matter else None
    if version_match is None:
        raise CorpusIndexError(f"corpus file {path} has no policy_version in its front matter")
    version = version_match.group("version")

    chunks = tuple(
        PolicyChunk(
            section_id=match.group("id"),
            title=match.group("title").strip(),
            body=match.group("body").strip(),
            corpus_version=version,
        )
        for match in _SECTION.finditer(text)
    )
    if not chunks:
        raise CorpusIndexError(f"corpus file {path} has no sections")
    return chunks
