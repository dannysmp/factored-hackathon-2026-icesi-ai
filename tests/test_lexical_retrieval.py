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


@pytest.mark.parametrize(
    ("lang", "query"),
    [
        # The originally reported forms (present perfect / conditional).
        ("es", "mi hermano ha sido muy amable conmigo hoy"),
        ("pt", "acho que seria bom sair mais cedo do trabalho"),
        # A different tense of the same auxiliary, found on the very sentence in the corpus
        # that the first fix's own evidence quoted ("esté fuera de plazo" / "esteja fora do
        # prazo") — the fix must cover the whole paradigm, not just the two reported forms.
        ("es", "si yo fuera tú, no me preocuparía por esto"),
        ("pt", "se eu fora rico, viajaria pelo mundo todo"),
        # Further persons and tenses of ser/estar/haber (ES) and ser/estar/ter/haver (PT), none
        # individually reported, to probe whether the fix is a full paradigm or another
        # hand-picked batch.
        ("es", "nosotros éramos muy felices en esa época"),
        ("es", "ellos estuvieron aquí ayer por la tarde"),
        ("es", "espero que hayamos hecho lo correcto"),
        ("es", "ella habría preferido quedarse en casa"),
        ("pt", "nós estivemos lá ontem à tarde"),
        ("pt", "espero que tenhamos feito a coisa certa"),
        ("pt", "eles estarão lá amanhã de manhã"),
    ],
)
def test_a_common_auxiliary_verb_conjugation_does_not_leak_relevance(
    retriever: LexicalRetriever, lang: Lang, query: str
) -> None:
    """AC-E5-51: an unrelated sentence sharing only a "to be"/"to have" auxiliary — in any
    person or tense, not only the specific forms first reported — still abstains."""
    assert retriever.search(query, lang) == ()


def test_every_auxiliary_verb_form_is_a_stopword(retriever: LexicalRetriever) -> None:
    """The stopword lists cover the auxiliary paradigm as a whole, checked against the closed
    set of forms rather than against any one reported sentence: tokenizing every conjugated
    form of ser/estar/haber (ES) and ser/estar/ter/haver (PT) yields nothing, in every person
    and tense, so none of them can ever leak in as an accidental content token."""
    es_forms = [
        "soy",
        "eres",
        "es",
        "somos",
        "sois",
        "son",
        "era",
        "eras",
        "eramos",
        "erais",
        "eran",
        "fui",
        "fuiste",
        "fue",
        "fuimos",
        "fuisteis",
        "fueron",
        "sere",
        "seras",
        "sera",
        "seremos",
        "sereis",
        "seran",
        "seria",
        "serias",
        "seriamos",
        "seriais",
        "serian",
        "sea",
        "seas",
        "seamos",
        "seais",
        "sean",
        "fuera",
        "fueras",
        "fueramos",
        "fuerais",
        "fueran",
        "fuese",
        "fueses",
        "fuesemos",
        "fueseis",
        "fuesen",
        "siendo",
        "sido",
        "ser",
        "estoy",
        "estas",
        "esta",
        "estamos",
        "estais",
        "estan",
        "estaba",
        "estabas",
        "estabamos",
        "estabais",
        "estaban",
        "estuve",
        "estuviste",
        "estuvo",
        "estuvimos",
        "estuvisteis",
        "estuvieron",
        "estare",
        "estaras",
        "estara",
        "estaremos",
        "estareis",
        "estaran",
        "estaria",
        "estarias",
        "estariamos",
        "estariais",
        "estarian",
        "este",
        "estes",
        "estemos",
        "esteis",
        "esten",
        "estuviera",
        "estuvieras",
        "estuvieramos",
        "estuvierais",
        "estuvieran",
        "estuviese",
        "estuvieses",
        "estuviesemos",
        "estuvieseis",
        "estuviesen",
        "estando",
        "estado",
        "estar",
        "he",
        "has",
        "ha",
        "hemos",
        "habeis",
        "han",
        "hay",
        "habia",
        "habias",
        "habiamos",
        "habiais",
        "habian",
        "hube",
        "hubiste",
        "hubo",
        "hubimos",
        "hubisteis",
        "hubieron",
        "habre",
        "habras",
        "habra",
        "habremos",
        "habreis",
        "habran",
        "habria",
        "habrias",
        "habriamos",
        "habriais",
        "habrian",
        "haya",
        "hayas",
        "hayamos",
        "hayais",
        "hayan",
        "hubiera",
        "hubieras",
        "hubieramos",
        "hubierais",
        "hubieran",
        "hubiese",
        "hubieses",
        "hubiesemos",
        "hubieseis",
        "hubiesen",
        "habiendo",
        "habido",
        "haber",
    ]
    pt_forms = [
        "sou",
        "es",
        "somos",
        "sois",
        "sao",
        "era",
        "eras",
        "eramos",
        "ereis",
        "eram",
        "fui",
        "foste",
        "foi",
        "fomos",
        "fostes",
        "foram",
        "serei",
        "seras",
        "sera",
        "seremos",
        "sereis",
        "serao",
        "seria",
        "serias",
        "seriamos",
        "serieis",
        "seriam",
        "seja",
        "sejas",
        "sejamos",
        "sejais",
        "sejam",
        "fosse",
        "fosses",
        "fossemos",
        "fosseis",
        "fossem",
        "fora",
        "foras",
        "foramos",
        "foreis",
        "sendo",
        "sido",
        "ser",
        "estou",
        "estas",
        "esta",
        "estamos",
        "estais",
        "estao",
        "estava",
        "estavas",
        "estavamos",
        "estaveis",
        "estavam",
        "estive",
        "estiveste",
        "esteve",
        "estivemos",
        "estivestes",
        "estiveram",
        "estarei",
        "estaras",
        "estara",
        "estaremos",
        "estareis",
        "estarao",
        "estaria",
        "estarias",
        "estariamos",
        "estarieis",
        "estariam",
        "esteja",
        "estejas",
        "estejamos",
        "estejais",
        "estejam",
        "estivesse",
        "estivesses",
        "estivessemos",
        "estivesseis",
        "estivessem",
        "estando",
        "estado",
        "estar",
        "tenho",
        "tens",
        "tem",
        "temos",
        "tendes",
        "tinha",
        "tinhas",
        "tinhamos",
        "tinheis",
        "tinham",
        "tive",
        "tiveste",
        "teve",
        "tivemos",
        "tivestes",
        "tiveram",
        "terei",
        "teras",
        "tera",
        "teremos",
        "tereis",
        "terao",
        "teria",
        "terias",
        "teriamos",
        "terieis",
        "teriam",
        "tenha",
        "tenhas",
        "tenhamos",
        "tenhais",
        "tenham",
        "tivesse",
        "tivesses",
        "tivessemos",
        "tivesseis",
        "tivessem",
        "tendo",
        "tido",
        "ter",
        "hei",
        "havemos",
        "haveis",
        "hao",
        "havia",
        "havias",
        "haviamos",
        "havieis",
        "haviam",
        "houve",
        "houveste",
        "houvemos",
        "houvestes",
        "houveram",
        "havera",
        "haveras",
        "haveremos",
        "havereis",
        "haverao",
        "haveria",
        "haverias",
        "haveriamos",
        "haverieis",
        "haveriam",
        "haja",
        "hajas",
        "hajamos",
        "hajais",
        "hajam",
        "houvesse",
        "houvesses",
        "houvessemos",
        "houvesseis",
        "houvessem",
        "havendo",
        "havido",
        "haver",
    ]

    assert tokenize(" ".join(es_forms), "es") == ()
    assert tokenize(" ".join(pt_forms), "pt") == ()


def test_a_synonym_folds_across_its_verb_conjugations_without_corrupting_other_words(
    retriever: LexicalRetriever,
) -> None:
    """AC-E5-53: a conjugated form of the synonym still finds its section; a word that merely
    contains a synonym as a substring is never corrupted by the fold."""
    hits = retriever.search("¿cuándo vencen mis disputas?", "es")

    assert "filing-windows" in {hit.chunk.section_id for hit in hits[:3]}
    assert "convencer" in tokenize("quiero convencer al asesor de mi caso", "es")


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
