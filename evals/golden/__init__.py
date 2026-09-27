"""
Golden Set Corpus
=================

Overview
--------
The 135 authored cases of the held-out golden set, one module per category-group slice, plus the
generated CSV case sheet built from them. `evals.models.Case` defines the record shape; this
package holds the data itself.

Scope
-----
In: the authored `Case` tuples, grouped by category, and the pure renderer that turns them into
the case-sheet CSV.
Out: running or scoring a case (the runner and `evals.metrics`, both elsewhere); validating that
a `seed_ref` resolves against real `data/gold/ops_seed` or `data/gold/eval_bank` rows (checked by
the pipeline fixture that builds them, not at import time here).

Design Principles
-------------------
Importing this package has no side effects and touches no file outside itself. Each category
module exports a `CASES: tuple[Case, ...]` constant; nothing here decides how many categories
exist beyond what has already landed.
"""

from __future__ import annotations
