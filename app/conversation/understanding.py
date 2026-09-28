"""
Language Understanding Port
============================

Overview
--------
The port the dialogue controller reads a customer's message through, and a deterministic fake
that implements it without a model call. The Anthropic-backed adapter is a later change behind
the same port; nothing that consumes ``NluResult`` needs to know which implementation is in use.

Scope
-----
In: the ``Understanding`` port, ``TurnAccounting`` (what a real call cost, if one happened), and
``FakeNlu``, a keyword-based implementation for scripted conversations, tests and CI.
Out: the language model adapter that implements the same port against a real provider
(``app.conversation.llm_understanding``), what a caller does with the accounting (logging it is
the dialogue controller's job).

Design Principles
-----------------
- One call, one typed result — a pair, not a single value forced to carry two unrelated concerns.
  ``NluResult`` (``contracts/service_v1/nlu.py``) is a frozen, versioned service contract
  describing what was *understood*; it stays exactly that. ``TurnAccounting`` describes how the
  understanding was *produced* (which model, how many tokens, how long) and is ``None`` whenever
  no real model call happened — every call through ``FakeNlu``, and any real call the port itself
  could not complete. Widening the contract instead would force ``FakeNlu``, used in every test and
  CI path, to fabricate accounting for a call that made no request.
- The controller never sees raw model output, only the validated ``NluResult``; a caller that
  cannot understand a message at all uses ``NluResult.unusable()``.
- The fake is deliberately simple: keyword and pattern matching per language, not a stand-in for
  quality. Its purpose is to exercise every path the controller and the tests take, not to
  understand language; low-confidence and unclear results are still expected and correct outputs
  of it, matching what a real model also produces on a genuinely ambiguous message.

Runtime Contract
----------------
``Understanding`` (protocol): ``understand(text, *, language_hint) -> tuple[NluResult,
TurnAccounting | None]``, or raises ``UnderstandingUnavailable`` instead of returning at all.
``TurnAccounting(model, prompt_version, input_tokens, output_tokens, latency_ms)``.
``UnderstandingUnavailable``: raised instead of returning a result when the port could not reach
its own dependency after its bounded retries — distinct from ``NluResult.unusable()``, which
means the dependency answered but produced nothing usable. A caller that cannot tell the two apart
would spend a customer's clarification budget on an outage that was never their own confusion.
``FakeNlu``: a keyword-based implementation with no network access; always returns ``None``
accounting and never raises ``UnderstandingUnavailable``, since it makes no call that could fail.
"""

from __future__ import annotations

# Standard libraries
import re  # Keyword and pattern matching
from collections.abc import Callable  # Type of a rule's match test and result builder
from dataclasses import dataclass  # Immutable accounting record
from typing import Protocol  # The understanding port

# Local modules
from contracts.service_v1.envelope import Lang  # Closed set of languages
from contracts.service_v1.nlu import (  # The typed result and its vocabulary
    ConfirmationAnswer,
    NluIntent,
    NluResult,
    TransactionHint,
)


@dataclass(frozen=True, slots=True)
class TurnAccounting:
    """What one real model call behind a turn's understanding cost.

    Never produced by ``FakeNlu``, and never produced for a real call the port itself could not
    complete (``understand`` then returns ``NluResult.unusable()`` paired with ``None``, or raises
    ``UnderstandingUnavailable`` outright) — the absence of a real, priced call is not an
    accounting event.
    """

    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class UnderstandingUnavailable(Exception):
    """The port's own dependency could not be reached, after its bounded retries.

    Distinct from ``NluResult.unusable()``: that outcome means the dependency was reached and
    answered, just not with anything usable (empty text, a malformed model output) — genuine
    customer-facing ambiguity a clarification question can resolve. This exception means the
    dependency itself was not reachable; retrying the same question would not help, and the
    caller must not spend the customer's clarification budget on it. No accounting is produced
    either way: nothing was priced.
    """


class Understanding(Protocol):
    """Turns one customer message into a typed understanding."""

    def understand(
        self, text: str, *, language_hint: Lang | None
    ) -> tuple[NluResult, TurnAccounting | None]:
        """The understanding of ``text`` and, when a real model call produced it, its accounting.

        ``language_hint`` is a tie-breaker; the accounting half is ``None`` whenever no real,
        priced model call happened (``FakeNlu``, always; a real call the port could not complete).

        Raises
        ------
        UnderstandingUnavailable
            The port's own dependency could not be reached after its bounded retries.
        """
        ...


# Keyword sets used only to guess a message's language when nothing else says so.
_LANGUAGE_WORDS: dict[Lang, frozenset[str]] = {
    "es": frozenset(
        {"hola", "disputa", "cargo", "gracias", "asesor", "quiero", "tarjeta", "fraude", "cuenta"}
    ),
    "pt": frozenset(
        {
            "olá",
            "contestação",
            "cobrança",
            "obrigado",
            "atendente",
            "quero",
            "cartão",
            "fraude",
            "conta",
        }
    ),
    "en": frozenset(
        {"hello", "dispute", "charge", "thanks", "agent", "want", "card", "fraud", "account"}
    ),
}

_SWITCH_LANGUAGE_TARGETS: tuple[tuple[re.Pattern[str], Lang], ...] = (
    (re.compile(r"\b(español|espanhol|spanish)\b", re.IGNORECASE), "es"),
    (re.compile(r"\b(portugu[eéê]s|portuguese)\b", re.IGNORECASE), "pt"),
    (re.compile(r"\b(inglés|ingles|english)\b", re.IGNORECASE), "en"),
)

_FAREWELL = re.compile(
    r"\b(adiós|adios|chau|hasta luego|tchau|até logo|bye|goodbye|thanks?, ?that.?s all)\b",
    re.IGNORECASE,
)
_SMALL_TALK = re.compile(r"^\s*(hola|olá|ola|hello|hi|buenos días|bom dia)[!.\s]*$", re.IGNORECASE)
_REQUEST_PERSON = re.compile(
    r"\b(asesor|advisor|atendente|human|person|hablar con (alguien|un))\b", re.IGNORECASE
)
_FRAUD = re.compile(
    r"\b(fraude|fraud|clon\w*|robo|roubo|stolen use|no reconozco.*fraude)\b",
    re.IGNORECASE,
)
_CARD_LOSS = re.compile(r"\b(perdí|perdi|lost) (mi|meu|my) (tarjeta|cartão|card)\b", re.IGNORECASE)
_STATUS = re.compile(
    r"\b(mi disputa|meu caso|meu pedido|my dispute|my case|status)\b", re.IGNORECASE
)
_POLICY_QUESTION = re.compile(
    r"\b(cuánto tiempo|cuanto tiempo|quanto tempo|qual o prazo|how long|qué evidencia|"
    r"que evidência|what evidence)\b",
    re.IGNORECASE,
)
_UNSUPPORTED = re.compile(
    r"\b(transferir|transferência|transfer|aumentar.*límite|aumentar.*limite|"
    r"increase.*limit|bloquear|desbloquear|saldo|balance)\b",
    re.IGNORECASE,
)
_REVERSAL = re.compile(
    r"\b(devuélvanme|devolvam|reverse the charge|estorne|reembolso ya|refund now)\b", re.IGNORECASE
)
_FILE_DISPUTE = re.compile(
    r"\b(disputa|disputar|dispute|contest(ação|ar)?|cargo no reconocido|cobrança não "
    r"reconhecida|unrecognized charge|cobraron mal|cobraram errado|charged wrong)\b",
    re.IGNORECASE,
)
_YES = re.compile(
    r"^\s*(sí|si|confirmo|dale|sim|pode registrar|yes|go ahead|confirm)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_NO = re.compile(r"^\s*(no|não|nao)\s*[.!]?\s*$", re.IGNORECASE)
_BARE_OK = re.compile(r"^\s*(ok|okay|vale|beleza)\s*[.!]?\s*$", re.IGNORECASE)


def _detect_language(text: str, hint: Lang | None) -> Lang | None:
    """The best-matching language by keyword overlap, or ``hint`` on a tie or no match."""
    lowered = text.lower()
    scores = {
        lang: sum(1 for word in words if word in lowered) for lang, words in _LANGUAGE_WORDS.items()
    }
    best = max(scores.values(), default=0)
    if best == 0:
        return hint
    winners = [lang for lang, score in scores.items() if score == best]
    if len(winners) == 1:
        return winners[0]
    return hint if hint in winners else None


def _requested_language(text: str) -> Lang | None:
    for pattern, lang in _SWITCH_LANGUAGE_TARGETS:
        if pattern.search(text):
            return lang
    return None


_SWITCH_REQUEST = re.compile(
    r"\b(cambiar|mudar|switch|hablar en|falar em|talk in)\b", re.IGNORECASE
)

# One rule per recognized message shape: how to test it, and how to build the result once it
# matches. Order matters: checked top to bottom, the first match wins.
_Rule = tuple[Callable[[str], "re.Match[str] | None"], Callable[[str, Lang | None], NluResult]]


def _confirmation(
    answer: ConfirmationAnswer, confidence: float
) -> Callable[[str, Lang | None], NluResult]:
    def build(_text: str, language: Lang | None) -> NluResult:
        return NluResult(
            intent=NluIntent.CONFIRMATION,
            confidence=confidence,
            language=language,
            confirmation=answer,
        )

    return build


def _plain(intent: NluIntent, confidence: float) -> Callable[[str, Lang | None], NluResult]:
    def build(_text: str, language: Lang | None) -> NluResult:
        return NluResult(intent=intent, confidence=confidence, language=language)

    return build


def _policy_question(_text: str, language: Lang | None) -> NluResult:
    return NluResult(
        intent=NluIntent.POLICY_QUESTION,
        confidence=0.85,
        language=language,
        policy_query=_text[:200],
    )


def _file_dispute(_text: str, language: Lang | None) -> NluResult:
    return NluResult(
        intent=NluIntent.FILE_DISPUTE,
        confidence=0.8,
        language=language,
        transaction=TransactionHint(),
    )


_RULES: tuple[_Rule, ...] = (
    (_YES.match, _confirmation(ConfirmationAnswer.YES, 0.95)),
    (_NO.match, _confirmation(ConfirmationAnswer.NO, 0.95)),
    (_BARE_OK.match, _confirmation(ConfirmationAnswer.AMBIGUOUS, 0.4)),
    (_FAREWELL.search, _plain(NluIntent.FAREWELL, 0.9)),
    (_CARD_LOSS.search, _plain(NluIntent.REPORT_CARD_LOSS, 0.9)),
    (_FRAUD.search, _plain(NluIntent.REPORT_FRAUD, 0.9)),
    (_REQUEST_PERSON.search, _plain(NluIntent.REQUEST_PERSON, 0.9)),
    (_REVERSAL.search, _plain(NluIntent.REQUEST_REVERSAL, 0.85)),
    (_UNSUPPORTED.search, _plain(NluIntent.UNSUPPORTED_ACTION, 0.85)),
    (_POLICY_QUESTION.search, _policy_question),
    (_STATUS.search, _plain(NluIntent.DISPUTE_STATUS, 0.85)),
    (_FILE_DISPUTE.search, _file_dispute),
    (_SMALL_TALK.match, _plain(NluIntent.SMALL_TALK, 0.9)),
)


def _classify(text: str, *, language_hint: Lang | None) -> NluResult:
    """Classify ``text`` by keyword and pattern matching."""
    if not text.strip():
        return NluResult.unusable()

    language = _detect_language(text, language_hint)
    requested = _requested_language(text)
    if requested is not None and _SWITCH_REQUEST.search(text):
        return NluResult(
            intent=NluIntent.SWITCH_LANGUAGE,
            confidence=0.95,
            language=language,
            requested_language=requested,
        )

    for matches, build in _RULES:
        if matches(text) is not None:
            return build(text, language)
    return NluResult(intent=NluIntent.UNCLEAR, confidence=0.3, language=language)


class FakeNlu:
    """A deterministic, keyword-based ``Understanding``; no network, no model."""

    def understand(
        self, text: str, *, language_hint: Lang | None
    ) -> tuple[NluResult, TurnAccounting | None]:
        """Classify ``text`` by keyword and pattern matching; never produces accounting."""
        return _classify(text, language_hint=language_hint), None
