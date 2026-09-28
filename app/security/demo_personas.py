"""
Demo Personas
=============

Overview
--------
Reads the demo sign-in broker's persona file into a validated ``PersonaList``, and checks each
customer persona against the seed at start-up (ADR-18, CR-12 Arch C4). The demo broker route
accepts a persona slug, never a customer identifier or a document number; this module is what
turns a slug into a customer_id, and only after start-up has already proven that customer_id
resolves to a real, active, seeded customer.

Scope
-----
In: reading and validating ``personas/demo_personas_v1.yaml``, the start-up check against the
seed, and looking a slug up by name.
Out: the demo broker route itself (which calls this module), the agent audience's consumer (1.5b
wires ``PersonaList.agents``; this slice only validates and looks up ``customers``).

Design Principles
------------------
- Fail loudly at start-up, exactly like the policy loader: an unreadable, malformed or invalid
  persona file raises ``PersonaError`` naming the file; an unresolvable or inactive customer
  raises it naming the **slug**, never the customer_id (SECURITY.md: PII minimization) — CR-12
  requires active customers only, stricter than the sandbox login's own no-status-gate rule
  (AC-E4-48 does not apply here: this is a startup data-integrity check, not a sign-in decision).
- The validation only runs when the caller asks for it (``validate_active_customers``), so an
  environment with ``DEMO_SIGNIN_ENABLED`` off never needs seed data available to start.
- Models reject unknown fields, matching this codebase's other loaders (the policy, the contracts).

Runtime Contract
-----------------
``PersonaList``, ``CustomerPersona``, ``AgentPersona``; ``load_personas(path) -> PersonaList``
raises ``PersonaError``; ``validate_active_customers(personas, lookup) -> None`` raises
``PersonaError``; ``DEFAULT_PERSONAS_PATH``.

Limitations
-----------
Duplicate slugs in the file are rejected by ``PersonaList``'s own validator, but a duplicate YAML
*key* within one persona entry is not specially detected (unlike the policy loader's unique-key
YAML loader) — a persona file is small and hand-reviewed, so this is judged unnecessary here.
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Callable  # Type of the customer-existence check
from pathlib import Path  # Location of the persona file
from typing import Annotated, Literal  # Bounded fields and the closed set of languages

# Third-party libraries
import yaml  # Parsing the persona file
from pydantic import (  # Validated models
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

# Local modules
from app.security.sessions import CUSTOMER_ID_PATTERN  # One shape for a signed subject

DEFAULT_PERSONAS_PATH = Path(__file__).resolve().parents[2] / "personas" / "demo_personas_v1.yaml"
_ANCHORED_CUSTOMER_ID = f"^{CUSTOMER_ID_PATTERN.pattern}$"

# The same shape as app.api.auth.CustomerLookup (a customer's status, or None): defined here too,
# rather than imported from that route module, so this lower-layer module never depends upward on
# the API layer for a one-line type alias.
CustomerLookup = Callable[[str], str | None]

_ACTIVE_STATUS = "Active"


class _Frozen(BaseModel):
    """Base of every model here: immutable, and unknown fields are an error."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CustomerPersona(_Frozen):
    """One customer the demo broker may sign in, by slug."""

    slug: str
    customer_id: Annotated[str, Field(pattern=_ANCHORED_CUSTOMER_ID)]
    language: Literal["es", "pt", "en"]
    scenario: str


class AgentPersona(_Frozen):
    """One agent the demo broker may sign in (1.5b wires this; this slice only carries it)."""

    slug: str
    agent_id: str
    languages: tuple[str, ...]
    specialty: str | None = None


class PersonaList(_Frozen):
    """The whole persona file: every slug the demo broker will ever accept."""

    version: int
    customers: tuple[CustomerPersona, ...]
    agents: tuple[AgentPersona, ...] = ()

    @model_validator(mode="after")
    def _slugs_are_unique_within_each_audience(self) -> PersonaList:
        """A repeated slug would make "which persona is this" ambiguous."""
        customer_slugs = [persona.slug for persona in self.customers]
        agent_slugs = [persona.slug for persona in self.agents]
        if len(set(customer_slugs)) != len(customer_slugs):
            raise ValueError("customer persona slugs must be unique")
        if len(set(agent_slugs)) != len(agent_slugs):
            raise ValueError("agent persona slugs must be unique")
        return self

    @model_validator(mode="after")
    def _at_least_one_customer_persona(self) -> PersonaList:
        """An empty persona list would mean the broker starts but no one could ever sign in."""
        if not self.customers:
            raise ValueError("at least one customer persona is required")
        return self

    def customer_by_slug(self, slug: str) -> CustomerPersona | None:
        """The customer persona named ``slug``, or ``None`` if there is no such slug."""
        return next((persona for persona in self.customers if persona.slug == slug), None)


class PersonaError(Exception):
    """The persona file could not be read, does not describe a valid list, or fails start-up
    validation against the seed."""


def load_personas(path: Path = DEFAULT_PERSONAS_PATH) -> PersonaList:
    """Read and validate the persona file at ``path``.

    Raises
    ------
    PersonaError
        When the file cannot be read, is not valid YAML, is not a mapping, or fails validation.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise PersonaError(f"persona file {path.name} cannot be read") from error
    except yaml.YAMLError as error:
        raise PersonaError(f"persona file {path.name} is not valid YAML") from error
    if not isinstance(document, dict):
        raise PersonaError(f"persona file {path.name} must contain a mapping")
    try:
        return PersonaList.model_validate(document)
    except ValidationError as error:
        raise PersonaError(f"persona file {path.name} is invalid: {error}") from error


def validate_active_customers(personas: PersonaList, lookup: CustomerLookup) -> None:
    """Check every customer persona resolves to an Active seeded customer.

    Raises
    ------
    PersonaError
        Naming the offending **slug** — never the customer_id it maps to — for the first persona
        whose customer_id does not resolve, or resolves to a status other than Active.
    """
    for persona in personas.customers:
        status = lookup(persona.customer_id)
        if status is None:
            raise PersonaError(f"persona {persona.slug!r} does not resolve to a seeded customer")
        if status != _ACTIVE_STATUS:
            raise PersonaError(f"persona {persona.slug!r} is not an Active customer")
