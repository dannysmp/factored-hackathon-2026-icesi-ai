"""
Golden Set Corpus
=================

Overview
--------
The authored cases of the held-out golden set, one module per category, plus the case sheet
generated from them. ``evals.models.Case`` defines the record shape; this package holds the data.

Scope
-----
In: the authored ``Case`` tuples, grouped by category, and the pure renderer that turns them into
the case-sheet CSV files; the judge-validation sample and its export for human raters.
Out: running or scoring a case (``evals.runner`` and ``evals.metrics``); checking that a
``seed_ref`` resolves against the seed data (the runner resolves a transaction reference against
the store when it runs the case, not at import time).

Design Principles
-----------------
Importing this package has no side effects and touches no file outside itself. Each category
module exports a ``CASES: tuple[Case, ...]`` constant; nothing here fixes how many categories
exist.
"""

from __future__ import annotations
