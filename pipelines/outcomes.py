"""
Cleaning Outcomes
=================

Overview
--------
The result objects of a cleaning run: what happened to each table and the manifest that
describes it. Kept apart from the stage and the report renderer, which both use them.

Scope
-----
In: value objects.
Out: producing (``pipelines.silver``) or presenting (``pipelines.quality``) them.

Design Principles
-----------------
Frozen dataclasses; a manifest is a plain JSON-serializable dictionary because it is also the
on-disk format.
"""

from __future__ import annotations

# Standard libraries
from dataclasses import dataclass  # Immutable result objects
from enum import StrEnum  # Closed set of outcomes
from typing import Any  # Manifest values


class Status(StrEnum):
    """What happened to one table in a run."""

    BUILT = "built"
    UNCHANGED = "unchanged"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class TableOutcome:
    """Result of processing one table."""

    table: str
    status: Status
    reason: str | None
    manifest: dict[str, Any] | None
