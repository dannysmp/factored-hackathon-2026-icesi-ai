"""
Language Stickiness
====================

Overview
--------
Decides which language a conversation continues in, from what was detected in the newest message,
an explicit request to switch, and how many consecutive messages in a row have not matched the
current language. The rule is deliberately resistant to a single stray message: one off-language
line does not move a conversation that has already settled.

Scope
-----
In: the pure decision function and its result.
Out: detecting a message's language (the understanding step) and asking the customer whether to
switch (the renderer's fixed wording).

Design Principles
-----------------
- A first message sets the language. When the detector cannot place it, the best guess is used
  and the result says so, so the caller can offer the other language in both languages.
- Once a language is set, it changes only on an explicit request, or on two consecutive messages
  read in another language — never on one.
- Pure function: no state is read or written here; the caller carries the streak forward and
  stores whatever this function returns.

Runtime Contract
----------------
``resolve_language(current, detected, explicit_switch_to, consecutive_off_language) ->
LanguageResolution``. ``LanguageResolution`` has ``lang``, ``streak`` and ``ambiguous``.

Limitations
-----------
The dialogue controller does not call this function: it keeps the language on ``DialogueState``
and moves it once, while the conversation is still in its opening (``DialogueState.is_opening``),
or on an explicit request. This module states the two-message rule (one off-language message does
not switch the language; a second consecutive one does) as a pure, tested decision for a caller
that carries a streak.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable result of resolving the language

# Local modules
from contracts.service_v1.envelope import Lang  # Closed set of languages

# The language assumed for a first message the detector cannot place at all.
_DEFAULT_LANG: Lang = "es"

# Consecutive off-language messages required before the conversation switches on its own.
_SWITCH_STREAK = 2


@dataclass(frozen=True, slots=True)
class LanguageResolution:
    """The language the conversation continues in, and whether to offer the other one."""

    lang: Lang
    streak: int
    ambiguous: bool


def resolve_language(
    current: Lang | None,
    detected: Lang | None,
    *,
    explicit_switch_to: Lang | None = None,
    consecutive_off_language: int = 0,
) -> LanguageResolution:
    """Decide the conversation's language for the newest message.

    Parameters
    ----------
    current : Lang | None
        The language the conversation is in, or ``None`` for the first message.
    detected : Lang | None
        The language the understanding step read the newest message in, or ``None`` when it could
        not tell.
    explicit_switch_to : Lang | None
        The language the customer asked to switch to, when the message was a request to change.
    consecutive_off_language : int
        How many messages in a row, ending just before this one, were read in a language other
        than ``current``. Ignored when ``current`` is ``None``.

    Returns
    -------
    LanguageResolution
        The language to continue in, the streak to carry into the next message, and whether this
        message was too ambiguous to place (only possible on the first message).
    """
    if current is None:
        if detected is not None:
            return LanguageResolution(lang=detected, streak=0, ambiguous=False)
        return LanguageResolution(lang=_DEFAULT_LANG, streak=0, ambiguous=True)

    if explicit_switch_to is not None and explicit_switch_to != current:
        return LanguageResolution(lang=explicit_switch_to, streak=0, ambiguous=False)

    if detected is None or detected == current:
        return LanguageResolution(lang=current, streak=0, ambiguous=False)

    streak = consecutive_off_language + 1
    if streak >= _SWITCH_STREAK:
        return LanguageResolution(lang=detected, streak=0, ambiguous=False)
    return LanguageResolution(lang=current, streak=streak, ambiguous=False)
