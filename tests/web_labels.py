"""
Web Labels
==========

The quick-reply labels the chat shows, read from the web's own translation files so that tests of
the dialogue follow a relabelled button instead of a copy of its words.
"""

from __future__ import annotations

# Standard libraries
import re
from pathlib import Path

_I18N = Path(__file__).resolve().parents[1] / "web" / "src" / "i18n"


def web_label(language: str, key: str) -> str:
    """The label the web shows for ``key`` in ``language``."""
    source = (_I18N / f"{language}.ts").read_text(encoding="utf-8")
    match = re.search(
        rf"'{re.escape(key)}':\s*(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")", source
    )
    assert match is not None, f"{key} is not defined in {language}.ts"
    return (match.group(1) or match.group(2)).replace("\\'", "'")
