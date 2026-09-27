"""
Prompts as Versioned Configuration
====================================

Overview
--------
Loads a prompt from a YAML file under ``prompts/`` and renders its task template with the turn's
own values. A prompt is data, not a Python string embedded in the adapter: editing wording is a
reviewed change to a file, never a code change, and every logged model call can name exactly the
version that produced it.

Scope
-----
In: reading one prompt file into a validated ``PromptTemplate`` and rendering its task template.
Out: what the rendered text is used for (the LLM port) and masking it (``masking``); a prompt
receives already-masked text, never raw customer text.

Design Principles
-----------------
- Fail loudly at load time: an unreadable, malformed or invalid prompt file raises ``PromptError``
  naming the file, the same discipline the policy loader applies to the policy file.
- Rendering is strict in both directions: a missing placeholder value and a value passed for a
  placeholder the template does not use are both errors. A silently dropped value is exactly the
  kind of mistake that would let stale wording ship unnoticed.

Runtime Contract
----------------
``load_prompt(name) -> PromptTemplate`` raises ``PromptError``.
``PromptTemplate.render_task(**values) -> str`` raises ``PromptError``.
``PROMPTS_DIR``: the directory prompt files ship from.
"""

from __future__ import annotations

# Standard libraries
from pathlib import Path  # Location of prompt files
from string import Formatter  # Extracting a template's named placeholders
from typing import Annotated, Any  # Bounded fields, parsed YAML content

# Third-party libraries
import yaml  # Safe parsing of the prompt file
from pydantic import BaseModel, ConfigDict, Field, ValidationError  # Validated prompt shape

# The directory prompt files ship from, found from this package, not from the working directory.
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class PromptError(Exception):
    """A prompt file could not be read or parsed, or a render call was given the wrong values."""


class PromptTemplate(BaseModel):
    """One versioned prompt: a system message and a task template with named placeholders."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Annotated[str, Field(min_length=1, max_length=16)]
    system: Annotated[str, Field(min_length=1)]
    task_template: Annotated[str, Field(min_length=1)]

    def placeholders(self) -> frozenset[str]:
        """The named placeholders ``task_template`` uses."""
        return frozenset(name for _, name, _, _ in Formatter().parse(self.task_template) if name)

    def render_task(self, **values: str) -> str:
        """Fill the task template with ``values``.

        Raises
        ------
        PromptError
            A placeholder the template uses has no value, or a value was passed for a name the
            template does not use.
        """
        expected = self.placeholders()
        given = set(values)
        missing = expected - given
        extra = given - expected
        if missing:
            raise PromptError(f"missing placeholder value(s): {', '.join(sorted(missing))}")
        if extra:
            raise PromptError(f"unused placeholder value(s) passed: {', '.join(sorted(extra))}")
        return self.task_template.format(**values)


def load_prompt(name: str, *, directory: Path = PROMPTS_DIR) -> PromptTemplate:
    """Read and validate the prompt file ``<directory>/<name>.yaml``.

    Raises
    ------
    PromptError
        When the file cannot be read, is not valid YAML, is not a mapping, or fails validation.
        The message names the file and, for validation errors, the offending fields; never a
        value read from the file.
    """
    path = directory / f"{name}.yaml"
    try:
        document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise PromptError(f"prompt file {path.name} cannot be read") from error
    except yaml.YAMLError as error:
        raise PromptError(f"prompt file {path.name} is not valid YAML") from error
    if not isinstance(document, dict):
        raise PromptError(f"prompt file {path.name} must contain a mapping")
    try:
        return PromptTemplate.model_validate(document)
    except ValidationError as error:
        fields = ", ".join(".".join(str(part) for part in item["loc"]) for item in error.errors())
        raise PromptError(f"prompt file {path.name} is invalid at: {fields}") from error
