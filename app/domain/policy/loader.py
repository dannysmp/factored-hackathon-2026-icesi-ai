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
- Safe YAML only, and a validation error reports the fields at fault, never a value read from
  the file.

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

# Third-party libraries
import yaml  # Safe parsing of the policy file
from pydantic import ValidationError  # Raised by the policy model on invalid content

# Local modules
from app.domain.policy.models import Policy  # Validated policy

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[3] / "policy" / "dispute_policy_v1.yaml"


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
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
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


def _describe(error: ValidationError) -> str:
    """The fields at fault, or the policy-wide rule that failed; never a value from the file.

    Field errors are located by their path. A rule about the policy as a whole has no path, and
    its message is written by this package from names of the vocabulary, not from the file.
    """
    parts = {
        ".".join(str(step) for step in item["loc"]) or f"policy ({item['msg']})"
        for item in error.errors()
    }
    return ", ".join(sorted(parts))
