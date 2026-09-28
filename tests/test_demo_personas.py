"""
Demo Persona Tests
===================

Component: ``app.security.demo_personas``. Hermetic: personas are read from temporary files, and
the customer lookup is a fake, exactly like the sandbox login's own tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.security.demo_personas import (
    DEFAULT_PERSONAS_PATH,
    PersonaError,
    PersonaList,
    load_personas,
    validate_active_customers,
)

_VALID_DOCUMENT = """
version: 1
customers:
  - slug: ana
    customer_id: CUST-1
    language: es
    scenario: eligible
  - slug: joao
    customer_id: CUST-2
    language: pt
    scenario: repeat_complainer
agents:
  - slug: agent-beatriz
    agent_id: AGENT-1
    languages: [pt, es]
    specialty: null
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "personas.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_persona_file_loads_and_validates_structurally() -> None:
    """The file this repository ships parses; its placeholder customer_ids are a seed question,
    not a loader question (proven separately by the validate_active_customers tests below)."""
    personas = load_personas(DEFAULT_PERSONAS_PATH)

    assert len(personas.customers) >= 1


def test_a_valid_persona_file_loads(tmp_path: Path) -> None:
    personas = load_personas(_write(tmp_path, _VALID_DOCUMENT))

    assert [persona.slug for persona in personas.customers] == ["ana", "joao"]
    ana = personas.customer_by_slug("ana")
    assert ana is not None
    assert ana.customer_id == "CUST-1"
    assert personas.customer_by_slug("unknown") is None

    beatriz = personas.agent_by_slug("agent-beatriz")
    assert beatriz is not None
    assert beatriz.agent_id == "AGENT-1"
    assert personas.agent_by_slug("unknown") is None


@pytest.mark.parametrize(
    "text",
    [
        "not: [valid, yaml: at all",
        "- a\n- list\n- not a mapping",
        "version: 1\ncustomers: []\n",
        "version: 1\ncustomers:\n  - slug: a\n    customer_id: C1\n    language: es\n"
        "    scenario: eligible\n  - slug: a\n    customer_id: C2\n    language: es\n"
        "    scenario: eligible\n",
        "version: 1\ncustomers:\n  - slug: a\n    customer_id: C1\n    language: xx\n"
        "    scenario: eligible\n",
        "version: 1\ncustomers:\n  - slug: a\n    customer_id: C1\n    language: es\n"
        "    scenario: eligible\n    extra_field: nope\n",
        "version: 1\ncustomers:\n  - slug: a\n    customer_id: C1\n    language: es\n"
        "    scenario: eligible\nagents:\n  - slug: b\n    agent_id: A1\n    languages: [es]\n"
        "  - slug: b\n    agent_id: A2\n    languages: [es]\n",
        "version: 1\ncustomers:\n  - slug: a\n    customer_id: C1\n    language: es\n"
        "    scenario: eligible\nagents:\n  - slug: b\n    agent_id: 'has a space'\n"
        "    languages: [es]\n",
    ],
    ids=[
        "bad-yaml",
        "not-a-mapping",
        "empty-customers",
        "duplicate-slug",
        "bad-language",
        "unknown-field",
        "duplicate-agent-slug",
        "malformed-agent-id",
    ],
)
def test_an_invalid_persona_file_is_refused(tmp_path: Path, text: str) -> None:
    with pytest.raises(PersonaError):
        load_personas(_write(tmp_path, text))


def test_an_unreadable_persona_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PersonaError, match="cannot be read"):
        load_personas(tmp_path / "does-not-exist.yaml")


def test_every_persona_resolving_to_an_active_customer_passes(tmp_path: Path) -> None:
    personas = load_personas(_write(tmp_path, _VALID_DOCUMENT))

    validate_active_customers(personas, lambda customer_id: "Active")


def test_a_persona_whose_customer_id_does_not_resolve_is_refused_by_slug(tmp_path: Path) -> None:
    personas = load_personas(_write(tmp_path, _VALID_DOCUMENT))

    with pytest.raises(PersonaError, match="'ana'") as raised:
        validate_active_customers(personas, lambda customer_id: None)
    assert "CUST-1" not in str(raised.value)


def test_a_persona_resolving_to_an_inactive_customer_is_refused(tmp_path: Path) -> None:
    personas = load_personas(_write(tmp_path, _VALID_DOCUMENT))

    with pytest.raises(PersonaError, match="not an Active customer"):
        validate_active_customers(personas, lambda customer_id: "Suspended")


def test_personas_are_immutable() -> None:
    personas = PersonaList(
        version=1,
        customers=(
            {
                "slug": "ana",
                "customer_id": "CUST-1",
                "language": "es",
                "scenario": "eligible",
            },
        ),
    )

    with pytest.raises(Exception):  # noqa: B017 - pydantic's own frozen-model error
        personas.version = 2  # type: ignore[misc]
