"""
Dispute Policy Loader
=====================

Overview
--------
Reads a policy file into a validated ``Policy``. This is the only place where the policy touches
the file system, which keeps the engine free of I/O.

Scope
-----
In: reading and validating one YAML policy file.
Out: applying the policy (``engine``) and choosing which version is in force (composition root).

Design Principles
-----------------
- Fail loudly at start-up: an unreadable, malformed or invalid policy raises ``PolicyError``
  naming the file, so the service never runs on a policy it could not validate.
- Safe YAML only, duplicate keys refused, and a validation error reports the fields at fault
  using the policy's own vocabulary; neither a value nor an unknown key read from the file is
  ever echoed.

Runtime Contract
----------------
``load_policy(path) -> Policy`` raises ``PolicyError``.
``DEFAULT_POLICY_PATH``: the policy file that ships with the repository.

Limitations
-----------
A file holds exactly one policy version; selecting among versions is the caller's decision.
"""

from __future__ import annotations

# Standard libraries
from pathlib import Path  # Location of the policy file
from typing import Any  # Keys and values of parsed YAML

# Third-party libraries
import yaml  # Safe parsing of the policy file
from pydantic import ValidationError  # Raised by the policy model on invalid content

# Local modules
from app.domain.policy.models import (  # Validated policy and its vocabulary
    CategoryRule,
    DisputeCategory,
    Policy,
    RoutingRules,
)

# The policy file that ships with the repository, found from this package, not from the working
# directory.
DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[3] / "policy" / "dispute_policy_v1.yaml"


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses a mapping that repeats a key.

    A repeated key would otherwise let the last value win silently, so an edited parameter could
    be overridden further down the file without anyone noticing.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        """Build the mapping, refusing duplicate keys."""
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                repeated = key in seen
            except TypeError as error:  # a list or mapping used as a key cannot be hashed
                raise yaml.YAMLError("unhashable key") from error
            if repeated:
                raise yaml.YAMLError("duplicate key")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class PolicyError(Exception):
    """The policy file could not be read or does not describe a valid policy."""


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> Policy:
    """Read and validate the policy file at ``path``.

    Raises
    ------
    PolicyError
        When the file cannot be read, is not valid YAML, is not a mapping, or fails validation.
        The message names the file and, for validation errors, the offending fields.
    """
    # Read the file and parse it as plain data
    try:
        document = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)  # noqa: S506 - SafeLoader subclass
    except (OSError, UnicodeDecodeError) as error:
        raise PolicyError(f"policy file {path.name} cannot be read") from error
    except yaml.YAMLError as error:
        raise PolicyError(f"policy file {path.name} is not valid YAML") from error
    if not isinstance(document, dict):
        raise PolicyError(f"policy file {path.name} must contain a mapping")

    # Validate it against the policy model, reporting the fields at fault and no values
    try:
        return Policy.model_validate(document)
    except ValidationError as error:
        raise PolicyError(f"policy file {path.name} is invalid at: {_describe(error)}") from error


# Names a validation error may show: the policy's own fields, categories and list positions.
_KNOWN_NAMES = (
    set(Policy.model_fields)
    | set(CategoryRule.model_fields)
    | set(RoutingRules.model_fields)
    | {category.value for category in DisputeCategory}
)


def _describe(error: ValidationError) -> str:
    """The places at fault, or the policy-wide rule that failed; never data from the file.

    A path step that is not part of the policy's vocabulary (an unknown key read from the file)
    is shown as ``?``. A rule about the policy as a whole has no path, and its message is written
    by this package from names of the vocabulary.
    """
    parts = {
        ".".join(
            str(step) if isinstance(step, int) or step in _KNOWN_NAMES else "?"
            for step in item["loc"]
        )
        or f"policy ({item['msg']})"
        for item in error.errors()
    }
    return ", ".join(sorted(parts))
