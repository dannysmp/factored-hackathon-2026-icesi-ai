"""
Text Matching
=============

Overview
--------
The one rule for comparing what a customer typed with stored text: accents and case do not matter,
so "cafe" finds "Café Sol" and "São Paulo" finds "SAO PAULO". The dialogue controller applies it
in Python, and the transaction store applies the same rule inside its query through the character
map below, so a listing narrowed by the store never excludes what the controller would accept.

Scope
-----
In: ``fold_text(text)``, ``SQL_FOLD_FROM`` and ``SQL_FOLD_TO`` (the arguments of the store's
``translate`` call, which finishes with ``lower``).
Out: deciding what is being compared, and any matching other than "contains".

Design Principles
-----------------
- The character map is derived from ``fold_text`` itself over the Latin-1 and Latin Extended-A
  letters, never written out by hand, so the two sides cannot drift apart.
- A character whose fold is not a single character (the German sharp s, ligatures) is outside the
  map; no such character is in the stored merchant names.

Runtime Contract
----------------
Pure and deterministic: no I/O, no clock.
"""

from __future__ import annotations

# Standard libraries
import unicodedata  # Decomposition into base letter and accent


def fold_text(text: str) -> str:
    """``text`` without accents and case, so "cafe" and "Café" compare equal."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def _fold_map() -> tuple[str, str]:
    """The letters whose fold is one different character, and that character for each."""
    pairs = [
        (char, fold_text(char))
        for char in map(chr, range(0xC0, 0x180))
        if len(fold_text(char)) == 1 and fold_text(char) != char
    ]
    return "".join(c for c, _ in pairs), "".join(f for _, f in pairs)


SQL_FOLD_FROM, SQL_FOLD_TO = _fold_map()
