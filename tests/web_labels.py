"""
Web Quick Replies
=================

What the chat's confirmation buttons send and show, read from the web's own sources so that tests of
the dialogue follow the web instead of a copy of its words. A button posts a fixed text
(``CONFIRMATION_TEXT``, ``DECLINE_TEXT``) and displays a localized label in the transcript; only
the fixed text reaches the dialogue.
"""

from __future__ import annotations

# Standard libraries
import re
from pathlib import Path

_WEB = Path(__file__).resolve().parents[1] / "web" / "src"
_I18N = _WEB / "i18n"
_CONTRACTS = _WEB / "features" / "customer-chat" / "contracts.ts"


def web_sent_text(constant: str) -> str:
    """The fixed text the web posts for a button: the value of ``constant`` in the web contract."""
    match = re.search(
        rf"export const {re.escape(constant)}\s*=\s*'((?:[^'\\]|\\.)*)'",
        _CONTRACTS.read_text(encoding="utf-8"),
    )
    assert match is not None, f"{constant} is not defined in contracts.ts"
    return match.group(1)


def web_label(language: str, key: str) -> str:
    """The label the web displays for ``key`` in ``language`` (shown, never sent)."""
    source = (_I18N / f"{language}.ts").read_text(encoding="utf-8")
    match = re.search(
        rf"'{re.escape(key)}':\s*(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")", source
    )
    assert match is not None, f"{key} is not defined in {language}.ts"
    return (match.group(1) or match.group(2)).replace("\\'", "'")
