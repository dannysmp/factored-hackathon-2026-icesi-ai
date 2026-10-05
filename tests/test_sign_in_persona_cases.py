"""
Sign-in Persona Case Coverage Tests
====================================

Component: the demonstration persona directory (``personas/demo_personas_v1.yaml``) and the
sign-in screen's case descriptions (``web/src/features/sign-in/personaCases.ts``). Hermetic: both
files are read from the repository. The screen shows no case line for a persona it has no
description for, so every persona the broker can offer must be described there, and the screen
must not describe a persona the broker does not offer.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.security.demo_personas import DEFAULT_PERSONAS_PATH, load_personas

_PERSONA_CASES = Path(__file__).resolve().parent.parent / (
    "web/src/features/sign-in/personaCases.ts"
)
_CASE_KEY = re.compile(r"^\s*'?([a-z][a-z-]*)'?:\s*'signin\.persona\.([a-z-]+)\.case',\s*$", re.M)


def test_every_persona_has_a_case_description_and_no_other_does() -> None:
    """The screen's case keys name exactly the slugs the persona directory offers."""
    personas = load_personas(DEFAULT_PERSONAS_PATH)
    offered = {persona.slug for persona in personas.customers} | {
        persona.slug for persona in personas.agents
    }
    described = _CASE_KEY.findall(_PERSONA_CASES.read_text(encoding="utf-8"))

    assert offered
    assert {slug for slug, _ in described} == offered
    assert all(slug == key_slug for slug, key_slug in described)
