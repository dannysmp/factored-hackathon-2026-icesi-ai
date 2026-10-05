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
    _join,
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


def _response_days(policy: Policy, category: DisputeCategory, days: int) -> Policy:
    """A copy of ``policy`` with one category's first-response count changed."""
    updated = {**policy.first_response_days, category: days}
    return policy.model_copy(update={"first_response_days": updated})


def _evidence(policy: Policy, category: DisputeCategory, items: tuple[str, ...]) -> Policy:
    """A copy of ``policy`` with one category's evidence list changed."""
    updated = {**policy.evidence_required, category: items}
    return policy.model_copy(update={"evidence_required": updated})


def _document(policy: Policy, language: str) -> str:
    return render_corpus(policy)[f"{language}/dispute-policy.md"]


def _section(text: str, section_id: str) -> str:
    """The body of one section, by its identifier, up to the next section heading."""
    return text.split(f"{{#{section_id}}}", 1)[1].split("\n\n## ", 1)[0]


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
# The customer-facing text discloses no threshold, floor or trigger
# -----------------------------------------------------------------------------


def test_the_human_review_section_names_only_fraud_confidence_and_a_catch_all(
    policy: Policy,
) -> None:
    """Exactly three lines: a fraud claim, understanding failure, and the bank's other criteria."""
    for language in LANGUAGES:
        m = MESSAGES[language]
        section = _section(_document(policy, language), "human-review")

        lines = [line for line in section.strip().splitlines() if line]
        assert lines[1:] == [m.human_fraud, m.human_confidence, m.human_criteria]


def test_every_escalate_reason_reads_as_a_person_reviewing_the_request_with_no_trigger(
    policy: Policy,
) -> None:
    """Every review reason in the table is the same statement: no number, no trigger named."""
    escalate_codes = [code for code in ReasonCode if code.value.startswith("escalate_")]
    assert len(escalate_codes) == 6

    for language in LANGUAGES:
        m = MESSAGES[language]
        texts = {m.reason_codes[code] for code in escalate_codes}
        assert len(texts) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"nlu_confidence_floor": 0.75},
        {"risk_score_threshold": 0.95},
        {"escalate_repeat_complainer": False},
        {"escalate_unknown_amount": False},
        {"risk_routing_enabled": True},
    ],
    ids=["floor", "risk-threshold", "repeat-off", "unknown-amount-off", "risk-routing-on"],
)
def test_the_human_review_section_never_changes_with_a_routing_parameter(
    policy: Policy, updates: dict[str, object]
) -> None:
    """None of the routing flags or thresholds can be told apart from the rendered text."""
    changed = _routing(policy, **updates)

    for language in LANGUAGES:
        base = _section(_document(policy, language), "human-review")
        after = _section(_document(changed, language), "human-review")
        assert base == after


@pytest.mark.parametrize(
    ("language", "floor_percent", "amount_thousands", "risk_percent"),
    [
        ("en", "60%", "5,000", "80%"),
        ("es", f"60{NBSP}%", f"5{NBSP}000", f"80{NBSP}%"),
        ("pt", "60%", "5.000", "80%"),
    ],
)
def test_no_threshold_or_confidence_floor_appears_in_any_language(
    policy: Policy, language: str, floor_percent: str, amount_thousands: str, risk_percent: str
) -> None:
    """A test reads the thresholds from the policy file and scans the corpus for them."""
    text = _document(policy, language)

    stripped = text.replace("\u00a0", "").replace(".", "").replace(",", "")
    assert str(policy.routing.escalate_amount_usd).split(".")[0] not in stripped
    assert floor_percent not in text
    assert amount_thousands not in text
    assert risk_percent not in text
    assert "reclam" not in text.lower()


def test_a_planted_threshold_value_is_caught_by_the_drift_scan(policy: Policy) -> None:
    """A negative test: the scan fails when a forbidden value is planted in the text."""
    text = _document(policy, "en")
    poisoned = text.replace("It is a fraud claim.", "It is a fraud claim (60%).")

    assert "60%" not in text
    assert "60%" in poisoned


# -----------------------------------------------------------------------------
# Response time and evidence
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_response_time_section_states_each_category_s_first_response_days(
    policy: Policy, language: str
) -> None:
    """Every category's calendar-day count for the first response is in the text."""
    section = _section(_document(policy, language), "response-time")

    for category in DisputeCategory:
        days = policy.first_response_days[category]
        unit = {"es": "día" if days == 1 else "días", "pt": "dia" if days == 1 else "dias"}.get(
            language, "day" if days == 1 else "days"
        )
        assert f"{days} {unit}" in section


def test_changing_a_first_response_count_changes_only_that_category_s_line(policy: Policy) -> None:
    """The response-time section tracks the policy, one category at a time."""
    changed = _response_days(policy, DisputeCategory.SERVICE_NOT_RECEIVED, 10)

    base = _section(_document(policy, "en"), "response-time")
    after = _section(_document(changed, "en"), "response-time")

    assert "Service not received: 5 days." in base
    assert "Service not received: 10 days." in after
    assert "Fraud claim: 1 day." in base and "Fraud claim: 1 day." in after


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_evidence_section_lists_every_category_s_items(policy: Policy, language: str) -> None:
    """Every category has at least one evidence item stated in the reply language."""
    m = MESSAGES[language]
    section = _section(_document(policy, language), "evidence")

    for category in DisputeCategory:
        items = policy.evidence_required[category]
        assert items
        for item in items:
            assert m.evidence_items[item] in section


def test_changing_the_evidence_list_changes_only_that_category_s_line(policy: Policy) -> None:
    """The evidence section tracks the policy; other categories are untouched."""
    changed = _evidence(policy, DisputeCategory.WRONG_AMOUNT, ("card_status", "last_genuine_use"))

    base = _section(_document(policy, "en"), "evidence")
    after = _section(_document(changed, "en"), "evidence")

    assert "Wrong amount: proof of the agreed amount" in base
    assert "Wrong amount: whether the card is lost, stolen or still in your hands" in after
    assert "Duplicate charge: the dates and amounts of both charges." in base
    assert "Duplicate charge: the dates and amounts of both charges." in after


# -----------------------------------------------------------------------------
# The conditions of a dispute
# -----------------------------------------------------------------------------


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_conditions_of_a_dispute_are_all_stated(policy: Policy, language: str) -> None:
    """The text lists every gate the engine applies: type, status, deadline and no open dispute."""
    messages = MESSAGES[language]
    text = render_corpus(policy)[f"{language}/dispute-policy.md"]
    section = text.split("{#who-can-dispute}")[1].split("{#filing-windows}")[0]

    assert messages.transactions.split("{types}")[0] in section
    assert messages.transactions.split("{approved}")[1] in section


_FUTURE_DATE_KEYWORD = {"es": "futur", "pt": "futur", "en": "future"}


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_future_dated_transaction_cannot_be_disputed(policy: Policy, language: str) -> None:
    """The who-can-dispute section states the future-date gate, the one condition that used to
    be missing from the sentence (`_gate_future_date` in the engine, reason
    ``transaction_date_in_future``)."""
    text = render_corpus(policy)[f"{language}/dispute-policy.md"]
    section = text.split("{#who-can-dispute}")[1].split("{#filing-windows}")[0]

    assert _FUTURE_DATE_KEYWORD[language] in section.lower()


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
# Confirmation
# -----------------------------------------------------------------------------


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


@pytest.mark.parametrize("language", LANGUAGES)
def test_four_of_five_categories_never_read_as_all_of_them(policy: Policy, language: str) -> None:
    """One category short of every one still names them, and never prints the ``all`` text."""
    messages = MESSAGES[language]
    missing = DisputeCategory.SERVICE_NOT_RECEIVED
    required = [c for c in DisputeCategory if c is not missing]
    categories = {
        c: policy.categories[c].model_copy(update={"requires_confirmation": c is not missing})
        for c in DisputeCategory
    }
    text = render_corpus(policy.model_copy(update={"categories": categories}))[
        f"{language}/dispute-policy.md"
    ]

    assert messages.confirmation_all not in text
    for category in required:
        assert messages.categories[category] in text
    assert (
        messages.categories[missing]
        not in text.split("{#confirmation}")[1].split("{#human-review}")[0]
    )


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
    """Fraud has its own word in Spanish and Portuguese; no complaint-history statement remains."""
    es = render_corpus(policy)["es/dispute-policy.md"]
    pt = render_corpus(policy)["pt/dispute-policy.md"]

    assert "reporte de fraude" in es.lower() and "reclamo" not in es.lower()
    assert "contestação por fraude" in pt.lower() and "reclamaç" not in pt.lower()


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


# -----------------------------------------------------------------------------
# Terminology pinned in the text itself (survives regenerating the corpus)
# -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "category_line", "fraud_bullet", "advisor_line"),
    [
        (
            "es",
            "- Reporte de fraude: 180 días.",
            "- Es un reporte de fraude.",
            "pasa a revisión de un asesor en estos casos:",
        ),
        (
            "pt",
            "- Contestação por fraude: 180 dias.",
            "- É uma contestação por fraude.",
            "um atendente o analisa nestes casos:",
        ),
    ],
)
def test_the_fraud_category_and_the_human_step_use_their_own_words(
    policy: Policy, language: str, category_line: str, fraud_bullet: str, advisor_line: str
) -> None:
    """The exact list line, bullet and intro are asserted, so a changed template fails here."""
    text = _document(policy, language)

    assert category_line in text
    assert fraud_bullet in text
    assert advisor_line in text


@pytest.mark.parametrize(
    ("language", "advisor", "forbidden"),
    [("es", "asesor", "agente"), ("pt", "atendente", "agente")],
)
def test_the_human_step_is_always_the_advisor_term(
    policy: Policy, language: str, advisor: str, forbidden: str
) -> None:
    """Every routing sentence and reason row names the advisor, never another term."""
    text = _document(policy, language)

    assert text.count(advisor) >= 8
    assert forbidden not in text.lower()


def test_portuguese_reason_rows_name_the_advisor_as_the_subject_of_the_review(
    policy: Policy,
) -> None:
    """Every routing reason states the same sentence, with the advisor as its subject."""
    rows = [
        line for line in _document(policy, "pt").splitlines() if line.startswith("| `escalate_")
    ]

    assert len(rows) == 6
    assert all(row.endswith("| Um atendente analisa este pedido. |") for row in rows)


@pytest.mark.parametrize(
    ("language", "sentence"),
    [
        ("es", "Un asesor revisa esta solicitud."),
        ("pt", "Um atendente analisa este pedido."),
        ("en", "A person reviews this request."),
    ],
)
def test_every_routing_reason_states_the_same_sentence_in_every_language(
    policy: Policy, language: str, sentence: str
) -> None:
    """The six routing reasons each read as one sentence about "this" request."""
    rows = [
        line for line in _document(policy, language).splitlines() if line.startswith("| `escalate_")
    ]

    assert len(rows) == 6
    assert all(row.endswith(f"| {sentence} |") for row in rows)


def test_the_spanish_decision_codes_introduction_describes_the_table_without_claiming_a_display(
    policy: Policy,
) -> None:
    """The sentence quotes the table's wording and does not say it is shown to the customer."""
    text = _document(policy, "es")

    assert "se describe con la frase: “Un asesor revisa esta solicitud.”" in text
    assert "se muestra" not in text


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_category_that_alone_requires_confirmation_is_named_once(
    policy: Policy, language: str
) -> None:
    """Only the fraud category confirms: the sentence names it once and is grammatical."""
    only_fraud = policy.model_copy(
        update={
            "categories": {
                c: policy.categories[c].model_copy(
                    update={"requires_confirmation": c is DisputeCategory.FRAUD_CLAIM}
                )
                for c in DisputeCategory
            }
        }
    )
    messages = MESSAGES[language]
    label = messages.categories[DisputeCategory.FRAUD_CLAIM]

    text = _document(only_fraud, language)
    sentence = messages.confirmation_some.format(categories=label)

    opening = {
        "es": "En los casos de reporte de fraude, antes de presentar la disputa,",
        "pt": "Nos casos de contestação por fraude, antes de apresentar a contestação,",
        "en": "For fraud claim disputes, before the dispute is filed,",
    }[language]
    assert sentence in text
    assert text.count(opening) == 1
    assert sentence.count(label) == 1


@pytest.mark.parametrize("language", LANGUAGES)
def test_a_window_of_one_day_uses_the_singular(policy: Policy, language: str) -> None:
    """`1 día`, `1 dia` and `1 day`, never `1 días`."""
    messages = MESSAGES[language]
    one_day = _window(policy, DisputeCategory.DUPLICATE_CHARGE, 1)

    text = _document(one_day, language)

    assert f": 1 {messages.day_one}." in text
    assert f"de 1 {messages.day_one}," in text or f"of 1 {messages.day_one}," in text
    assert re.search(rf"(?<!\d)1 {messages.day_many}", text) is None
