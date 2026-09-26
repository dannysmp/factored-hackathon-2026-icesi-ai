"""
Policy Corpus Tests
===================

Component: ``app.domain.policy.corpus`` and ``pipelines.policy_corpus``. Hermetic: the corpus is
rendered from the shipped policy and from variants of it; the committed files are compared with
what the policy generates, which is the drift check the repository relies on.
"""

from __future__ import annotations

# Standard libraries
import re  # Read the section identifiers back from the text
from decimal import Decimal  # Money in the tests
from pathlib import Path  # Temporary corpus folders

# Third-party libraries
import pytest  # Test runner and parametrisation

# Local modules
from app.domain.policy import (
    DEFAULT_POLICY_PATH,
    DisputeCategory,
    Policy,
    ReasonCode,
    TransactionStatus,
    load_policy,
)
from app.domain.policy.corpus import (
    LANGUAGES,
    MESSAGES,
    SECTION_IDS,
    _amount,
    _join,
    _rate,
    render_corpus,
)
from pipelines.policy_corpus import (
    DEFAULT_DIRECTORY,
    _source_name,
    check_corpus,
    main,
    write_corpus,
)

SOURCE = "policy/dispute_policy_v1.yaml"


@pytest.fixture(scope="module")
def policy() -> Policy:
    """The policy that ships with the repository."""
    return load_policy()


def _routing(policy: Policy, **updates: object) -> Policy:
    """A copy of ``policy`` with some routing parameters changed."""
    return policy.model_copy(update={"routing": policy.routing.model_copy(update=updates)})


def _window(policy: Policy, category: DisputeCategory, days: int) -> Policy:
    """A copy of ``policy`` with one filing window changed."""
    rule = policy.categories[category].model_copy(update={"filing_window_days": days})
    return policy.model_copy(update={"categories": {**policy.categories, category: rule}})


# -----------------------------------------------------------------------------
# Drift
# -----------------------------------------------------------------------------


def test_the_committed_corpus_is_exactly_what_the_policy_generates(policy: Policy) -> None:
    """The drift check: a policy change without regenerating the corpus fails here."""
    assert check_corpus(policy, DEFAULT_DIRECTORY, source=SOURCE) == []
    for relative, text in render_corpus(policy, source=SOURCE).items():
        assert (DEFAULT_DIRECTORY / relative).read_text(encoding="utf-8") == text


def test_changing_a_parameter_changes_the_rendered_corpus(policy: Policy) -> None:
    """A policy that differs from the committed one is reported as drift, in every language."""
    changed = _window(policy, DisputeCategory.WRONG_AMOUNT, 91)

    assert sorted(check_corpus(changed, DEFAULT_DIRECTORY, source=SOURCE)) == sorted(
        f"{language}/dispute-policy.md" for language in LANGUAGES
    )


# -----------------------------------------------------------------------------
# Structure
# -----------------------------------------------------------------------------


def test_one_document_per_language_in_a_fixed_order(policy: Policy) -> None:
    """The keys are the relative paths, Spanish first."""
    assert list(render_corpus(policy)) == [
        "es/dispute-policy.md",
        "pt/dispute-policy.md",
        "en/dispute-policy.md",
    ]


def test_rendering_is_deterministic(policy: Policy) -> None:
    """The same policy renders the same text."""
    assert render_corpus(policy) == render_corpus(policy)


def test_every_language_has_the_same_sections_in_the_same_order(policy: Policy) -> None:
    """Section identifiers are stable across languages, so a citation works in any of them."""
    for text in render_corpus(policy).values():
        assert re.findall(r"\{#([a-z-]+)\}", text) == list(SECTION_IDS)


def test_each_document_states_its_language_and_policy_version(policy: Policy) -> None:
    """The front matter lets retrieval filter by language and version."""
    for language, text in zip(LANGUAGES, render_corpus(policy).values(), strict=True):
        assert text.startswith(f'---\nlang: {language}\npolicy_version: "{policy.version}"\n')
        assert f"(`{SOURCE}`" not in text
        assert SOURCE in text


def test_every_language_covers_every_category_status_reason_and_section() -> None:
    """A new enum member without a translation fails here instead of rendering a KeyError."""
    for language in LANGUAGES:
        messages = MESSAGES[language]
        assert set(messages.categories) == set(DisputeCategory)
        assert set(messages.statuses) == set(TransactionStatus)
        assert set(messages.reason_codes) == set(ReasonCode)
        assert set(messages.section_titles) == set(SECTION_IDS)


def test_every_reason_code_appears_in_every_language(policy: Policy) -> None:
    """The reason-code table is complete."""
    for text in render_corpus(policy).values():
        for code in ReasonCode:
            assert f"| `{code.value}` |" in text


# -----------------------------------------------------------------------------
# Numbers come from the policy
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "amount", "percent", "score", "days"),
    [
        ("en", "5,000 USD", "60%", "0.80", "120 days"),
        ("es", "5.000 USD", "60 %", "0,80", "120 días"),
        ("pt", "5.000 USD", "60 %", "0,80", "120 dias"),
    ],
)
def test_every_parameter_of_the_policy_appears_in_the_language_s_own_format(
    policy: Policy, *, language: str, amount: str, percent: str, score: str, days: str
) -> None:
    """Amount, confidence floor, risk threshold and windows are all in the text."""
    text = render_corpus(policy)[f"{language}/dispute-policy.md"]

    for expected in (amount, percent, score, days):
        assert expected in text
    for category in DisputeCategory:
        window = policy.categories[category].filing_window_days
        assert re.search(rf"\b{window} (days|días|dias)\b", text)


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_changed_parameter_reaches_the_text_and_the_old_value_leaves(
    policy: Policy, language: str
) -> None:
    """Change a window, the amount, the floor and the threshold: only the new values remain."""
    changed = _routing(
        _window(policy, DisputeCategory.FRAUD_CLAIM, 200),
        escalate_amount_usd=Decimal("7500.50"),
        nlu_confidence_floor=0.75,
        risk_score_threshold=0.95,
    )

    text = render_corpus(changed)[f"{language}/dispute-policy.md"]

    assert "200" in text and "180" not in text
    assert ("7,500.50" if language == "en" else "7.500,50") in text
    assert "5.000" not in text and "5,000" not in text
    percent = "{}%" if language == "en" else "{} %"
    assert percent.format(75) in text
    assert percent.format(60) not in text
    assert ("0.95" if language == "en" else "0,95") in text


@pytest.mark.parametrize(
    ("value", "english", "spanish"),
    [
        (Decimal("5000.00"), "5,000", "5.000"),
        (Decimal("1234.5"), "1,234.50", "1.234,50"),
        (Decimal("0.01"), "0.01", "0,01"),
        (Decimal("1000000"), "1,000,000", "1.000.000"),
    ],
)
def test_amounts_use_each_language_s_separators(value: Decimal, english: str, spanish: str) -> None:
    """A whole amount drops its fraction; a fractional one keeps two decimals."""
    assert _amount(value, MESSAGES["en"]) == english
    assert _amount(value, MESSAGES["es"]) == spanish
    assert _amount(value, MESSAGES["pt"]) == spanish
    assert _rate(0.8, MESSAGES["es"]) == "0,80"
    assert _rate(0.8, MESSAGES["en"]) == "0.80"


@pytest.mark.parametrize(
    ("items", "expected"),
    [([], ""), (["a"], "a"), (["a", "b"], "a and b"), (["a", "b", "c"], "a, b and c")],
)
def test_lists_are_joined_with_a_final_conjunction(items: list[str], expected: str) -> None:
    """Zero, one, two and three items read naturally."""
    assert _join(items, "and") == expected


# -----------------------------------------------------------------------------
# Rules a version switches off are left out
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_rules_the_policy_switches_off_are_not_described(policy: Policy, language: str) -> None:
    """Repeat-complainer and unknown-amount routing are listed only while they are on."""
    messages = MESSAGES[language]
    on = render_corpus(policy)[f"{language}/dispute-policy.md"]
    off = render_corpus(
        _routing(policy, escalate_repeat_complainer=False, escalate_unknown_amount=False)
    )[f"{language}/dispute-policy.md"]

    assert messages.human_repeat in on and messages.human_unknown_amount in on
    assert messages.human_repeat not in off and messages.human_unknown_amount not in off


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_confirmation_section_follows_the_categories_that_require_it(
    policy: Policy, language: str
) -> None:
    """All, some or none of the categories require confirmation."""
    messages = MESSAGES[language]

    def rules(required: set[DisputeCategory]) -> Policy:
        categories = {
            c: policy.categories[c].model_copy(update={"requires_confirmation": c in required})
            for c in DisputeCategory
        }
        return policy.model_copy(update={"categories": categories})

    everything = render_corpus(rules(set(DisputeCategory)))[f"{language}/dispute-policy.md"]
    nothing = render_corpus(rules(set()))[f"{language}/dispute-policy.md"]
    some = render_corpus(rules({DisputeCategory.WRONG_AMOUNT, DisputeCategory.FRAUD_CLAIM}))[
        f"{language}/dispute-policy.md"
    ]

    assert messages.confirmation_all in everything
    assert messages.confirmation_none in nothing
    listed = f"{messages.categories[DisputeCategory.WRONG_AMOUNT]} {messages.and_word} "
    assert listed + messages.categories[DisputeCategory.FRAUD_CLAIM] in some
    assert messages.confirmation_all not in some


def test_a_type_without_a_translation_appears_as_the_source_spells_it(policy: Policy) -> None:
    """Unknown labels are shown as they are, never dropped or raised on."""
    custom = policy.model_copy(
        update={"disputable_transaction_types": frozenset({"Purchase", "Refund"})}
    )

    text = render_corpus(custom)["en/dispute-policy.md"]

    assert "(purchase and Refund)" in text or "(Refund and purchase)" in text


def test_the_fraud_section_states_the_rule_the_engine_enforces(policy: Policy) -> None:
    """Every language says a fraud claim is never refused automatically."""
    for language in LANGUAGES:
        text = render_corpus(policy)[f"{language}/dispute-policy.md"]
        assert MESSAGES[language].fraud in text


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def _arguments(tmp_path: Path, *extra: str) -> list[str]:
    return ["--policy", str(DEFAULT_POLICY_PATH), "--out", str(tmp_path / "corpus"), *extra]


def test_the_command_writes_the_corpus_and_a_rerun_changes_nothing(
    tmp_path: Path, policy: Policy
) -> None:
    """Generation is idempotent: the second run reports no changed file."""
    assert main(_arguments(tmp_path)) == 0

    assert write_corpus(policy, tmp_path / "corpus", source=_source_name(DEFAULT_POLICY_PATH)) == []
    assert main(_arguments(tmp_path, "--check")) == 0


def test_check_fails_on_an_edited_a_missing_and_an_extra_file(tmp_path: Path) -> None:
    """Drift is an edit by hand, a deleted document or a document the policy does not generate."""
    assert main(_arguments(tmp_path)) == 0
    corpus = tmp_path / "corpus"

    (corpus / "en" / "dispute-policy.md").write_text("edited", encoding="utf-8")
    assert main(_arguments(tmp_path, "--check")) == 1
    assert main(_arguments(tmp_path)) == 0

    (corpus / "pt" / "dispute-policy.md").unlink()
    assert main(_arguments(tmp_path, "--check")) == 1
    assert main(_arguments(tmp_path)) == 0

    (corpus / "fr").mkdir()
    (corpus / "fr" / "dispute-policy.md").write_text("stale", encoding="utf-8")
    assert main(_arguments(tmp_path, "--check")) == 1


def test_check_on_a_folder_that_does_not_exist_reports_every_file(
    tmp_path: Path, policy: Policy
) -> None:
    """Nothing generated yet: all three documents are drift."""
    assert len(check_corpus(policy, tmp_path / "nothing", source=SOURCE)) == len(LANGUAGES)


def test_an_invalid_policy_stops_the_command(tmp_path: Path) -> None:
    """A policy that cannot be loaded produces no corpus and exit code 1."""
    broken = tmp_path / "policy.yaml"
    broken.write_text("version: [unclosed", encoding="utf-8")

    code = main(["--policy", str(broken), "--out", str(tmp_path / "corpus")])

    assert code == 1
    assert not (tmp_path / "corpus").exists()


def test_the_source_is_quoted_relative_to_the_repository_when_inside_it(tmp_path: Path) -> None:
    """A policy elsewhere is quoted by its file name only."""
    assert _source_name(DEFAULT_POLICY_PATH) == SOURCE
    assert _source_name(tmp_path / "custom.yaml") == "custom.yaml"
