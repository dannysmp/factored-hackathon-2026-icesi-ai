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
- The dataset's product codes ("Cuenta Ahorro") are engine identifiers, not customer language: the
  text shows a display name per language ("Conta poupança"), and a code without a display name is
  shown as it is.
- Wording rules: alternatives use "or" (a charge is a payment or a purchase), exclusions use
  "nor", "reclamo" means a complaint only (a fraud report is a "reporte de fraude"), and the
  deadline is stated in calendar days with a worked example taken from the policy's shortest
  window.

Runtime Contract
----------------
``render_corpus(policy) -> dict[str, str]`` maps a relative file path (``es/dispute-policy.md``)
to its text. ``LANGUAGES`` and ``SECTION_IDS`` name what is rendered.

Limitations
-----------
The prose is fixed per language, not per country: Spanish uses a space as the thousands separator
and a comma as the decimal separator, the neutral form for Mexico, Colombia and Argentina. A chat
reply that quotes a number should format it for the customer's country at render time. A native
review of the final wording is still recommended before customer use. The text is synthetic policy
prose written for this project, not legal advice.
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

# Separates thousands and precedes the percent sign in Spanish, so a number does not split.
NO_BREAK_SPACE = chr(0xA0)


@dataclass(frozen=True, slots=True)
class Messages:
    """Every piece of prose of one language."""

    title: str
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
    product_names: dict[str, str]
    transaction_types_indefinite: dict[str, str]
    transaction_types_plural: dict[str, str]
    statuses: dict[TransactionStatus, str]
    statuses_plural: dict[TransactionStatus, str]
    reason_codes: dict[ReasonCode, str]
    and_word: str
    or_word: str
    nor_word: str


_ES = Messages(
    title="Política de disputas de transacciones",
    decimal_separator=",",
    thousands_separator=NO_BREAK_SPACE,
    percent_format="{value}" + NO_BREAK_SPACE + "%",
    section_titles={
        "overview": "Qué es esta política",
        "who-can-dispute": "Qué transacciones se pueden disputar",
        "filing-windows": "Plazos para presentar una disputa",
        "confirmation": "Confirmación antes de presentar",
        "human-review": "Cuándo lo revisa un asesor",
        "fraud-claims": "Reportes de fraude",
        "decision-codes": "Motivos de cada decisión",
    },
    overview=(
        "Esta política explica cómo se decide una solicitud de disputa sobre una transacción "
        "hecha con una cuenta o una tarjeta. Es una política sintética escrita para este "
        "proyecto: no es la de ningún banco ni regulador, y no constituye asesoría legal. Cada "
        "decisión se toma con reglas fijas y queda registrada junto con su motivo."
    ),
    products="Se pueden disputar transacciones de estos productos: {products}.",
    products_out_of_scope=(
        "Los demás productos ({products}) tienen sus propios canales de atención y no se "
        "gestionan con esta política."
    ),
    transactions=(
        "Para presentar una disputa, la transacción debe ser un cargo al cliente ({types}), "
        "estar {approved}, estar dentro del plazo de su categoría (ver más abajo) y no tener "
        "otra disputa abierta."
    ),
    types_excluded="No se pueden disputar {types}.",
    statuses_excluded=(
        "Tampoco se pueden disputar transacciones {declined}, {pending} o {reversed}."
    ),
    windows_intro=(
        "La disputa debe presentarse dentro de un plazo, contado en días calendario desde la "
        "fecha de la transacción. El último día del plazo todavía es válido: por ejemplo, con "
        "un plazo de {example} días, la disputa se puede presentar el día {example}, pero no "
        "el día {next}."
    ),
    window_line="- {category}: {days} días.",
    confirmation_all=(
        "Antes de presentar una disputa, el cliente confirma exactamente lo que se va a "
        "presentar: la transacción, el motivo y los datos de la solicitud."
    ),
    confirmation_some=(
        "Antes de presentar una disputa por {categories}, el cliente confirma exactamente lo "
        "que se va a presentar: la transacción, el motivo y los datos de la solicitud."
    ),
    confirmation_none="La política no exige confirmación previa a la presentación.",
    human_intro=(
        "Aunque la solicitud cumpla las reglas, pasa a revisión de un asesor en estos casos:"
    ),
    human_fraud="- Es un reporte de fraude.",
    human_confidence=(
        "- El sistema no entendió la solicitud con suficiente certeza (confianza inferior al "
        "{percent})."
    ),
    human_repeat="- El cliente ha presentado reclamos repetidos.",
    human_amount="- El monto es de {amount} USD o más.",
    human_unknown_amount="- No se conoce el monto en dólares.",
    human_risk=(
        "- El puntaje de riesgo de la transacción es {score} o más. El puntaje solo sirve para "
        "enviar el caso a revisión; nunca decide el resultado."
    ),
    fraud=(
        "Un asesor revisa siempre los reportes de fraude. Nunca se descartan automáticamente, "
        "aunque la transacción haya sido rechazada, esté fuera de plazo o corresponda a un "
        "producto fuera del alcance de esta política; en esos casos, el asesor recibe además "
        "el motivo por el que la solicitud no habría sido elegible."
    ),
    codes_intro="Cada decisión lleva uno de estos motivos.",
    codes_header=("Motivo", "Significado"),
    categories={
        DisputeCategory.UNRECOGNIZED_CHARGE: "cargo no reconocido",
        DisputeCategory.DUPLICATE_CHARGE: "cargo duplicado",
        DisputeCategory.WRONG_AMOUNT: "monto incorrecto",
        DisputeCategory.SERVICE_NOT_RECEIVED: "servicio no recibido",
        DisputeCategory.FRAUD_CLAIM: "reporte de fraude",
    },
    product_names={
        "Cuenta Ahorro": "Cuenta de ahorros",
        "Cuenta Corriente": "Cuenta corriente",
        "Tarjeta Crédito": "Tarjeta de crédito",
        "Tarjeta Débito": "Tarjeta de débito",
        "Préstamo Personal": "Préstamo personal",
        "Préstamo Hipotecario": "Crédito hipotecario",
        "Inversión": "Inversiones",
        "Seguro": "Seguros",
    },
    transaction_types_indefinite={
        "Purchase": "una compra",
        "Withdrawal": "un retiro",
        "Transfer": "una transferencia",
        "Payment": "un pago",
        "Deposit": "un depósito",
        "Adjustment": "un ajuste",
    },
    transaction_types_plural={
        "Purchase": "compras",
        "Withdrawal": "retiros",
        "Transfer": "transferencias",
        "Payment": "pagos",
        "Deposit": "depósitos",
        "Adjustment": "ajustes",
    },
    statuses={
        TransactionStatus.APPROVED: "aprobada",
        TransactionStatus.DECLINED: "rechazada",
        TransactionStatus.PENDING: "pendiente",
        TransactionStatus.REVERSED: "revertida",
    },
    statuses_plural={
        TransactionStatus.APPROVED: "aprobadas",
        TransactionStatus.DECLINED: "rechazadas",
        TransactionStatus.PENDING: "pendientes",
        TransactionStatus.REVERSED: "revertidas",
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
        ReasonCode.TRANSACTION_DATE_IN_FUTURE: "La fecha de la transacción es futura.",
        ReasonCode.FILING_WINDOW_EXPIRED: "Venció el plazo para presentar esta disputa.",
        ReasonCode.DUPLICATE_OPEN_CASE: "Ya hay una disputa abierta para esta transacción.",
        ReasonCode.ESCALATE_FRAUD_CLAIM: "Es un reporte de fraude; pasa a revisión de un asesor.",
        ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE: (
            "El sistema no entendió la solicitud con suficiente certeza; pasa a revisión de un "
            "asesor."
        ),
        ReasonCode.ESCALATE_REPEAT_COMPLAINER: (
            "El cliente tiene reclamos repetidos; pasa a revisión de un asesor."
        ),
        ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD: (
            "El monto alcanza el umbral de revisión; pasa a revisión de un asesor."
        ),
        ReasonCode.ESCALATE_AMOUNT_UNKNOWN: (
            "No se conoce el monto en dólares; pasa a revisión de un asesor."
        ),
        ReasonCode.ESCALATE_RISK_SCORE: (
            "El puntaje de riesgo alcanza el umbral; pasa a revisión de un asesor."
        ),
    },
    and_word="y",
    or_word="o",
    nor_word="ni",
)

_PT = Messages(
    title="Política de contestação de transações",
    decimal_separator=",",
    thousands_separator=".",
    percent_format="{value}%",
    section_titles={
        "overview": "O que é esta política",
        "who-can-dispute": "Quais transações podem ser contestadas",
        "filing-windows": "Prazos para apresentar uma contestação",
        "confirmation": "Confirmação antes de apresentar",
        "human-review": "Quando um atendente analisa",
        "fraud-claims": "Contestações por fraude",
        "decision-codes": "Motivos de cada decisão",
    },
    overview=(
        "Esta política explica como se decide um pedido de contestação de uma transação feita "
        "com uma conta ou um cartão. É uma política sintética escrita para este projeto: não é "
        "a de nenhum banco nem regulador, e não constitui orientação jurídica. Cada decisão é "
        "tomada com regras fixas e fica registrada com um motivo."
    ),
    products="Podem ser contestadas transações destes produtos: {products}.",
    products_out_of_scope=(
        "Os demais produtos ({products}) têm canais de atendimento próprios e não são "
        "tratados por esta política."
    ),
    transactions=(
        "Para apresentar uma contestação, a transação deve ser uma cobrança ao cliente "
        "({types}), estar {approved}, estar dentro do prazo da sua categoria (veja abaixo) e "
        "não ter outra contestação em aberto."
    ),
    types_excluded="Não podem ser contestados {types}.",
    statuses_excluded=(
        "Também não podem ser contestadas transações {declined}, {pending} ou {reversed}."
    ),
    windows_intro=(
        "A contestação deve ser apresentada dentro de um prazo, contado em dias corridos a "
        "partir da data da transação. O último dia do prazo ainda é válido: por exemplo, com "
        "um prazo de {example} dias, a contestação pode ser apresentada no {example}º dia, "
        "mas não no {next}º."
    ),
    window_line="- {category}: {days} dias.",
    confirmation_all=(
        "Antes de apresentar uma contestação, o cliente confirma exatamente o que será "
        "apresentado: a transação, o motivo e os dados do pedido."
    ),
    confirmation_some=(
        "Antes de apresentar uma contestação por {categories}, o cliente confirma exatamente "
        "o que será apresentado: a transação, o motivo e os dados do pedido."
    ),
    confirmation_none="A política não exige confirmação antes da apresentação.",
    human_intro=(
        "Mesmo que o pedido cumpra as regras, ele é encaminhado para análise de um atendente "
        "nestes casos:"
    ),
    human_fraud="- É uma contestação por fraude.",
    human_confidence=(
        "- O sistema não entendeu o pedido com segurança suficiente (confiança abaixo de "
        "{percent})."
    ),
    human_repeat="- O cliente apresentou reclamações repetidas.",
    human_amount="- O valor é de {amount} USD ou mais.",
    human_unknown_amount="- O valor em dólares não é conhecido.",
    human_risk=(
        "- A pontuação de risco da transação é {score} ou mais. A pontuação serve apenas para "
        "encaminhar o caso para análise; nunca decide o resultado."
    ),
    fraud=(
        "Toda contestação por fraude é analisada por um atendente. Ela nunca é descartada "
        "automaticamente, mesmo que a transação tenha sido recusada, esteja fora do prazo ou "
        "seja de um produto fora do escopo desta política; nesses casos, o atendente recebe "
        "também o motivo pelo qual o pedido não seria elegível."
    ),
    codes_intro="Cada decisão traz um destes motivos.",
    codes_header=("Motivo", "Significado"),
    categories={
        DisputeCategory.UNRECOGNIZED_CHARGE: "cobrança não reconhecida",
        DisputeCategory.DUPLICATE_CHARGE: "cobrança em duplicidade",
        DisputeCategory.WRONG_AMOUNT: "valor incorreto",
        DisputeCategory.SERVICE_NOT_RECEIVED: "serviço não recebido",
        DisputeCategory.FRAUD_CLAIM: "contestação por fraude",
    },
    product_names={
        "Cuenta Ahorro": "Conta poupança",
        "Cuenta Corriente": "Conta corrente",
        "Tarjeta Crédito": "Cartão de crédito",
        "Tarjeta Débito": "Cartão de débito",
        "Préstamo Personal": "Empréstimo pessoal",
        "Préstamo Hipotecario": "Financiamento imobiliário",
        "Inversión": "Investimentos",
        "Seguro": "Seguros",
    },
    transaction_types_indefinite={
        "Purchase": "uma compra",
        "Withdrawal": "um saque",
        "Transfer": "uma transferência",
        "Payment": "um pagamento",
        "Deposit": "um depósito",
        "Adjustment": "um ajuste",
    },
    transaction_types_plural={
        "Purchase": "compras",
        "Withdrawal": "saques",
        "Transfer": "transferências",
        "Payment": "pagamentos",
        "Deposit": "depósitos",
        "Adjustment": "ajustes",
    },
    statuses={
        TransactionStatus.APPROVED: "aprovada",
        TransactionStatus.DECLINED: "recusada",
        TransactionStatus.PENDING: "pendente",
        TransactionStatus.REVERSED: "estornada",
    },
    statuses_plural={
        TransactionStatus.APPROVED: "aprovadas",
        TransactionStatus.DECLINED: "recusadas",
        TransactionStatus.PENDING: "pendentes",
        TransactionStatus.REVERSED: "estornadas",
    },
    reason_codes={
        ReasonCode.ELIGIBLE: "A contestação pode ser apresentada, mediante confirmação.",
        ReasonCode.PRODUCT_OUT_OF_SCOPE: "O produto não faz parte do escopo desta política.",
        ReasonCode.TRANSACTION_TYPE_NOT_DISPUTABLE: (
            "O tipo de transação não é uma cobrança que possa ser contestada."
        ),
        ReasonCode.TRANSACTION_DECLINED: "A transação foi recusada: não houve cobrança.",
        ReasonCode.TRANSACTION_PENDING: "A transação ainda está pendente.",
        ReasonCode.TRANSACTION_REVERSED: "A transação já foi estornada.",
        ReasonCode.TRANSACTION_DATE_IN_FUTURE: "A data da transação está no futuro.",
        ReasonCode.FILING_WINDOW_EXPIRED: "O prazo para apresentar esta contestação expirou.",
        ReasonCode.DUPLICATE_OPEN_CASE: "Já existe uma contestação aberta para esta transação.",
        ReasonCode.ESCALATE_FRAUD_CLAIM: (
            "É uma contestação por fraude; é encaminhada para análise de um atendente."
        ),
        ReasonCode.ESCALATE_LOW_NLU_CONFIDENCE: (
            "O sistema não entendeu o pedido com segurança suficiente; é encaminhado para "
            "análise de um atendente."
        ),
        ReasonCode.ESCALATE_REPEAT_COMPLAINER: (
            "O cliente tem reclamações repetidas; é encaminhado para análise de um atendente."
        ),
        ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD: (
            "O valor atinge o valor de referência para análise; é encaminhado para análise de "
            "um atendente."
        ),
        ReasonCode.ESCALATE_AMOUNT_UNKNOWN: (
            "O valor em dólares não é conhecido; é encaminhado para análise de um atendente."
        ),
        ReasonCode.ESCALATE_RISK_SCORE: (
            "A pontuação de risco atinge o valor de referência; é encaminhado para análise de "
            "um atendente."
        ),
    },
    and_word="e",
    or_word="ou",
    nor_word="nem",
)

_EN = Messages(
    title="Transaction dispute policy",
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
        "This policy explains how a request to dispute a transaction made with an account or a "
        "card is decided. It is a synthetic policy written for this project: it is not any "
        "bank's or regulator's policy, and it is not legal advice. Every decision is made with "
        "fixed rules and recorded with a reason."
    ),
    products="Transactions on these products can be disputed: {products}.",
    products_out_of_scope=(
        "The other products ({products}) have their own claim processes and are not handled "
        "by this policy."
    ),
    transactions=(
        "To file a dispute, the transaction must be a charge to the customer ({types}), be "
        "{approved}, be within the deadline of its category (see below) and have no other "
        "open dispute."
    ),
    types_excluded="Transactions that are {types} cannot be disputed.",
    statuses_excluded=(
        "Transactions that are {declined}, {pending} or {reversed} cannot be disputed either."
    ),
    windows_intro=(
        "A dispute must be filed within a deadline, counted in calendar days from the "
        "transaction date. The last day of the deadline is still valid: for example, with a "
        "deadline of {example} days, the dispute can be filed on day {example} but not on day "
        "{next}."
    ),
    window_line="- {category}: {days} days.",
    confirmation_all=(
        "Before a dispute is filed, the customer confirms exactly what is going to be filed: "
        "the transaction, the reason and the details of the request."
    ),
    confirmation_some=(
        "Before a dispute for {categories} is filed, the customer confirms exactly what is "
        "going to be filed: the transaction, the reason and the details of the request."
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
    product_names={
        "Cuenta Ahorro": "Savings account",
        "Cuenta Corriente": "Checking account",
        "Tarjeta Crédito": "Credit card",
        "Tarjeta Débito": "Debit card",
        "Préstamo Personal": "Personal loan",
        "Préstamo Hipotecario": "Mortgage",
        "Inversión": "Investments",
        "Seguro": "Insurance",
    },
    transaction_types_indefinite={
        "Purchase": "a purchase",
        "Withdrawal": "a withdrawal",
        "Transfer": "a transfer",
        "Payment": "a payment",
        "Deposit": "a deposit",
        "Adjustment": "an adjustment",
    },
    transaction_types_plural={
        "Purchase": "purchases",
        "Withdrawal": "withdrawals",
        "Transfer": "transfers",
        "Payment": "payments",
        "Deposit": "deposits",
        "Adjustment": "adjustments",
    },
    statuses={
        TransactionStatus.APPROVED: "approved",
        TransactionStatus.DECLINED: "declined",
        TransactionStatus.PENDING: "pending",
        TransactionStatus.REVERSED: "reversed",
    },
    statuses_plural={
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
        ReasonCode.TRANSACTION_DATE_IN_FUTURE: "The transaction date is in the future.",
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
    or_word="or",
    nor_word="or",
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


# Codes the source uses for products and transaction types, in the order the text lists them. The
# text names as excluded whichever of these the policy does not accept, so an exclusion can only
# be stated when the policy makes it.
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


def _in_reading_order(accepted: frozenset[str], known: tuple[str, ...]) -> list[str]:
    """The accepted codes, known ones first in their fixed order, then any others by name."""
    return [code for code in known if code in accepted] + sorted(accepted - set(known))


def _who_can_dispute(policy: Policy, m: Messages) -> str:
    """The conditions a transaction must meet, and what the policy leaves out."""
    accepted_products = _in_reading_order(policy.in_scope_product_types, KNOWN_PRODUCT_TYPES)
    other_products = [p for p in KNOWN_PRODUCT_TYPES if p not in policy.in_scope_product_types]
    accepted_types = _in_reading_order(policy.disputable_transaction_types, KNOWN_TRANSACTION_TYPES)
    other_types = [
        t for t in KNOWN_TRANSACTION_TYPES if t not in policy.disputable_transaction_types
    ]

    def names(codes: list[str]) -> str:
        return _join([_label(m.product_names, code) for code in codes], m.and_word)

    charges = _join([_label(m.transaction_types_indefinite, t) for t in accepted_types], m.or_word)
    excluded_types = _join([_label(m.transaction_types_plural, t) for t in other_types], m.nor_word)
    paragraphs = [m.products.format(products=names(accepted_products))]
    if other_products:
        paragraphs.append(m.products_out_of_scope.format(products=names(other_products)))
    paragraphs.append(
        m.transactions.format(types=charges, approved=m.statuses[TransactionStatus.APPROVED])
    )
    if other_types:
        paragraphs.append(m.types_excluded.format(types=excluded_types))
    paragraphs.append(
        m.statuses_excluded.format(
            declined=m.statuses_plural[TransactionStatus.DECLINED],
            pending=m.statuses_plural[TransactionStatus.PENDING],
            reversed=m.statuses_plural[TransactionStatus.REVERSED],
        )
    )
    return "\n\n".join(paragraphs)


def _filing_windows(policy: Policy, m: Messages) -> str:
    """The deadline rule with a worked example, then one line per category with its window."""
    windows = [policy.categories[category].filing_window_days for category in DisputeCategory]
    example = min(windows)
    intro = m.windows_intro.format(example=example, next=example + 1)
    lines = [
        m.window_line.format(
            category=_capitalise(m.categories[category]),
            days=policy.categories[category].filing_window_days,
        )
        for category in DisputeCategory
    ]
    return intro + "\n\n" + "\n".join(lines)


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
    # Generation details live in the front matter, outside the sections retrieval searches
    front_matter = (
        f'---\nlang: {language}\npolicy_version: "{policy.version}"\n'
        f'generated: true\ngenerated_from: "{source}"\n---'
    )
    parts = [front_matter, f"# {m.title}"]
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
        Path of the policy file, recorded in the front matter of each document.

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
