"""
Quick Answers
=============

Overview
--------
Reads a message that is exactly a yes or a no to the question the dialogue is waiting on, without
a model call: the fixed "yes" and "no" the chat's buttons send and the short forms customers type
in their place, in Spanish, Portuguese and English.

Scope
-----
In: ``read_quick_answer(text) -> ConfirmationAnswer | None``.
Out: deciding when a yes or no is expected (the dialogue controller, from its pending slot) and
every longer or mixed message, which only the understanding step reads.

Design Principles
-----------------
- A whole-message match against a closed set, never a substring: "no, la otra transacción" is a
  correction, not a decline, and stays with the understanding step.
- The model reads each message without the conversation, so it cannot know a question is pending
  and can read a bare "no" as unclear. The controller knows, and asks this module only while a yes
  or no is awaited.
- Accents, case and punctuation do not matter; the words do.

Runtime Contract
----------------
Pure and deterministic: no I/O, no clock. ``None`` means the message is not a known quick answer.
"""

from __future__ import annotations

# Standard libraries
import re  # Punctuation removal
import unicodedata  # Accent-insensitive comparison

# Local application
from contracts.service_v1.nlu import ConfirmationAnswer  # The answer a quick reply stands for

_YES_PHRASES = frozenset(
    {
        # Spanish
        "si",
        "si registrar",
        "si registrala",
        "registrar",
        "registrala",
        "si confirmo",
        "confirmo",
        "dale",
        "claro",
        "si claro",
        "si por favor",
        # Portuguese
        "sim",
        "sim registrar",
        "sim pode registrar",
        "pode registrar",
        "claro que sim",
        "sim por favor",
        # English
        "yes",
        "yes file it",
        "yes please",
        "yes confirm",
        "file it",
        "go ahead",
        "confirm",
        "sure",
        "yes go ahead",
    }
)

_NO_PHRASES = frozenset(
    {
        # Spanish
        "no",
        "no registrar",
        "no no registrar",
        "no la registres",
        "no la registre",
        "no registrarla",
        "no confirmo",
        "no quiero",
        "no gracias",
        "mejor no",
        "no por favor",
        # Portuguese
        "nao",
        "nao registrar",
        "nao obrigado",
        "nao obrigada",
        "nao quero",
        "nao confirmo",
        "melhor nao",
        "nao nao registrar",
        # English
        "no dont file",
        "no dont file it",
        "dont file",
        "dont file it",
        "no thanks",
        "no thank you",
        "better not",
    }
)


def _normalized(text: str) -> str:
    """The message lower-cased, without accents or apostrophes, with other punctuation as spaces."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    unaccented = "".join(char for char in decomposed if not unicodedata.combining(char))
    without_apostrophes = re.sub("['\u2019`]", "", unaccented)
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_apostrophes).split())


def read_quick_answer(text: str) -> ConfirmationAnswer | None:
    """The answer ``text`` stands for when it is exactly a known yes or no, else ``None``."""
    phrase = _normalized(text)
    if phrase in _YES_PHRASES:
        return ConfirmationAnswer.YES
    if phrase in _NO_PHRASES:
        return ConfirmationAnswer.NO
    return None
