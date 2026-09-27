"""
Lexical Retrieval Tests
=======================

Component: ``app.retrieval.lexical``. Hermetic: searches the committed corpus files on disk, no
model call and no network.
"""

from __future__ import annotations

# Third-party libraries
import pytest  # Test runner and fixtures

# Local modules
from app.retrieval.lexical import LexicalRetriever, tokenize
from contracts.service_v1.envelope import Lang


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    """A retriever built from the corpus committed with the repository."""
    return LexicalRetriever.from_corpus()


# -----------------------------------------------------------------------------
# Tokenization
# -----------------------------------------------------------------------------


def test_tokenize_folds_accents_and_case() -> None:
    """An accented, capitalized word matches its plain lower-case form."""
    assert tokenize("Política", "es") == tokenize("politica", "es")


def test_tokenize_drops_stopwords_and_short_words() -> None:
    """Grammatical filler carries no signal and is removed."""
    tokens = tokenize("¿Puedo saber cuál es el plazo de mi disputa?", "es")

    assert "puedo" not in tokens
    assert "el" not in tokens
    assert "es" not in tokens
    assert "plazo" in tokens
    assert "disputa" in tokens


def test_tokenize_folds_a_synonym_to_its_canonical_term() -> None:
    """AC-E5-53: a paraphrase using a synonym reads as the term the corpus itself uses."""
    assert "plazo" in tokenize("¿cuánto tiempo tengo?", "es")
    assert "prazo" in tokenize("qual o tempo que eu tenho?", "pt")
    assert "deadline" in tokenize("what is the time limit?", "en")
    assert "deadline" in tokenize("how long do I have?", "en")


def test_tokenize_returns_nothing_for_text_with_no_content_word() -> None:
    """A message built entirely of stopwords has nothing left to search with."""
    assert tokenize("¿es el de la?", "es") == ()


# -----------------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lang", "query", "expected_section"),
    [
        ("es", "¿cuántos días tengo para disputar un cargo duplicado?", "filing-windows"),
        ("pt", "qual o prazo para contestar uma cobrança?", "filing-windows"),
        ("en", "what is the deadline to file a dispute?", "filing-windows"),
        ("es", "¿cuándo un asesor revisa mi caso?", "human-review"),
        ("pt", "quando um atendente analisa meu pedido?", "human-review"),
        ("es", "no reconozco esto, es fraude", "fraud-claims"),
        ("es", "¿qué necesito tener listo para reclamar?", "evidence"),
        ("en", "when will I get a first response?", "response-time"),
    ],
)
def test_a_covered_question_finds_its_section_within_the_top_three(
    retriever: LexicalRetriever, lang: Lang, query: str, expected_section: str
) -> None:
    """Recall at three (AC-E5-52): the right section is among the best three matches."""
    hits = retriever.search(query, lang)

    assert expected_section in {hit.chunk.section_id for hit in hits[:3]}


def test_a_question_that_shares_no_term_with_the_corpus_abstains(
    retriever: LexicalRetriever,
) -> None:
    """A query sharing nothing with the corpus returns no hits, the abstention signal."""
    hits = retriever.search("¿me pueden dar mi saldo bancario?", "es")

    assert hits == ()


def test_an_unrelated_banking_question_abstains_in_every_language(
    retriever: LexicalRetriever,
) -> None:
    """AC-E5-51: a question outside the corpus abstains, not just in one language."""
    assert retriever.search("quiero cambiar mi contraseña de la aplicación", "es") == ()
    assert retriever.search("quero mudar minha senha do aplicativo", "pt") == ()
    assert retriever.search("I need help resetting my password", "en") == ()


def test_search_never_returns_a_hit_from_another_language(retriever: LexicalRetriever) -> None:
    """AC-E5-54: search runs only within the query's own language."""
    for lang in ("es", "pt", "en"):
        hits = retriever.search("dispute fraud claim plazo prazo deadline", lang)
        for hit in hits:
            assert retriever.source_ref_for(hit.chunk.section_id).title_for(lang) == hit.chunk.title


def test_results_are_ordered_best_match_first(retriever: LexicalRetriever) -> None:
    """A more specific, higher-overlap query section ranks above a weaker one."""
    hits = retriever.search("plazo días calendario disputa duplicado", "es")

    assert list(hits) == sorted(hits, key=lambda hit: hit.score, reverse=True)


def test_search_is_deterministic(retriever: LexicalRetriever) -> None:
    """The same query and language always give the same ranked results."""
    assert retriever.search("plazo para disputar", "es") == retriever.search(
        "plazo para disputar", "es"
    )


# -----------------------------------------------------------------------------
# Citations
# -----------------------------------------------------------------------------


def test_source_ref_for_carries_a_title_in_every_language(retriever: LexicalRetriever) -> None:
    """A citation resolves to a readable title regardless of the reply language."""
    source = retriever.source_ref_for("filing-windows")

    assert source.section_id == "filing-windows"
    assert source.title_for("es") and source.title_for("pt") and source.title_for("en")
    assert source.corpus_version


def test_source_ref_for_an_unknown_section_is_a_key_error(retriever: LexicalRetriever) -> None:
    """A caller cannot cite a section that does not exist."""
    with pytest.raises(KeyError):
        retriever.source_ref_for("not-a-real-section")


def test_every_hit_s_section_can_be_cited(retriever: LexicalRetriever) -> None:
    """Every section the retriever can return also has a usable citation."""
    for lang in ("es", "pt", "en"):
        for hit in retriever.search("plazo prazo deadline disputa fraude", lang):
            source = retriever.source_ref_for(hit.chunk.section_id)
            assert source.title_for(lang) == hit.chunk.title


def test_search_on_an_empty_index_abstains_rather_than_dividing_by_zero() -> None:
    """A language with no chunks at all still returns no hits, not an error."""
    empty = LexicalRetriever({"es": (), "pt": (), "en": ()})

    assert empty.search("plazo", "es") == ()
