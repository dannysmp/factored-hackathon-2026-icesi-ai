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
    KNOWN_PRODUCT_TYPES,
    KNOWN_TRANSACTION_TYPES,
    LANGUAGES,
    MESSAGES,
    SECTION_IDS,
    _amount,
    _join,
    _percent,
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
NBSP = chr(0xA0)  # thousands separator and the space before % in Spanish


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
        assert f'generated_from: "{SOURCE}"' in text.split("---")[1]
        assert SOURCE not in text.split("---", 2)[2]


def test_every_language_covers_every_category_status_reason_and_section() -> None:
    """A new enum member without a translation fails here instead of rendering a KeyError."""
    for language in LANGUAGES:
        messages = MESSAGES[language]
        assert set(messages.categories) == set(DisputeCategory)
        assert set(messages.statuses) == set(TransactionStatus)
        assert set(messages.statuses_plural) == set(TransactionStatus)
        assert set(messages.reason_codes) == set(ReasonCode)
        assert set(messages.section_titles) == set(SECTION_IDS)
        assert set(messages.product_names) == set(KNOWN_PRODUCT_TYPES)
        assert set(messages.transaction_types_indefinite) == set(KNOWN_TRANSACTION_TYPES)
        assert set(messages.transaction_types_plural) == set(KNOWN_TRANSACTION_TYPES)


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
        ("es", f"5{NBSP}000 USD", f"60{NBSP}%", "0,80", "120 días"),
        ("pt", "5.000 USD", "60%", "0,80", "120 dias"),
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
    assert {"en": "7,500.50", "es": f"7{NBSP}500,50", "pt": "7.500,50"}[language] in text
    assert not {"5.000", "5,000", f"5{NBSP}000"} & {
        n for n in ("5.000", "5,000", f"5{NBSP}000") if n in text
    }
    percent = MESSAGES[language].percent_format
    assert percent.format(value=75) in text
    assert percent.format(value=60) not in text
    assert ("0.95" if language == "en" else "0,95") in text


@pytest.mark.parametrize(
    ("value", "english", "spanish", "portuguese"),
    [
        (Decimal("5000.00"), "5,000", f"5{NBSP}000", "5.000"),
        (Decimal("1234.5"), "1,234.50", f"1{NBSP}234,50", "1.234,50"),
        (Decimal("0.01"), "0.01", "0,01", "0,01"),
        (Decimal("1000000"), "1,000,000", f"1{NBSP}000{NBSP}000", "1.000.000"),
        (Decimal("4999.999"), "4,999.999", f"4{NBSP}999,999", "4.999,999"),
        (Decimal("5000.10"), "5,000.10", f"5{NBSP}000,10", "5.000,10"),
    ],
)
def test_amounts_use_each_language_s_separators(
    value: Decimal, english: str, spanish: str, portuguese: str
) -> None:
    """A whole amount drops its fraction; a fractional one keeps two decimals."""
    assert _amount(value, MESSAGES["en"]) == english
    assert _amount(value, MESSAGES["es"]) == spanish
    assert _amount(value, MESSAGES["pt"]) == portuguese
    assert _rate(0.8, MESSAGES["es"]) == "0,80"
    assert _rate(0.8, MESSAGES["en"]) == "0.80"


def test_rates_and_percentages_are_never_rounded() -> None:
    """The text states exactly the value the engine compares with."""
    en, es = MESSAGES["en"], MESSAGES["es"]

    assert _rate(0.855, en) == "0.855" and _rate(0.995, es) == "0,995"
    assert _rate(0.8, en) == "0.80" and _rate(1.0, en) == "1.00" and _rate(0.0, en) == "0.00"
    assert _percent(0.605, en) == "60.5%" and _percent(0.999, es) == f"99,9{NBSP}%"
    assert _percent(0.6, en) == "60%" and _percent(1.0, es) == f"100{NBSP}%"


def _parse_number(text: str, language: str) -> Decimal:
    """Read a number written in a language's format back into a Decimal."""
    if language == "en":
        return Decimal(text.replace(",", ""))
    if language == "es":
        return Decimal(text.replace(NBSP, "").replace(",", "."))
    return Decimal(text.replace(".", "").replace(",", "."))


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    ("floor", "threshold", "amount"),
    [
        (0.605, 0.855, Decimal("4999.999")),
        (0.999, 0.995, Decimal("0.01")),
        (0.6, 0.8, Decimal("5000.00")),
        (0.0, 1.0, Decimal("1234567.89")),
    ],
)
def test_the_numbers_in_the_text_parse_back_to_exactly_the_policy_values(
    policy: Policy, language: str, floor: float, threshold: float, amount: Decimal
) -> None:
    """No rounding for display: floor, threshold and amount round-trip through the text."""
    changed = _routing(
        policy,
        nlu_confidence_floor=floor,
        risk_score_threshold=threshold,
        escalate_amount_usd=amount,
    )
    text = render_corpus(changed)[f"{language}/dispute-policy.md"]
    section = text.split("{#human-review}")[1].split("{#fraud-claims}")[0]

    number = rf"([\d.,{NBSP}]+)"
    percent = re.search(rf"\([^()]*?{number}\s?%\)", section)
    score = re.search(r"([\d.,]+) (?:or|o|ou) (?:higher|más|mais)", section)
    money = re.search(rf"{number} USD", section)
    assert percent and score and money
    assert _parse_number(percent.group(1), language) == Decimal(str(floor)) * 100
    assert _parse_number(score.group(1), language) == Decimal(str(threshold))
    assert _parse_number(money.group(1), language) == amount


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_conditions_of_a_dispute_are_all_stated(policy: Policy, language: str) -> None:
    """The text lists every gate the engine applies: type, status, deadline and no open dispute."""
    messages = MESSAGES[language]
    text = render_corpus(policy)[f"{language}/dispute-policy.md"]
    section = text.split("{#who-can-dispute}")[1].split("{#filing-windows}")[0]

    assert messages.transactions.split("{types}")[0] in section
    assert messages.transactions.split("{approved}")[1] in section


@pytest.mark.parametrize("language", LANGUAGES)
def test_exclusions_are_derived_from_what_the_policy_accepts(policy: Policy, language: str) -> None:
    """A product or type the policy accepts is never listed as excluded, and vice versa."""
    messages = MESSAGES[language]
    wider = policy.model_copy(
        update={
            "in_scope_product_types": policy.in_scope_product_types | {"Seguro"},
            "disputable_transaction_types": policy.disputable_transaction_types | {"Deposit"},
        }
    )
    everything = policy.model_copy(
        update={
            "in_scope_product_types": frozenset(KNOWN_PRODUCT_TYPES),
            "disputable_transaction_types": frozenset(KNOWN_TRANSACTION_TYPES),
        }
    )
    base = render_corpus(policy)[f"{language}/dispute-policy.md"]
    widened = render_corpus(wider)[f"{language}/dispute-policy.md"]
    complete = render_corpus(everything)[f"{language}/dispute-policy.md"]

    other = messages.products_out_of_scope.split("{products}")[0]
    excluded_types = messages.types_excluded.split("{types}")[0]
    insurance = messages.product_names["Seguro"]
    deposits = messages.transaction_types_plural["Deposit"]
    assert insurance in base.split(other)[1].split(")")[0]
    assert insurance not in widened.split(other)[1].split(")")[0]
    assert deposits in base.split(excluded_types)[1].split(".")[0]
    assert deposits not in widened.split(excluded_types)[1].split(".")[0]
    assert other not in complete
    assert deposits not in complete


def test_stray_hidden_files_are_not_drift(tmp_path: Path, policy: Policy) -> None:
    """Editor and operating-system debris in the corpus folder does not fail the check."""
    write_corpus(policy, tmp_path, source=SOURCE)
    (tmp_path / ".DS_Store").write_bytes(b"x")
    (tmp_path / "en" / ".swp").write_bytes(b"x")

    assert check_corpus(policy, tmp_path, source=SOURCE) == []


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

    assert "(a purchase or Refund)" in text


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


# -----------------------------------------------------------------------------
# Language: display names, wording of lists, terminology, calendar days
# -----------------------------------------------------------------------------


def test_no_dataset_product_code_reaches_the_customer_text(policy: Policy) -> None:
    """The codes are engine identifiers; every language shows its own display names."""
    for text in render_corpus(policy).values():
        for code in KNOWN_PRODUCT_TYPES:
            assert not re.search(rf"\b{re.escape(code)}\b", text)


@pytest.mark.parametrize(
    ("language", "covered", "others"),
    [
        (
            "es",
            "Cuenta de ahorros, Cuenta corriente, Tarjeta de crédito y Tarjeta de débito",
            "Préstamo personal, Crédito hipotecario, Inversiones y Seguros",
        ),
        (
            "pt",
            "Conta poupança, Conta corrente, Cartão de crédito e Cartão de débito",
            "Empréstimo pessoal, Financiamento imobiliário, Investimentos e Seguros",
        ),
        (
            "en",
            "Savings account, Checking account, Credit card and Debit card",
            "Personal loan, Mortgage, Investments and Insurance",
        ),
    ],
)
def test_products_are_named_in_the_language_and_listed_in_a_fixed_order(
    policy: Policy, language: str, covered: str, others: str
) -> None:
    """Covered products and the others read as the customer's language names them."""
    text = render_corpus(policy)[f"{language}/dispute-policy.md"]

    assert f": {covered}." in text
    assert f"({others})" in text


@pytest.mark.parametrize(
    ("language", "charges", "excluded", "statuses"),
    [
        (
            "es",
            "(una compra, un retiro, una transferencia o un pago)",
            "No se pueden disputar depósitos ni ajustes.",
            "Tampoco se pueden disputar transacciones rechazadas, pendientes o revertidas.",
        ),
        (
            "pt",
            "(uma compra, um saque, uma transferência ou um pagamento)",
            "Não podem ser contestados depósitos nem ajustes.",
            "Também não podem ser contestadas transações recusadas, pendentes ou estornadas.",
        ),
        (
            "en",
            "(a purchase, a withdrawal, a transfer or a payment)",
            "Transactions that are deposits or adjustments cannot be disputed.",
            "Transactions that are declined, pending or reversed cannot be disputed either.",
        ),
    ],
)
def test_alternatives_use_or_and_exclusions_use_nor_with_agreeing_forms(
    policy: Policy, language: str, charges: str, excluded: str, statuses: str
) -> None:
    """A charge is one of the types, not all of them; exclusions and statuses agree in number."""
    text = render_corpus(policy)[f"{language}/dispute-policy.md"]

    assert charges in text
    assert excluded in text
    assert statuses in text


def test_a_fraud_report_is_never_called_a_complaint(policy: Policy) -> None:
    """In Spanish and Portuguese the fraud category has its own word; complaints keep theirs."""
    es = render_corpus(policy)["es/dispute-policy.md"]
    pt = render_corpus(policy)["pt/dispute-policy.md"]

    assert "reporte de fraude" in es.lower() and "reclamo de fraude" not in es.lower()
    assert "reclamos repetidos" in es and "reclamos de fraude" not in es.lower()
    assert "contestação por fraude" in pt.lower() and "alegação" not in pt.lower()
    assert "reclamações repetidas" in pt


def _example(days: str, after: str, text: str) -> bool:
    """Whether the deadline paragraph states ``days`` twice, then ``after`` as the day past it."""
    paragraph = text.split("{#filing-windows}", 1)[1].split("\n- ", 1)[0]
    pattern = rf"(?<!\d){days}(?!\d)\D+(?<!\d){days}(?!\d)\D+(?<!\d){after}(?!\d)"
    return re.search(pattern, paragraph) is not None


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_deadline_is_in_calendar_days_with_an_example_from_the_policy(
    policy: Policy, language: str
) -> None:
    """The worked example uses the shortest window of the policy, and follows it when it changes."""
    calendar = {"es": "días calendario", "pt": "dias corridos", "en": "calendar days"}[language]
    shorter = _window(policy, DisputeCategory.DUPLICATE_CHARGE, 45)

    base = render_corpus(policy)[f"{language}/dispute-policy.md"]
    changed = render_corpus(shorter)[f"{language}/dispute-policy.md"]

    assert calendar in base
    assert _example("60", "61", base)
    assert _example("45", "46", changed)


def test_generation_details_are_not_part_of_the_readable_text(policy: Policy) -> None:
    """The note about regenerating lives in the front matter, outside every section."""
    for text in render_corpus(policy).values():
        body = text.split("---", 2)[2]
        assert "Generado" not in body and "Gerado" not in body and "Generated" not in body
        assert SOURCE not in body


def test_spanish_numbers_use_the_neutral_form(policy: Policy) -> None:
    """A space keeps thousands together and the comma marks decimals; Portuguese uses dots."""
    es = render_corpus(policy)["es/dispute-policy.md"]
    pt = render_corpus(policy)["pt/dispute-policy.md"]

    assert f"5{NBSP}000 USD" in es and f"60{NBSP}%" in es and "0,80" in es
    assert "5.000 USD" in pt and "60%" in pt and "0,80" in pt
