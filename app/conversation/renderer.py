"""
Template Renderer
=================

Overview
--------
Turns a ``RenderEnvelope`` into the reply text, the persistent reference-date line and, for a
demonstration session, its notice — the fixed-wording template renderer. Every number, date and
name a template states is interpolated directly from ``facts``, ``decisions`` or ``sources`` by
this module's own code: there is no free-form generation here for a value to be invented from, so
grounding is a property of the code, not something checked at render time.

Scope
-----
In: the fixed-wording texts per language, the date and amount formatting rules, ``render()``.
Out: the model renderer (``app.conversation.model_renderer``), the choice between the two paths
(``app.conversation.reply``) and the grounding check on model output
(``app.conversation.verifier``); the dialogue controller, which decides which template applies.

Design Principles
-----------------
- Every ``TemplateId`` has exactly one renderer function, keyed by a table checked for
  completeness against the enumeration, so an addition to the contract fails here until this
  module is updated too.
- Dates are always absolute and carry the year, in words, in the reply language; the reference-date
  line is computed once and returned alongside the reply, never inside it, so every turn can show
  it regardless of intent.
- Money is written with the reply language's separators, computed by this module's own
  ``format_money``/``format_date``, never composed from a value the caller passed as text.
- One to four short sentences per reply, one question at a time, matching the product's shared
  conversation rules.

Runtime Contract
----------------
``render(envelope) -> RenderedReply``. ``reference_date_line(domain_date, lang)``.
``demo_notice(lang)``. ``format_money``/``format_date``/``amount_text``/``CATEGORY_NAMES``/
``INELIGIBLE_TEXT`` are exported so the model-rendered path's own slot values
(``app.conversation.slot_values``) format a figure identically to the template path, rather than
a second, independently maintained copy.

Limitations
-----------
The wording is fixed text per language, and a test renders every template in all three
languages. Combining an ambiguous first message's best guess with the offer to switch languages
in one reply is the dialogue controller's job; this module renders the offer as its own text.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of one template's renderer function
from dataclasses import dataclass  # Immutable result of one render
from datetime import date  # Absolute dates, always formatted in words
from typing import Literal  # Which path produced a reply

# Local modules
from app.domain.policy.models import DisputeCategory, Outcome  # Shared vocabulary
from contracts.service_v1.envelope import (  # The envelope and its typed facts
    CustomerReason,
    Lang,
    Money,
    RenderEnvelope,
    TemplateId,
)

# -----------------------------------------------------------------------------
# Dates and amounts
# -----------------------------------------------------------------------------

_MONTHS: dict[Lang, tuple[str, ...]] = {
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    "pt": (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}

# Number formatting conventions per language: Spanish groups with a no-break space, Portuguese
# with a period and English with a comma; the decimal mark is the comma or period that remains.
_THOUSANDS_SEPARATOR: dict[Lang, str] = {"es": chr(0xA0), "pt": ".", "en": ","}
_DECIMAL_SEPARATOR: dict[Lang, str] = {"es": ",", "pt": ",", "en": "."}


def format_date(value: date, lang: Lang) -> str:
    """``value`` written in words in ``lang``, absolute and carrying the year."""
    month = _MONTHS[lang][value.month - 1]
    if lang == "en":
        return f"{month} {value.day}, {value.year}"
    return f"{value.day} de {month} de {value.year}"


def _format_amount(money: Money, lang: Lang) -> str:
    """The digits and separators of ``money``, in ``lang``'s convention, with no currency code."""
    whole, _, fraction = format(money.amount, "f").partition(".")
    fraction = (fraction or "00").ljust(2, "0")[:2]
    grouped = f"{int(whole):,}".replace(",", _THOUSANDS_SEPARATOR[lang])
    return f"{grouped}{_DECIMAL_SEPARATOR[lang]}{fraction}"


def format_money(money: Money, lang: Lang) -> str:
    """An amount with its currency code, in the reply language's separators."""
    return f"{_format_amount(money, lang)} {money.currency}"


def reference_date_line(domain_date: date, lang: Lang) -> str:
    """The persistent line every reply carries: the data's reference date, in the reply language."""
    prefix = {
        "es": "Fecha de referencia de los datos",
        "pt": "Data de referência dos dados",
        "en": "Reference date of the data",
    }[lang]
    return f"{prefix}: {format_date(domain_date, lang)}"


def demo_notice(lang: Lang) -> str:
    """The notice a demonstration session shows, stating the data is synthetic, in ``lang``."""
    return {
        "es": "Esta es una sesión de demostración con datos sintéticos.",
        "pt": "Esta é uma sessão de demonstração com dados sintéticos.",
        "en": "This is a demonstration session with synthetic data.",
    }[lang]


# Synthetic placeholders for the channels the system redirects to; a real deployment replaces them
# with the bank's actual destinations.
_APP_CHANNEL: dict[Lang, str] = {
    "es": "la sección de tarjetas de la aplicación del banco",
    "pt": "a seção de cartões do aplicativo do banco",
    "en": "the card section of the banking app",
}
_URGENT_CHANNEL: dict[Lang, str] = {
    "es": "la línea de emergencias del banco",
    "pt": "a linha de emergência do banco",
    "en": "the bank's emergency line",
}

# -----------------------------------------------------------------------------
# Category and reason wording
# -----------------------------------------------------------------------------

CATEGORY_NAMES: dict[Lang, dict[DisputeCategory, str]] = {
    "es": {
        DisputeCategory.UNRECOGNIZED_CHARGE: "cargo no reconocido",
        DisputeCategory.DUPLICATE_CHARGE: "cargo duplicado",
        DisputeCategory.WRONG_AMOUNT: "monto incorrecto",
        DisputeCategory.SERVICE_NOT_RECEIVED: "servicio no recibido",
        DisputeCategory.FRAUD_CLAIM: "reporte de fraude",
    },
    "pt": {
        DisputeCategory.UNRECOGNIZED_CHARGE: "cobrança não reconhecida",
        DisputeCategory.DUPLICATE_CHARGE: "cobrança em duplicidade",
        DisputeCategory.WRONG_AMOUNT: "valor incorreto",
        DisputeCategory.SERVICE_NOT_RECEIVED: "serviço não recebido",
        DisputeCategory.FRAUD_CLAIM: "contestação por fraude",
    },
    "en": {
        DisputeCategory.UNRECOGNIZED_CHARGE: "unrecognized charge",
        DisputeCategory.DUPLICATE_CHARGE: "duplicate charge",
        DisputeCategory.WRONG_AMOUNT: "wrong amount",
        DisputeCategory.SERVICE_NOT_RECEIVED: "service not received",
        DisputeCategory.FRAUD_CLAIM: "fraud claim",
    },
}

# What a reply states in place of a figure it does not have, rather than inventing one.
_AMOUNT_UNKNOWN: dict[Lang, str] = {
    "es": "un monto no disponible",
    "pt": "um valor não disponível",
    "en": "an amount that isn't available",
}


def amount_text(amount: Money | None, lang: Lang) -> str:
    """``amount`` formatted in ``lang``, or the fixed phrase for one the source never gave."""
    return format_money(amount, lang) if amount is not None else _AMOUNT_UNKNOWN[lang]


# The plain wording for each ineligible reason; ELIGIBLE and NEEDS_REVIEW are handled elsewhere.
INELIGIBLE_TEXT: dict[Lang, dict[CustomerReason, str]] = {
    "es": {
        CustomerReason.WINDOW_EXPIRED: "El plazo para presentar esta disputa ya venció.",
        CustomerReason.PENDING: "La transacción todavía está pendiente; podrá disputarla "
        "cuando se confirme.",
        CustomerReason.DECLINED: "Esa transacción fue rechazada, así que no hubo ningún cargo "
        "que disputar.",
        CustomerReason.REVERSED: "Esa transacción ya fue revertida.",
        CustomerReason.DUPLICATE_CASE: "Ya existe una disputa abierta para esta transacción.",
        CustomerReason.NOT_DISPUTABLE: "Ese producto o tipo de transacción no se puede "
        "disputar con esta política.",
    },
    "pt": {
        CustomerReason.WINDOW_EXPIRED: "O prazo para apresentar esta contestação já expirou.",
        CustomerReason.PENDING: "A transação ainda está pendente; você poderá contestá-la "
        "quando for confirmada.",
        CustomerReason.DECLINED: "Essa transação foi recusada, então não houve cobrança "
        "para contestar.",
        CustomerReason.REVERSED: "Essa transação já foi estornada.",
        CustomerReason.DUPLICATE_CASE: "Já existe uma contestação em aberto para esta transação.",
        CustomerReason.NOT_DISPUTABLE: "Esse produto ou tipo de transação não pode ser "
        "contestado de acordo com esta política.",
    },
    "en": {
        CustomerReason.WINDOW_EXPIRED: "The deadline to file this dispute has already passed.",
        CustomerReason.PENDING: "That transaction is still pending; you can dispute it once "
        "it is confirmed.",
        CustomerReason.DECLINED: "That transaction was declined, so there was no charge to "
        "dispute.",
        CustomerReason.REVERSED: "That transaction has already been reversed.",
        CustomerReason.DUPLICATE_CASE: "A dispute is already open for this transaction.",
        CustomerReason.NOT_DISPUTABLE: "That product or transaction type cannot be disputed "
        "under this policy.",
    },
}


@dataclass(frozen=True, slots=True)
class RenderedReply:
    """What one turn's reply came from, alongside its text.

    ``render_mode`` names which path actually produced ``reply`` — ``render()`` itself always
    returns ``"template"``; ``app.conversation.reply.render_reply`` sets ``"model"`` only when a
    model-rendered candidate was accepted, and falls back to ``"template"`` otherwise. This is the
    console's own record of how a reply was produced, not a grounding check — that already
    happened before this value exists.
    """

    reply: str
    reference_date_line: str
    render_mode: Literal["template", "model"] = "template"


# -----------------------------------------------------------------------------
# One function per template
# -----------------------------------------------------------------------------


def _greeting(e: RenderEnvelope) -> str:
    """The greeting that offers disputing a charge, checking a dispute or explaining the policy."""
    return {
        "es": "Hola, puedo ayudarle a disputar un cargo, consultar una disputa o explicarle la "
        "política. Cuénteme qué transacción quiere revisar.",
        "pt": "Olá, posso ajudar a contestar uma cobrança, consultar uma contestação ou "
        "explicar a política. Diga qual transação você quer revisar.",
        "en": "Hello, I can help you dispute a charge, check a dispute, or explain the policy. "
        "Tell me which transaction you'd like to look at.",
    }[e.lang]


def _clarify_transaction(e: RenderEnvelope) -> str:
    """Ask for the merchant, amount or date that identifies the charge."""
    return {
        "es": "¿Podría decirme el comercio, el monto o la fecha del cargo que quiere disputar?",
        "pt": "Você poderia me dizer o estabelecimento, o valor ou a data da cobrança que quer "
        "contestar?",
        "en": "Could you tell me the merchant, the amount or the date of the charge you want "
        "to dispute?",
    }[e.lang]


def _clarify_reason(e: RenderEnvelope) -> str:
    """Ask for the dispute reason and list the categories the customer can choose from."""
    return {
        "es": "¿Cuál es el motivo? Puede ser un cargo no reconocido, un cargo duplicado, un "
        "monto incorrecto, un servicio no recibido o un fraude.",
        "pt": "Qual é o motivo? Pode ser uma cobrança não reconhecida, uma cobrança em "
        "duplicidade, um valor incorreto, um serviço não recebido ou uma fraude.",
        "en": "What's the reason? It can be an unrecognized charge, a duplicate charge, a "
        "wrong amount, a service not received, or fraud.",
    }[e.lang]


def _clarify_choice(e: RenderEnvelope) -> str:
    """Ask which of several presented options is the right one."""
    return {
        "es": "Encontré varias coincidencias. ¿Cuál de las opciones es la correcta?",
        "pt": "Encontrei mais de uma correspondência. Qual das opções é a correta?",
        "en": "I found more than one match. Which of the options is the right one?",
    }[e.lang]


def _clarify_confirmation(e: RenderEnvelope) -> str:
    """Ask for a yes or no to filing the dispute."""
    return {
        "es": "¿Confirma que desea presentar la disputa? Responda sí o no.",
        "pt": "Você confirma que quer apresentar a contestação? Responda sim ou não.",
        "en": "Do you confirm you want to file the dispute? Please answer yes or no.",
    }[e.lang]


def _language_offer(e: RenderEnvelope) -> str:
    """The trilingual offer shown when the first message gives no usable language signal.

    The Spanish and Portuguese texts say the conversation continues in Spanish and offer the other
    languages, each written in all three; the English text is a plain one-line greeting.
    """
    return {
        "es": "Hola, puedo ayudarle con su disputa. No sé si prefiere continuar "
        "en español o portugués; continuaré en español, avíseme si prefiere otro idioma. / "
        "Olá, posso ajudar com sua contestação. Vou continuar em espanhol; me avise se "
        "preferir português. / Hello, I can help with your dispute. I will continue in Spanish; "
        "let me know if you prefer English.",
        "pt": "Olá, posso ajudar com sua contestação. Não tenho certeza se você prefere "
        "continuar em português ou em espanhol; vou continuar em espanhol, mas me avise se "
        "preferir outro idioma. / Hola, puedo ayudarle con su disputa. Continuaré en español; "
        "avíseme si prefiere portugués. / Hello, I can help with your dispute. I will continue in "
        "Spanish; let me know if you prefer English.",
        "en": "Hello, I can help with your dispute.",
    }[e.lang]


# The preposition that introduces a merchant's name in each language ("en", "em", "at").
_MERCHANT_PREPOSITION: dict[Lang, str] = {"es": "en", "pt": "em", "en": "at"}


def _present_one(e: RenderEnvelope) -> str:
    """Present the single transaction found (amount, merchant, date) and ask if it is the one."""
    transaction = e.facts.transactions[0]
    merchant = (
        f" {_MERCHANT_PREPOSITION[e.lang]} {transaction.merchant}" if transaction.merchant else ""
    )
    amount = amount_text(transaction.amount, e.lang)
    parts = {
        "es": (
            f"Encontré una transacción de {amount}{merchant} "
            f"el {format_date(transaction.occurred_on, e.lang)}. ¿Es esta la que quiere "
            "disputar?"
        ),
        "pt": (
            f"Encontrei uma transação de {amount}{merchant} "
            f"no dia {format_date(transaction.occurred_on, e.lang)}. É esta que você quer "
            "contestar?"
        ),
        "en": (
            f"I found a transaction of {amount}{merchant} on "
            f"{format_date(transaction.occurred_on, e.lang)}. Is this the one you want to "
            "dispute?"
        ),
    }
    return parts[e.lang]


def _present_list(e: RenderEnvelope) -> str:
    """Introduce a list of transactions that might match and ask which one the customer means."""
    return {
        "es": "Encontré varias transacciones que podrían coincidir. Elija el número de la que "
        "quiere disputar.",
        "pt": "Encontrei várias transações que podem coincidir. Escolha o número da que você "
        "quer contestar.",
        "en": "I found several transactions that might match. Choose the number of the one "
        "you want to dispute.",
    }[e.lang]


def _present_narrow(e: RenderEnvelope) -> str:
    """Say there are too many matches to list and ask for more detail."""
    return {
        "es": "Encontré demasiadas coincidencias para mostrarlas. ¿Podría darme más detalles, "
        "como el comercio o la fecha exacta?",
        "pt": "Encontrei correspondências demais para mostrar. Você poderia me dar mais "
        "detalhes, como o estabelecimento ou a data exata?",
        "en": "I found too many matches to list. Could you give me more detail, like the "
        "merchant or the exact date?",
    }[e.lang]


def _not_found(e: RenderEnvelope) -> str:
    """Say no transaction matched and offer a wider date range or a person."""
    return {
        "es": "No encontré ninguna transacción con esos datos. Dígame el comercio, el monto "
        "exacto o la fecha tal como aparecen en su extracto, o pídame hablar con un asesor.",
        "pt": "Não encontrei nenhuma transação com esses dados. Informe o estabelecimento, o "
        "valor exato ou a data como aparecem no seu extrato, ou peça para falar com um atendente.",
        "en": "I couldn't find a transaction matching that. Tell me the merchant, the exact "
        "amount or the date as it appears on your statement, or ask to speak with a person.",
    }[e.lang]


def _english_article(noun_phrase: str) -> str:
    """``"a"``/``"an"`` for an English noun phrase, by its first letter's sound."""
    return "an" if noun_phrase[:1].lower() in "aeiou" else "a"


def _confirm_filing(e: RenderEnvelope) -> str:
    """State the dispute about to be filed and ask for the customer's confirmation.

    Names the category, the amount and the date of the selected transaction, all from ``facts``, and
    says a person reviews the case and no outcome is guaranteed.
    """
    facts = e.facts
    transaction = next(t for t in facts.transactions if t.ref == facts.selected_ref)
    category = CATEGORY_NAMES[e.lang][facts.category] if facts.category else ""
    amount = amount_text(transaction.amount, e.lang)
    parts = {
        "es": (
            f"Voy a presentar una disputa por {category} sobre el cargo de "
            f"{amount} del "
            f"{format_date(transaction.occurred_on, e.lang)}. Un asesor la revisará; esto no "
            "garantiza un resultado. ¿Confirma que desea presentarla?"
        ),
        "pt": (
            f"Vou apresentar uma contestação por {category} sobre a cobrança de "
            f"{amount} do dia "
            f"{format_date(transaction.occurred_on, e.lang)}. Um atendente vai analisar o pedido; "
            "isso não garante um resultado. Você confirma que quer apresentá-la?"
        ),
        "en": (
            f"I'll file {_english_article(category)} {category} dispute for the charge of "
            f"{amount} on "
            f"{format_date(transaction.occurred_on, e.lang)}. A person will review it; this "
            "does not guarantee an outcome. Do you confirm you want to file it?"
        ),
    }
    return parts[e.lang]


def _filing_result(e: RenderEnvelope) -> str:
    """State the filed case number and, when known, the date a first response is expected."""
    case = e.facts.cases[0]
    expected = case.expected_response_on
    when = (
        {
            "es": f" Espere una primera respuesta antes del {format_date(expected, e.lang)}.",
            "pt": f" Você deve receber uma primeira resposta até {format_date(expected, e.lang)}.",
            "en": f" Expect a first response by {format_date(expected, e.lang)}.",
        }[e.lang]
        if expected is not None
        else ""
    )
    parts = {
        "es": f"Su disputa quedó registrada con el número de caso {case.case_number}.{when}",
        "pt": f"Sua contestação foi registrada com o número do caso {case.case_number}.{when}",
        "en": f"Your dispute was filed with case number {case.case_number}.{when}",
    }
    return parts[e.lang]


def _filing_unverified(e: RenderEnvelope) -> str:
    """Say the filing could not be confirmed and that a person will verify it."""
    return {
        "es": "No pude confirmar que la disputa quedó registrada correctamente. La estoy "
        "pasando a un asesor para que lo verifique.",
        "pt": "Não consegui confirmar que a contestação foi registrada corretamente. Estou "
        "encaminhando o caso para um atendente verificar.",
        "en": "I couldn't confirm the dispute was filed correctly. I'm passing this to a "
        "person to verify it.",
    }[e.lang]


def _filing_cancelled(e: RenderEnvelope) -> str:
    """Acknowledge that the customer declined and nothing was filed."""
    return {
        "es": "De acuerdo, no presenté la disputa.",
        "pt": "Tudo bem, não apresentei a contestação.",
        "en": "Understood, I didn't file the dispute.",
    }[e.lang]


def _ineligible(e: RenderEnvelope) -> str:
    """State the plain-language reason the ineligible decision gives."""
    decision = next(d for d in e.decisions if d.outcome is Outcome.INELIGIBLE)
    return INELIGIBLE_TEXT[e.lang][decision.customer_reason]


def _dispute_status(e: RenderEnvelope) -> str:
    """List the customer's recent cases with their status and filing date, one per line."""
    lines = [
        {
            "es": f"Caso {case.case_number}: {case.status}, presentado el "
            f"{format_date(case.filed_on, e.lang)}.",
            "pt": f"Caso {case.case_number}: {case.status}, apresentado em "
            f"{format_date(case.filed_on, e.lang)}.",
            "en": f"Case {case.case_number}: {case.status}, filed on "
            f"{format_date(case.filed_on, e.lang)}.",
        }[e.lang]
        for case in e.facts.cases
    ]
    intro = {
        "es": "Estos son sus casos recientes:",
        "pt": "Estes são seus casos recentes:",
        "en": "Here are your recent cases:",
    }[e.lang]
    return "\n".join([intro, *lines])


def _no_case_found(e: RenderEnvelope) -> str:
    """Say no case was found on the customer's account."""
    return {
        "es": "No encontré ningún caso con esos datos en su cuenta.",
        "pt": "Não encontrei nenhum caso com esses dados na sua conta.",
        "en": "I couldn't find a case matching that on your account.",
    }[e.lang]


def _policy_answer(e: RenderEnvelope) -> str:
    """Quote the retrieved policy section by title together with the values it states."""
    title = e.sources[0].title_for(e.lang)
    if not e.facts.policy_values:
        return {
            "es": f"Puede consultarlo en la sección “{title}” de nuestra política de disputas.",
            "pt": f"Você pode consultar isso na seção “{title}” da nossa política de disputas.",
            "en": f"You can find this in the “{title}” section of our dispute policy.",
        }[e.lang]
    values = ", ".join(f"{v.name}: {v.value}" for v in e.facts.policy_values)
    parts = {
        "es": f"Según la sección “{title}”: {values}.",
        "pt": f"De acordo com a seção “{title}”: {values}.",
        "en": f"According to the “{title}” section: {values}.",
    }
    return parts[e.lang]


def _abstain_policy(e: RenderEnvelope) -> str:
    """Say the information is not available, give no figure, and offer a person."""
    return {
        "es": "No tengo esa información y no puedo darle una cifra al respecto. Puedo "
        "pasarla con un asesor.",
        "pt": "Não tenho essa informação e não posso informar um valor sobre isso. Posso "
        "encaminhar você para um atendente.",
        "en": "I don't have that information and can't give you a figure on it. I can connect "
        "you with a person.",
    }[e.lang]


def _refuse_unsupported(e: RenderEnvelope) -> str:
    """Refuse an unsupported action and point to the bank's own channel or a person."""
    channel = _APP_CHANNEL[e.lang]
    return {
        "es": f"Eso no lo puedo hacer aquí. Puedo ayudarle a disputar una transacción, "
        f"consultar un caso o explicarle la política; para eso use {channel}, o la paso "
        "con un asesor.",
        "pt": f"Isso eu não posso fazer por aqui. Posso ajudar a contestar uma transação, "
        f"consultar um caso ou explicar a política. Para isso, use {channel}, ou posso "
        "encaminhar você para um atendente.",
        "en": f"I can't do that here. I can help you dispute a transaction, check a case, or "
        f"explain the policy; for that, use {channel}, or I can connect you with a person.",
    }[e.lang]


def _refuse_reversal(e: RenderEnvelope) -> str:
    """Explain that the bank decides the dispute; no refund or date is promised."""
    return {
        "es": "Yo presento la disputa y el banco decide; no puedo garantizar un reembolso ni "
        "una fecha. ¿Quiere que la presente si es elegible?",
        "pt": "Eu apresento a contestação e o banco decide; não posso garantir um reembolso "
        "nem uma data. Quer que eu apresente a contestação, se for elegível?",
        "en": "I file the dispute and the bank decides; I can't promise a refund or a date. "
        "Would you like me to file it if it's eligible?",
    }[e.lang]


def _handoff_review(e: RenderEnvelope) -> str:
    """Tell the customer a person must review the request and give the ticket reference.

    The contact-within-hours sentence is included only when ``facts`` carries that figure.
    """
    hours = e.facts.contact_within_hours
    ticket = e.facts.ticket_ref
    contact = (
        {
            "es": f" Le contactarán en un plazo de {hours} horas.",
            "pt": f" Um atendente entrará em contato com você em até {hours} horas.",
            "en": f" You'll be contacted within {hours} hours.",
        }[e.lang]
        if hours is not None
        else ""
    )
    return {
        "es": f"Un asesor debe revisar esto.{contact} Su referencia es {ticket}.",
        "pt": f"Um atendente precisa analisar isso.{contact} Sua referência é {ticket}.",
        "en": f"A person must review this.{contact} Your reference is {ticket}.",
    }[e.lang]


def _handoff_fraud(e: RenderEnvelope) -> str:
    """Tell the customer a person handles a possible fraud right away, with no promised outcome.

    States the ticket reference, and the contact-within-hours sentence only when ``facts`` carries
    it.
    """
    hours = e.facts.contact_within_hours
    ticket = e.facts.ticket_ref
    contact = (
        {
            "es": f" Le contactarán en un plazo de {hours} horas.",
            "pt": f" Um atendente entrará em contato com você em até {hours} horas.",
            "en": f" You'll be contacted within {hours} hours.",
        }[e.lang]
        if hours is not None
        else ""
    )
    return {
        "es": f"Esto lo atiende un asesor de inmediato por ser un posible fraude. No "
        f"prometo un resultado.{contact} Su referencia es {ticket}.",
        "pt": f"Como pode se tratar de fraude, um atendente cuida disso de imediato. "
        f"Não posso prometer um resultado.{contact} Sua referência é {ticket}.",
        "en": f"A person handles this right away since it may be fraud. I can't promise an "
        f"outcome.{contact} Your reference is {ticket}.",
    }[e.lang]


def _handoff_card_loss(e: RenderEnvelope) -> str:
    """Say a card cannot be blocked here, point to the emergency line and note the handoff."""
    channel = _URGENT_CHANNEL[e.lang]
    return {
        "es": f"No puedo bloquear una tarjeta desde aquí. Hágalo de inmediato en {channel}. "
        "También estoy pasando esto a un asesor.",
        "pt": f"Não posso bloquear um cartão por aqui. Faça isso imediatamente, ligando para "
        f"{channel}. Também estou encaminhando isso para um atendente.",
        "en": f"I can't block a card here. Please do that right away through {channel}. I'm "
        "also passing this to a person.",
    }[e.lang]


def _handoff_requested(e: RenderEnvelope) -> str:
    """Confirm the customer is being connected to a person."""
    return {
        "es": "Le paso con un asesor ahora mismo. No hace falta que repita los detalles.",
        "pt": "Vou encaminhar você para um atendente agora mesmo. Você não precisa repetir os "
        "detalhes.",
        "en": "I'm connecting you with a person right now. You won't need to repeat the details.",
    }[e.lang]


def _handoff_not_registered(e: RenderEnvelope) -> str:
    """Say the request could not be registered and point the customer to the emergency line."""
    channel = _URGENT_CHANNEL[e.lang]
    return {
        "es": f"No pude registrar su solicitud en este momento. Por favor contacte al banco "
        f"por {channel}.",
        "pt": f"Não consegui registrar sua solicitação agora. Por favor, ligue para {channel}.",
        "en": f"I couldn't register your request right now. Please contact the bank through "
        f"{channel}.",
    }[e.lang]


def _restart_after_pending(e: RenderEnvelope) -> str:
    """Say the earlier session expired with nothing filed and start over."""
    return {
        "es": "Su sesión anterior expiró y no se presentó nada. Empecemos de nuevo: ¿qué "
        "transacción quiere revisar?",
        "pt": "Sua sessão anterior expirou e nenhuma contestação foi apresentada. Vamos "
        "começar de novo: qual transação você quer revisar?",
        "en": "Your previous session expired and nothing was filed. Let's start again: which "
        "transaction would you like to look at?",
    }[e.lang]


def _farewell(e: RenderEnvelope) -> str:
    """Close the conversation politely."""
    return {
        "es": "Gracias por escribir. Que tenga un buen día.",
        "pt": "Agradeço o contato. Tenha um bom dia.",
        "en": "Thank you for writing in. Have a good day.",
    }[e.lang]


# One template's renderer: reads only the envelope and returns the reply text.
_Renderer = Callable[[RenderEnvelope], str]

# Exactly one renderer per template, checked for completeness by the tests.
_RENDERERS: dict[TemplateId, _Renderer] = {
    TemplateId.GREETING: _greeting,
    TemplateId.CLARIFY_TRANSACTION: _clarify_transaction,
    TemplateId.CLARIFY_REASON: _clarify_reason,
    TemplateId.CLARIFY_CHOICE: _clarify_choice,
    TemplateId.CLARIFY_CONFIRMATION: _clarify_confirmation,
    TemplateId.LANGUAGE_OFFER: _language_offer,
    TemplateId.PRESENT_ONE: _present_one,
    TemplateId.PRESENT_LIST: _present_list,
    TemplateId.PRESENT_NARROW: _present_narrow,
    TemplateId.NOT_FOUND: _not_found,
    TemplateId.CONFIRM_FILING: _confirm_filing,
    TemplateId.FILING_RESULT: _filing_result,
    TemplateId.FILING_UNVERIFIED: _filing_unverified,
    TemplateId.FILING_CANCELLED: _filing_cancelled,
    TemplateId.INELIGIBLE: _ineligible,
    TemplateId.DISPUTE_STATUS: _dispute_status,
    TemplateId.NO_CASE_FOUND: _no_case_found,
    TemplateId.POLICY_ANSWER: _policy_answer,
    TemplateId.ABSTAIN_POLICY: _abstain_policy,
    TemplateId.REFUSE_UNSUPPORTED: _refuse_unsupported,
    TemplateId.REFUSE_REVERSAL: _refuse_reversal,
    TemplateId.HANDOFF_REVIEW: _handoff_review,
    TemplateId.HANDOFF_FRAUD: _handoff_fraud,
    TemplateId.HANDOFF_CARD_LOSS: _handoff_card_loss,
    TemplateId.HANDOFF_REQUESTED: _handoff_requested,
    TemplateId.HANDOFF_NOT_REGISTERED: _handoff_not_registered,
    TemplateId.RESTART_AFTER_PENDING: _restart_after_pending,
    TemplateId.FAREWELL: _farewell,
}


def render(envelope: RenderEnvelope) -> RenderedReply:
    """Render ``envelope`` with its fixed-wording template.

    Raises
    ------
    KeyError
        When ``envelope.template_id`` has no renderer (a contract addition this module has not
        caught up with yet).
    ValueError
        When ``envelope.render_mode`` is not ``"template"``.
    """
    if envelope.render_mode != "template" or envelope.template_id is None:
        raise ValueError("render() only handles template-mode envelopes")
    reply = _RENDERERS[envelope.template_id](envelope)
    return RenderedReply(
        reply=reply, reference_date_line=reference_date_line(envelope.domain_date, envelope.lang)
    )
