"""
Lexical Policy Retrieval
========================

Overview
--------
Answers a policy question by searching the corpus for the section that covers it: accent-folded
BM25 with per-language stopwords and a curated synonym set, behind a ``Retriever`` port (ADR-16,
amending ADR-5's hybrid design: the corpus is small, about nine sections per language, and the
provider ratified in ADR-7 has no embeddings endpoint). Retrieval never feeds the policy engine;
it only grounds what a reply may cite.

Scope
-----
In: the ``Retriever`` port, ``LexicalRetriever``, tokenization, the relevance floor and the
synonym sets.
Out: reading the corpus files (``corpus_index``), building the reply text (the renderer),
deciding eligibility (the policy engine, which never reads this module).

Design Principles
-----------------
- Abstention is part of the decision, not a caller's afterthought: a query that shares no term
  with any chunk returns no hits at all, so the caller has one signal for "nothing found" instead
  of having to compare scores itself.
- Search runs only within the query's language; a corpus chunk from another language is never a
  candidate, so a language a customer did not use is never quoted at them (AC-E5-54).
- A synonym set folds a handful of same-idea words and phrases (plazo, tiempo, límite, vence;
  prazo, tempo, limite, vence; deadline, how long, time limit) to one canonical term before
  scoring, on both the query and the corpus side, so a paraphrase still finds the section a
  literal keyword match would (AC-E5-53).
- Accent folding and stopword removal happen once per chunk, at construction, not per query.
- Pure and hermetic: no model call, no network, no import-time file read (the retriever is built
  explicitly from chunks the caller already loaded).

Runtime Contract
----------------
``Retriever`` (protocol): ``search(query, lang) -> tuple[Hit, ...]``.
``LexicalRetriever.from_corpus(root=...) -> LexicalRetriever``, one instance covering all three
languages. ``Hit`` carries the chunk and its score. ``source_ref_for(section_id) -> SourceRef``
builds the citation a reply attaches, with a title in every language.

Limitations
-----------
The relevance floor is the mathematical minimum (some shared term at all): the exact value a
richer floor should use is a measured question the recall and abstention sets this ADR requires
are meant to answer, and that measurement is not part of this module.
"""

from __future__ import annotations

# Standard libraries
import math  # BM25's logarithmic term weighting
import re  # Tokenization
import unicodedata  # Accent folding
from collections import Counter  # Term frequencies
from dataclasses import dataclass  # Immutable search result
from pathlib import Path  # Corpus root location
from typing import Protocol  # The retrieval port

# Local modules
from app.retrieval.corpus_index import DEFAULT_CORPUS_ROOT, PolicyChunk, load_chunks
from contracts.service_v1.envelope import (  # The citation contract
    LANGUAGES,
    Lang,
    LocalizedTitle,
    SourceRef,
)

# BM25 parameters; standard defaults for a small, short-document corpus.
_K1 = 1.5
_B = 0.75

# Tokens this short in Spanish, Portuguese or English are almost always grammatical, never a
# content word this policy vocabulary would need.
_MIN_TOKEN_LENGTH = 3

# A shared idea folded to one canonical term, on both the query and the corpus side, so a
# paraphrase using any member finds what a literal match on another member would (AC-E5-53).
# Multi-word entries are matched as a phrase before the text is tokenized into single words.
_SYNONYM_GROUPS: dict[Lang, tuple[tuple[str, ...], ...]] = {
    "es": (("plazo", "tiempo", "limite", "vence"),),
    "pt": (("prazo", "tempo", "limite", "vence"),),
    "en": (("deadline", "how long", "time limit"),),
}

# Short function words and the common conjugations of auxiliary/modal verbs (poder, querer,
# tener, ser/estar; PT and EN equivalents), which carry no topic signal on their own and would
# otherwise leak spurious relevance from a single incidental shared word (a query and an
# unrelated section sharing only "pueden" must not look related). Removed after accent folding
# and lowercasing; a token of two characters or fewer is also always dropped (``tokenize``).
_STOPWORDS: dict[Lang, frozenset[str]] = {
    "es": frozenset(
        {
            "el",
            "la",
            "los",
            "las",
            "un",
            "una",
            "unos",
            "unas",
            "de",
            "del",
            "y",
            "o",
            "a",
            "en",
            "que",
            "es",
            "son",
            "era",
            "esta",
            "estan",
            "estar",
            "ser",
            "para",
            "con",
            "por",
            "su",
            "sus",
            "se",
            "lo",
            "le",
            "les",
            "mi",
            "mis",
            "me",
            "no",
            "si",
            "sino",
            "cual",
            "cuales",
            "como",
            "cuando",
            "donde",
            "quien",
            "quienes",
            "porque",
            "tengo",
            "tiene",
            "tienen",
            "tenemos",
            "tener",
            "puedo",
            "puede",
            "pueden",
            "podemos",
            "poder",
            "podria",
            "quiero",
            "quiere",
            "quieren",
            "queremos",
            "querer",
            "hace",
            "hacen",
            "hacer",
            "esto",
            "eso",
            "estas",
            "estos",
            "asi",
            "sobre",
            "entre",
            "hasta",
            "desde",
            "todo",
            "toda",
            "todos",
            "todas",
            "muy",
            "mas",
        }
    ),
    "pt": frozenset(
        {
            "o",
            "a",
            "os",
            "as",
            "um",
            "uma",
            "uns",
            "umas",
            "de",
            "do",
            "da",
            "e",
            "ou",
            "em",
            "que",
            "eh",
            "sao",
            "era",
            "esta",
            "estao",
            "estar",
            "ser",
            "para",
            "com",
            "por",
            "seu",
            "sua",
            "seus",
            "suas",
            "se",
            "lhe",
            "lhes",
            "meu",
            "minha",
            "me",
            "nao",
            "sim",
            "senao",
            "qual",
            "quais",
            "como",
            "quando",
            "onde",
            "quem",
            "porque",
            "tenho",
            "tem",
            "temos",
            "ter",
            "posso",
            "pode",
            "podem",
            "podemos",
            "poder",
            "poderia",
            "quero",
            "quer",
            "querem",
            "queremos",
            "querer",
            "faz",
            "fazem",
            "fazer",
            "isto",
            "isso",
            "estas",
            "estes",
            "assim",
            "sobre",
            "entre",
            "ate",
            "desde",
            "todo",
            "toda",
            "todos",
            "todas",
            "muito",
            "mais",
        }
    ),
    "en": frozenset(
        {
            "the",
            "a",
            "an",
            "of",
            "and",
            "or",
            "to",
            "in",
            "that",
            "this",
            "these",
            "those",
            "is",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "for",
            "with",
            "by",
            "my",
            "me",
            "no",
            "yes",
            "which",
            "who",
            "how",
            "when",
            "where",
            "why",
            "have",
            "has",
            "had",
            "can",
            "could",
            "will",
            "would",
            "shall",
            "should",
            "want",
            "wants",
            "do",
            "does",
            "did",
            "i",
            "you",
            "it",
            "on",
            "at",
            "as",
            "if",
            "so",
            "not",
            "about",
            "from",
            "all",
            "any",
            "some",
        }
    ),
}


def _fold_accents(text: str) -> str:
    """``text`` with every accent removed, lower case."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def _fold_synonyms(text: str, lang: Lang) -> str:
    """``text`` with every synonym-group member replaced by the group's first (canonical) term."""
    folded = text
    for canonical, *others in _SYNONYM_GROUPS[lang]:
        for term in others:
            folded = folded.replace(term, canonical)
    return folded


def tokenize(text: str, lang: Lang) -> tuple[str, ...]:
    """``text`` as lower-case, accent-folded, synonym-normalized, stopword-free word tokens.

    A token of two characters or fewer is dropped along with the explicit stopword list: almost
    every grammatical word in Spanish, Portuguese or English this short carries no topic signal,
    while a two-letter content word essentially never appears in this policy vocabulary.
    """
    folded = _fold_synonyms(_fold_accents(text), lang)
    words = re.findall(r"[a-z0-9]+", folded)
    return tuple(
        word for word in words if len(word) >= _MIN_TOKEN_LENGTH and word not in _STOPWORDS[lang]
    )


@dataclass(frozen=True, slots=True)
class Hit:
    """One search result: a chunk and how well it matched the query."""

    chunk: PolicyChunk
    score: float


class Retriever(Protocol):
    """Searches the policy corpus for the sections that best answer a query."""

    def search(self, query: str, lang: Lang) -> tuple[Hit, ...]:
        """Every chunk of ``lang``'s corpus above the relevance floor, best match first."""
        ...


@dataclass(frozen=True, slots=True)
class _Indexed:
    """One chunk with its token counts and length, precomputed for scoring."""

    chunk: PolicyChunk
    terms: Counter[str]
    length: int


class LexicalRetriever:
    """Accent-folded BM25 over the corpus, with per-language stopwords and synonyms (ADR-16)."""

    def __init__(self, chunks_by_lang: dict[Lang, tuple[PolicyChunk, ...]]) -> None:
        self._index: dict[Lang, tuple[_Indexed, ...]] = {
            lang: tuple(
                _Indexed(
                    chunk=chunk,
                    terms=Counter(tokenize(f"{chunk.title} {chunk.body}", lang)),
                    length=len(tokenize(f"{chunk.title} {chunk.body}", lang)),
                )
                for chunk in chunks
            )
            for lang, chunks in chunks_by_lang.items()
        }
        self._titles: dict[str, dict[Lang, str]] = {}
        self._corpus_version: dict[str, str] = {}
        for lang, chunks in chunks_by_lang.items():
            for chunk in chunks:
                self._titles.setdefault(chunk.section_id, {})[lang] = chunk.title
                self._corpus_version[chunk.section_id] = chunk.corpus_version

    @classmethod
    def from_corpus(cls, *, root: Path = DEFAULT_CORPUS_ROOT) -> LexicalRetriever:
        """Build a retriever covering all three languages from the committed corpus files."""
        return cls({lang: load_chunks(lang, root=root) for lang in LANGUAGES})

    def search(self, query: str, lang: Lang) -> tuple[Hit, ...]:
        """Every chunk of ``lang``'s corpus above the relevance floor, best match first.

        The floor is the mathematical minimum: a chunk with no scoring term in common with the
        query scores zero and is never returned, which is the abstention signal (ADR-16).
        """
        indexed = self._index[lang]
        query_terms = tokenize(query, lang)
        if not query_terms or not indexed:
            return ()

        document_count = len(indexed)
        average_length = sum(entry.length for entry in indexed) / document_count
        document_frequency = Counter(term for entry in indexed for term in set(entry.terms))

        hits = []
        for entry in indexed:
            score = 0.0
            for term in set(query_terms):
                frequency = entry.terms.get(term, 0)
                if frequency == 0:
                    continue
                containing = document_frequency[term]
                idf = math.log(1 + (document_count - containing + 0.5) / (containing + 0.5))
                length_norm = 1 - _B + _B * entry.length / average_length
                score += idf * (frequency * (_K1 + 1)) / (frequency + _K1 * length_norm)
            if score > 0:
                hits.append(Hit(chunk=entry.chunk, score=score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return tuple(hits)

    def source_ref_for(self, section_id: str) -> SourceRef:
        """The citation for ``section_id``, with a title in every language.

        Raises
        ------
        KeyError
            When no language's corpus has a section by this identifier.
        """
        by_language = self._titles[section_id]
        titles = tuple(LocalizedTitle(lang=lang, text=by_language[lang]) for lang in LANGUAGES)
        return SourceRef(
            section_id=section_id, titles=titles, corpus_version=self._corpus_version[section_id]
        )
