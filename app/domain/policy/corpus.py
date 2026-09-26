"""
Dispute Policy Corpus Renderer
==============================

Overview
--------
Renders the dispute policy as customer-facing text in Spanish, Portuguese and English, one
Markdown document per language with stable section identifiers. The text answers the questions a
customer asks ("how long do I have?", "when does a person review my dispute?") and is the
material retrieval later searches, so an explanation can only quote what the engine enforces.

Scope
-----
In: turning a validated ``Policy`` into text; the section identifiers and the message templates.
Out: reading the policy (``loader``), writing the files and checking them for drift
(``pipelines.policy_corpus``).

Design Principles
-----------------
- Every number in the text is taken from the policy, never typed into a template, so the corpus
  cannot say what the engine does not enforce. A test compares the committed files with this
  output and fails on any difference.
- Pure function: the same policy always renders the same text, byte for byte.
- Section identifiers are the same in every language and never change, so an answer can cite
  ``filing-windows`` whichever language the customer used.
- Rules that a policy version switches off (for example repeat-complainer routing) are left out
  of the text instead of being described as active.

Runtime Contract
----------------
``render_corpus(policy) -> dict[str, str]`` maps a relative file path (``es/dispute-policy.md``)
to its text. ``LANGUAGES`` and ``SECTION_IDS`` name what is rendered.

Limitations
-----------
Product and transaction-type labels are the source's own and appear as they are, with a
translation of the transaction type where one is known. The text is synthetic policy prose
written for this project, not legal advice.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable message sets per language
from decimal import Decimal  # Money formatted without float error

# Local modules
from app.domain.policy.models import (  # Vocabulary rendered in the text
    DisputeCategory,
    Policy,
    ReasonCode,
    TransactionStatus,
)

LANGUAGES: tuple[str, ...] = ("es", "pt", "en")

# Stable identifiers of the sections, in reading order; identical in every language.
SECTION_IDS: tuple[str, ...] = (
    "overview",
    "who-can-dispute",
    "filing-windows",
    "confirmation",
    "human-review",
    "fraud-claims",
    "decision-codes",
)

DOCUMENT_NAME = "dispute-policy.md"


@dataclass(frozen=True, slots=True)
class Messages:
    """Every piece of prose of one language."""

    title: str
    generated_note: str
    decimal_separator: str
    thousands_separator: str
    percent_format: str
    section_titles: dict[str, str]
    overview: str
    products: str
    products_out_of_scope: str
    transactions: str
    types_excluded: str
    statuses_excluded: str
    windows_intro: str
    window_line: str
    confirmation_all: str
    confirmation_some: str
    confirmation_none: str
    human_intro: str
    human_fraud: str
    human_confidence: str
    human_repeat: str
    human_amount: str
    human_unknown_amount: str
    human_risk: str
    fraud: str
    codes_intro: str
    codes_header: tuple[str, str]
    categories: dict[DisputeCategory, str]
    transaction_types: dict[str, str]
    statuses: dict[TransactionStatus, str]
    reason_codes: dict[ReasonCode, str]
    and_word: str


_ES = Messages(
    title="Política de disputas de transacciones",
    generated_note=(
        "Generado a partir de la política `{source}` (versión {version}). No editar a mano: "
        "cualquier cambio se hace en la política y se regenera."
    ),
    decimal_separator=",",
    thousands_separator=".",
    percent_format="{value} %",
    section_titles={
        "overview": "Qué es esta política",
        "who-can-dispute": "Qué transacciones se pueden disputar",
        "filing-windows": "Plazos para presentar una disputa",
        "confirmation": "Confirmación antes de presentar",
        "human-review": "Cuándo lo revisa una persona",
        "fraud-claims": "Reclamos de fraude",
        "decision-codes": "Motivos de cada decisión",
    },
    overview=(
        "Esta política explica cómo se decide una solicitud de disputa sobre una transacción de "
        "cuentas y tarjetas. Es una política sintética escrita para este proyecto: no es la de "
        "ningún banco ni regulador, y no es asesoría legal. Cada decisión se toma con reglas "
        "fijas y queda registrada con un motivo."
    ),
    products="Se pueden disputar las transacciones de estos productos: {products}.",
    products_out_of_scope=(
        "Los demás productos ({products}) tienen sus propios procesos de reclamo y no se "
        "disputan aquí."
    ),
    transactions=(
        "Para presentar una disputa, la transacción debe ser un cargo al cliente ({types}), "
        "tener estado {approved}, estar dentro del plazo de su categoría (ver más abajo) y no "
        "tener ya otra disputa abierta."
    ),
    types_excluded="No se pueden disputar las transacciones de estos tipos: {types}.",
    statuses_excluded=(
        "No se pueden disputar las transacciones con estado {declined}, {pending} o {reversed}."
    ),
    windows_intro=(
        "La disputa debe presentarse dentro de un plazo, contado en días desde la fecha de la "
        "transacción. El último día válido es el día que indica el plazo; al día siguiente ya no "
        "se puede presentar."
    ),
    window_line="- {category}: {days} días.",
    confirmation_all=(
        "Cuando una disputa se puede presentar, y antes de presentarla, el cliente confirma la "
        "presentación exacta (transacción, motivo y datos)."
    ),
    confirmation_some=(
        "Cuando una disputa por {categories} se puede presentar, y antes de presentarla, el "
        "cliente confirma la presentación exacta (transacción, motivo y datos)."
    ),
    confirmation_none="La política no exige confirmación previa a la presentación.",
    human_intro=("Aunque la solicitud cumpla las reglas, la revisa una persona en estos casos:"),
    human_fraud="- Es un reclamo de fraude.",
    human_confidence=(
        "- No se entendió la solicitud con suficiente seguridad (menos de {percent})."
    ),
    human_repeat="- El cliente ha presentado reclamos repetidos.",
    human_amount="- El monto es de {amount} USD o más.",
    human_unknown_amount="- No se conoce el monto en dólares.",
    human_risk=(
        "- El puntaje de riesgo de la transacción es {score} o más. El puntaje solo decide que "
        "la revise una persona; nunca decide el resultado."
    ),
    fraud=(
        "Un reclamo de fraude siempre lo revisa una persona. Nunca se rechaza automáticamente, "
        "aunque la transacción esté rechazada, fuera de plazo o de un producto fuera de alcance: "
        "en ese caso la persona recibe el motivo por el que la regla habría fallado."
    ),
    codes_intro="Cada decisión lleva uno de estos motivos.",
    codes_header=("Motivo", "Significado"),
    categories={
        DisputeCategory.UNRECOGNIZED_CHARGE: "cargo no reconocido",
        DisputeCategory.DUPLICATE_CHARGE: "cargo duplicado",
        DisputeCategory.WRONG_AMOUNT: "monto incorrecto",
        DisputeCategory.SERVICE_NOT_RECEIVED: "servicio no recibido",
        DisputeCategory.FRAUD_CLAIM: "reclamo de fraude",
    },
    transaction_types={
        "Purchase": "compra",
        "Withdrawal": "retiro",
        "Transfer": "transferencia",
        "Payment": "pago",
        "Deposit": "depósito",
        "Adjustment": "ajuste",
    },
    statuses={
        TransactionStatus.APPROVED: "aprobado",
        TransactionStatus.DECLINED: "rechazado",
        TransactionStatus.PENDING: "pendiente",
        TransactionStatus.REVERSED: "revertido",
    },
    reason_codes={
        ReasonCode.ELIGIBLE: "La disputa se puede presentar, previa confirmación.",
        ReasonCode.PRODUCT_OUT_OF_SCOPE: "El producto no está dentro del alcance de esta política.",
        ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE: (
            "El tipo de transacción no es un cargo que se pueda disputar."
        ),
        ReasonCode.TRANSACTION_DECLINED: "La transacción fue rechazada: no hubo cargo.",
        ReasonCode.TRANSACTION_PENDING: "La transacción sigue pendiente.",
        ReasonCode.TRANSACTION_REVERSED: "La transacción ya fue revertida.",
        ReasonCode.TRANSACTION_DATE_IN_FUTURE: "La fecha de la transacción es posterior a hoy.",
        ReasonCode.FILING_WINDOW_EXPIRED: "Venció el plazo para presentar esta disputa.",
        ReasonCode.DUPLICATE_OPEN_CASE: "Ya hay una disputa abierta para esta transacción.",
        ReasonCode.ESCALATE_FRAUD_CLAIM: "Es un reclamo de fraude; pasa a revisión de una persona.",
        ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE: (
            "No se entendió la solicitud con suficiente seguridad; pasa a revisión de una persona."
        ),
        ReasonCode.ESCALATE_REPEAT_COMPLAINER: (
            "El cliente tiene reclamos repetidos; pasa a revisión de una persona."
        ),
        ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD: (
            "El monto alcanza el umbral de revisión; pasa a revisión de una persona."
        ),
        ReasonCode.ESCALATE_AMOUNT_UNKNOWN: (
            "No se conoce el monto en dólares; pasa a revisión de una persona."
        ),
        ReasonCode.ESCALATE_RISK_SCORE: (
            "El puntaje de riesgo alcanza el umbral; pasa a revisión de una persona."
        ),
    },
    and_word="y",
)

_PT = Messages(
    title="Política de contestação de transações",
    generated_note=(
        "Gerado a partir da política `{source}` (versão {version}). Não editar à mão: qualquer "
        "mudança é feita na política e regenerada."
    ),
    decimal_separator=",",
    thousands_separator=".",
    percent_format="{value}%",
    section_titles={
        "overview": "O que é esta política",
        "who-can-dispute": "Quais transações podem ser contestadas",
        "filing-windows": "Prazos para apresentar uma contestação",
        "confirmation": "Confirmação antes de apresentar",
        "human-review": "Quando uma pessoa revisa",
        "fraud-claims": "Alegações de fraude",
        "decision-codes": "Motivos de cada decisão",
    },
    overview=(
        "Esta política explica como se decide um pedido de contestação de uma transação de "
        "contas e cartões. É uma política sintética escrita para este projeto: não é a de nenhum "
        "banco nem regulador, e não é aconselhamento jurídico. Cada decisão é tomada com regras "
        "fixas e fica registrada com um motivo."
    ),
    products="Podem ser contestadas as transações destes produtos: {products}.",
    products_out_of_scope=(
        "Os demais produtos ({products}) têm seus próprios processos de reclamação e não são "
        "contestados aqui."
    ),
    transactions=(
        "Para apresentar uma contestação, a transação deve ser uma cobrança ao cliente "
        "({types}), ter status {approved}, estar dentro do prazo da sua categoria (veja "
        "abaixo) e não ter outra contestação aberta."
    ),
    types_excluded="Não podem ser contestadas as transações destes tipos: {types}.",
    statuses_excluded=(
        "Não podem ser contestadas as transações com status {declined}, {pending} ou {reversed}."
    ),
    windows_intro=(
        "A contestação deve ser apresentada dentro de um prazo, contado em dias a partir da data "
        "da transação. O último dia válido é o dia indicado pelo prazo; no dia seguinte já não "
        "pode ser apresentada."
    ),
    window_line="- {category}: {days} dias.",
    confirmation_all=(
        "Quando uma contestação pode ser apresentada, e antes de apresentá-la, o cliente "
        "confirma a apresentação exata (transação, motivo e dados)."
    ),
    confirmation_some=(
        "Quando uma contestação por {categories} pode ser apresentada, e antes de apresentá-la, "
        "o cliente confirma a apresentação exata (transação, motivo e dados)."
    ),
    confirmation_none="A política não exige confirmação antes da apresentação.",
    human_intro=("Mesmo que o pedido cumpra as regras, uma pessoa o revisa nestes casos:"),
    human_fraud="- É uma alegação de fraude.",
    human_confidence=(
        "- O pedido não foi entendido com segurança suficiente (menos de {percent})."
    ),
    human_repeat="- O cliente apresentou reclamações repetidas.",
    human_amount="- O valor é de {amount} USD ou mais.",
    human_unknown_amount="- O valor em dólares não é conhecido.",
    human_risk=(
        "- A pontuação de risco da transação é {score} ou mais. A pontuação só decide que uma "
        "pessoa revise; nunca decide o resultado."
    ),
    fraud=(
        "Uma alegação de fraude é sempre revisada por uma pessoa. Nunca é recusada "
        "automaticamente, mesmo que a transação tenha sido recusada, esteja fora do prazo ou "
        "seja de um produto fora do alcance: nesse caso a pessoa recebe o motivo pelo qual a "
        "regra teria falhado."
    ),
    codes_intro="Cada decisão traz um destes motivos.",
    codes_header=("Motivo", "Significado"),
    categories={
        DisputeCategory.UNRECOGNIZED_CHARGE: "cobrança não reconhecida",
        DisputeCategory.DUPLICATE_CHARGE: "cobrança duplicada",
        DisputeCategory.WRONG_AMOUNT: "valor incorreto",
        DisputeCategory.SERVICE_NOT_RECEIVED: "serviço não recebido",
        DisputeCategory.FRAUD_CLAIM: "alegação de fraude",
    },
    transaction_types={
        "Purchase": "compra",
        "Withdrawal": "saque",
        "Transfer": "transferência",
        "Payment": "pagamento",
        "Deposit": "depósito",
        "Adjustment": "ajuste",
    },
    statuses={
        TransactionStatus.APPROVED: "aprovado",
        TransactionStatus.DECLINED: "recusado",
        TransactionStatus.PENDING: "pendente",
        TransactionStatus.REVERSED: "estornado",
    },
    reason_codes={
        ReasonCode.ELIGIBLE: "A contestação pode ser apresentada, mediante confirmação.",
        ReasonCode.PRODUCT_OUT_OF_SCOPE: "O produto não está no alcance desta política.",
        ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE: (
            "O tipo de transação não é uma cobrança que possa ser contestada."
        ),
        ReasonCode.TRANSACTION_DECLINED: "A transação foi recusada: não houve cobrança.",
        ReasonCode.TRANSACTION_PENDING: "A transação ainda está pendente.",
        ReasonCode.TRANSACTION_REVERSED: "A transação já foi estornada.",
        ReasonCode.TRANSACTION_DATE_IN_FUTURE: "A data da transação é posterior a hoje.",
        ReasonCode.FILING_WINDOW_EXPIRED: "O prazo para apresentar esta contestação venceu.",
        ReasonCode.DUPLICATE_OPEN_CASE: "Já existe uma contestação aberta para esta transação.",
        ReasonCode.ESCALATE_FRAUD_CLAIM: (
            "É uma alegação de fraude; passa para revisão de uma pessoa."
        ),
        ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE: (
            "O pedido não foi entendido com segurança suficiente; passa para revisão de uma pessoa."
        ),
        ReasonCode.ESCALATE_REPEAT_COMPLAINER: (
            "O cliente tem reclamações repetidas; passa para revisão de uma pessoa."
        ),
        ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD: (
            "O valor atinge o limite de revisão; passa para revisão de uma pessoa."
        ),
        ReasonCode.ESCALATE_AMOUNT_UNKNOWN: (
            "O valor em dólares não é conhecido; passa para revisão de uma pessoa."
        ),
        ReasonCode.ESCALATE_RISK_SCORE: (
            "A pontuação de risco atinge o limite; passa para revisão de uma pessoa."
        ),
    },
    and_word="e",
)

_EN = Messages(
    title="Transaction dispute policy",
    generated_note=(
        "Generated from the policy `{source}` (version {version}). Do not edit by hand: any "
        "change is made in the policy and regenerated."
    ),
    decimal_separator=".",
    thousands_separator=",",
    percent_format="{value}%",
    section_titles={
        "overview": "What this policy is",
        "who-can-dispute": "Which transactions can be disputed",
        "filing-windows": "Deadlines to file a dispute",
        "confirmation": "Confirmation before filing",
        "human-review": "When a person reviews it",
        "fraud-claims": "Fraud claims",
        "decision-codes": "Reasons for each decision",
    },
    overview=(
        "This policy explains how a request to dispute a transaction on an account or card is "
        "decided. It is a synthetic policy written for this project: it is not any bank's or "
        "regulator's policy, and it is not legal advice. Every decision is made with fixed rules "
        "and recorded with a reason."
    ),
    products="Transactions on these products can be disputed: {products}.",
    products_out_of_scope=(
        "The other products ({products}) have their own claim processes and are not disputed here."
    ),
    transactions=(
        "To file a dispute, the transaction must be a charge to the customer ({types}), have "
        "the status {approved}, be within the deadline of its category (see below) and have no "
        "other open dispute."
    ),
    types_excluded="Transactions of these types cannot be disputed: {types}.",
    statuses_excluded=(
        "Transactions with the status {declined}, {pending} or {reversed} cannot be disputed."
    ),
    windows_intro=(
        "A dispute must be filed within a deadline, counted in days from the transaction date. "
        "The last valid day is the day the deadline states; the next day it can no longer be "
        "filed."
    ),
    window_line="- {category}: {days} days.",
    confirmation_all=(
        "When a dispute can be filed, and before it is filed, the customer confirms the exact "
        "filing (transaction, reason and details)."
    ),
    confirmation_some=(
        "When a dispute for {categories} can be filed, and before it is filed, the customer "
        "confirms the exact filing (transaction, reason and details)."
    ),
    confirmation_none="The policy does not require confirmation before filing.",
    human_intro="Even when a request meets the rules, a person reviews it in these cases:",
    human_fraud="- It is a fraud claim.",
    human_confidence="- The request was not understood with enough confidence (below {percent}).",
    human_repeat="- The customer has filed repeated complaints.",
    human_amount="- The amount is {amount} USD or more.",
    human_unknown_amount="- The amount in US dollars is not known.",
    human_risk=(
        "- The transaction's risk score is {score} or higher. The score only decides that a "
        "person reviews the dispute; it never decides the outcome."
    ),
    fraud=(
        "A fraud claim is always reviewed by a person. It is never refused automatically, even "
        "when the transaction was declined, is outside the deadline or is on a product outside "
        "the scope: in that case the person receives the reason the rule would have failed."
    ),
    codes_intro="Every decision carries one of these reasons.",
    codes_header=("Reason", "Meaning"),
    categories={
        DisputeCategory.UNRECOGNIZED_CHARGE: "unrecognized charge",
        DisputeCategory.DUPLICATE_CHARGE: "duplicate charge",
        DisputeCategory.WRONG_AMOUNT: "wrong amount",
        DisputeCategory.SERVICE_NOT_RECEIVED: "service not received",
        DisputeCategory.FRAUD_CLAIM: "fraud claim",
    },
    transaction_types={
        "Purchase": "purchase",
        "Withdrawal": "withdrawal",
        "Transfer": "transfer",
        "Payment": "payment",
        "Deposit": "deposit",
        "Adjustment": "adjustment",
    },
    statuses={
        TransactionStatus.APPROVED: "approved",
        TransactionStatus.DECLINED: "declined",
        TransactionStatus.PENDING: "pending",
        TransactionStatus.REVERSED: "reversed",
    },
    reason_codes={
        ReasonCode.ELIGIBLE: "The dispute can be filed, after confirmation.",
        ReasonCode.PRODUCT_OUT_OF_SCOPE: "The product is outside the scope of this policy.",
        ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE: (
            "The transaction type is not a charge that can be disputed."
        ),
        ReasonCode.TRANSACTION_DECLINED: "The transaction was declined: there was no charge.",
        ReasonCode.TRANSACTION_PENDING: "The transaction is still pending.",
        ReasonCode.TRANSACTION_REVERSED: "The transaction has already been reversed.",
        ReasonCode.TRANSACTION_DATE_IN_FUTURE: "The transaction date is after today.",
        ReasonCode.FILING_WINDOW_EXPIRED: "The deadline to file this dispute has passed.",
        ReasonCode.DUPLICATE_OPEN_CASE: "A dispute is already open for this transaction.",
        ReasonCode.ESCALATE_FRAUD_CLAIM: "It is a fraud claim; a person reviews it.",
        ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE: (
            "The request was not understood with enough confidence; a person reviews it."
        ),
        ReasonCode.ESCALATE_REPEAT_COMPLAINER: (
            "The customer has repeated complaints; a person reviews it."
        ),
        ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD: (
            "The amount reaches the review threshold; a person reviews it."
        ),
        ReasonCode.ESCALATE_AMOUNT_UNKNOWN: (
            "The amount in US dollars is not known; a person reviews it."
        ),
        ReasonCode.ESCALATE_RISK_SCORE: (
            "The risk score reaches the threshold; a person reviews it."
        ),
    },
    and_word="and",
)

MESSAGES: dict[str, Messages] = {"es": _ES, "pt": _PT, "en": _EN}


# -----------------------------------------------------------------------------
# Formatting
# -----------------------------------------------------------------------------


def _join(items: list[str], word: str) -> str:
    """Join with commas and a final conjunction: ``a, b and c``."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {word} {items[-1]}"


def _plain(number: Decimal) -> str:
    """A decimal written out in full, without an exponent and without trailing zeros."""
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _amount(value: Decimal, messages: Messages) -> str:
    """A US-dollar amount exactly as the policy holds it, with the language's separators.

    A whole amount drops its fraction; a fractional one keeps every digit and at least two
    decimals, so the text never states a value other than the one the engine compares with.
    """
    whole, _, fraction = _plain(value).partition(".")
    grouped = f"{int(whole):,}".replace(",", messages.thousands_separator)
    if not fraction:
        return grouped
    return f"{grouped}{messages.decimal_separator}{fraction.ljust(2, '0')}"


def _rate(value: float, messages: Messages) -> str:
    """A rate exactly as the policy holds it (at least two decimals), in the language's format."""
    whole, _, fraction = _plain(Decimal(str(value))).partition(".")
    return f"{whole}{messages.decimal_separator}{fraction.ljust(2, '0')}"


def _percent(value: float, messages: Messages) -> str:
    """A rate as a percentage without rounding: 0.6 is 60 and 0.605 is 60.5."""
    number = _plain(Decimal(str(value)) * 100).replace(".", messages.decimal_separator)
    return messages.percent_format.format(value=number)


def _capitalise(text: str) -> str:
    """The text with its first letter in upper case and the rest untouched."""
    return text[:1].upper() + text[1:]


def _label(mapping: dict[str, str], key: str) -> str:
    """A translated label, or the source's own spelling when there is no translation."""
    return mapping.get(key, key)


# -----------------------------------------------------------------------------
# Sections
# -----------------------------------------------------------------------------


# Labels the source uses for products and transaction types. The text lists as excluded whichever
# of these the policy does not accept, so an exclusion can only be stated when the policy makes it.
KNOWN_PRODUCT_TYPES: tuple[str, ...] = (
    "Cuenta Ahorro",
    "Cuenta Corriente",
    "Tarjeta Crédito",
    "Tarjeta Débito",
    "Préstamo Personal",
    "Préstamo Hipotecario",
    "Inversión",
    "Seguro",
)
KNOWN_TRANSACTION_TYPES: tuple[str, ...] = (
    "Purchase",
    "Withdrawal",
    "Transfer",
    "Payment",
    "Deposit",
    "Adjustment",
)


def _who_can_dispute(policy: Policy, m: Messages) -> str:
    """The conditions a transaction must meet, and what the policy leaves out."""

    def types(names: list[str]) -> str:
        return _join([_label(m.transaction_types, name) for name in names], m.and_word)

    accepted_types = sorted(policy.disputable_transaction_types)
    other_products = [p for p in KNOWN_PRODUCT_TYPES if p not in policy.in_scope_product_types]
    other_types = [
        t for t in KNOWN_TRANSACTION_TYPES if t not in policy.disputable_transaction_types
    ]
    paragraphs = [
        m.products.format(products=_join(sorted(policy.in_scope_product_types), m.and_word))
    ]
    if other_products:
        paragraphs.append(
            m.products_out_of_scope.format(products=_join(other_products, m.and_word))
        )
    paragraphs.append(
        m.transactions.format(
            types=types(accepted_types), approved=m.statuses[TransactionStatus.APPROVED]
        )
    )
    if other_types:
        paragraphs.append(m.types_excluded.format(types=types(other_types)))
    paragraphs.append(
        m.statuses_excluded.format(
            declined=m.statuses[TransactionStatus.DECLINED],
            pending=m.statuses[TransactionStatus.PENDING],
            reversed=m.statuses[TransactionStatus.REVERSED],
        )
    )
    return "\n\n".join(paragraphs)


def _filing_windows(policy: Policy, m: Messages) -> str:
    """One line per category with its window in days."""
    lines = [
        m.window_line.format(
            category=_capitalise(m.categories[category]),
            days=policy.categories[category].filing_window_days,
        )
        for category in DisputeCategory
    ]
    return m.windows_intro + "\n\n" + "\n".join(lines)


def _confirmation(policy: Policy, m: Messages) -> str:
    """Which dispute categories the customer must confirm before filing."""
    required = [c for c in DisputeCategory if policy.categories[c].requires_confirmation]
    if not required:
        return m.confirmation_none
    if len(required) == len(DisputeCategory):
        return m.confirmation_all
    names = _join([m.categories[c] for c in required], m.and_word)
    return m.confirmation_some.format(categories=names)


def _human_review(policy: Policy, m: Messages) -> str:
    """The routing rules the policy has switched on."""
    routing = policy.routing
    lines = [
        m.human_fraud,
        m.human_confidence.format(percent=_percent(routing.nlu_confidence_floor, m)),
    ]
    if routing.escalate_repeat_complainer:
        lines.append(m.human_repeat)
    lines.append(m.human_amount.format(amount=_amount(routing.escalate_amount_usd, m)))
    if routing.escalate_unknown_amount:
        lines.append(m.human_unknown_amount)
    lines.append(m.human_risk.format(score=_rate(routing.risk_score_threshold, m)))
    return m.human_intro + "\n\n" + "\n".join(lines)


def _decision_codes(m: Messages) -> str:
    """A table of every reason code and what it means."""
    header, meaning = m.codes_header
    rows = [f"| `{code.value}` | {m.reason_codes[code]} |" for code in ReasonCode]
    return "\n".join([m.codes_intro, "", f"| {header} | {meaning} |", "|---|---|", *rows])


def _render_language(policy: Policy, language: str, source: str) -> str:
    """The whole document of one language."""
    m = MESSAGES[language]
    bodies = {
        "overview": m.overview,
        "who-can-dispute": _who_can_dispute(policy, m),
        "filing-windows": _filing_windows(policy, m),
        "confirmation": _confirmation(policy, m),
        "human-review": _human_review(policy, m),
        "fraud-claims": m.fraud,
        "decision-codes": _decision_codes(m),
    }
    parts = [
        f'---\nlang: {language}\npolicy_version: "{policy.version}"\ngenerated: true\n---',
        f"# {m.title}",
        m.generated_note.format(source=source, version=policy.version),
    ]
    for section in SECTION_IDS:
        parts.append(f"## {m.section_titles[section]} {{#{section}}}\n\n{bodies[section]}")
    return "\n\n".join(parts) + "\n"


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def render_corpus(
    policy: Policy, *, source: str = "policy/dispute_policy_v1.yaml"
) -> dict[str, str]:
    """Render the corpus of every language.

    Parameters
    ----------
    policy : Policy
        The validated policy the text describes.
    source : str
        Path of the policy file, quoted in the note at the top of each document.

    Returns
    -------
    dict[str, str]
        Relative file path (``es/dispute-policy.md``) to the text of the document, in a fixed
        order.
    """
    return {
        f"{language}/{DOCUMENT_NAME}": _render_language(policy, language, source)
        for language in LANGUAGES
    }
