"""
Smoke Case Selection
======================

Overview
--------
The fixed subset of the golden set the CI smoke job runs on every pull request, "all injection +
authz": the prompt-injection (via a user message or a poisoned data field) and unauthorized-access
subtypes of the adversarial category, 16 cases in total.

Scope
-----
In: ``SMOKE_CASE_IDS``, naming the cases explicitly, and ``smoke_cases``, resolving them against
the real golden set.
Out: seeding the store the smoke set's cases need (a CI-only synthetic substitute, a separate
module); running or scoring the cases (the runner, unchanged); the other adversarial subtypes
(expired session, tool failure, bad data) — real coverage, already exercised by this project's own
integration tests, just not part of the PR-gating smoke subset.

Design Principles
-----------------
- **A fixed, explicit list, not a prefix match.** ``evals.golden.adversarial``'s case ids already
  group by subtype (``adv-injection-*``, ``adv-poisoned-*``, ``adv-unauthorized-*``), but naming
  the 16 ids directly here means a case added later under the same prefix does not silently grow
  — or shrink — what the smoke job runs without being added to this list too.
- **Fails loudly if the golden set changes underneath it.** ``smoke_cases`` raises if any id in
  ``SMOKE_CASE_IDS`` no longer resolves, rather than silently running fewer cases than the CI job
  is meant to cover.

Runtime Contract
-----------------
``SMOKE_CASE_IDS: frozenset[str]``.
``smoke_cases() -> tuple[Case, ...]``, in ``evals.golden.adversarial.CASES``' own declared order
(a ``frozenset`` has no stable iteration order of its own to preserve).

Limitations
-----------
Drawn only from ``evals.golden.adversarial`` today, since every injection and unauthorized-access
case lives there; a future smoke case from another category module adds its id here and its import
above, not a new resolution mechanism.
"""

from __future__ import annotations

from evals.golden.adversarial import CASES as ADVERSARIAL_CASES
from evals.models import Case

#: The 16 cases of "all injection + authz": every `adv-injection-*` and `adv-poisoned-*` case (10,
#: prompt injection via a user message and via a poisoned data field), plus every
#: `adv-unauthorized-*` case (6).
SMOKE_CASE_IDS: frozenset[str] = frozenset(
    {
        "adv-injection-es-01",
        "adv-injection-es-02",
        "adv-injection-es-03",
        "adv-injection-pt-01",
        "adv-injection-pt-02",
        "adv-injection-en-01",
        "adv-poisoned-es-01",
        "adv-poisoned-es-02",
        "adv-poisoned-pt-01",
        "adv-poisoned-en-01",
        "adv-unauthorized-es-01",
        "adv-unauthorized-es-02",
        "adv-unauthorized-es-03",
        "adv-unauthorized-pt-01",
        "adv-unauthorized-pt-02",
        "adv-unauthorized-en-01",
    }
)


def smoke_cases() -> tuple[Case, ...]:
    """The golden-set ``Case`` records ``SMOKE_CASE_IDS`` names, in the golden set's own order.

    Raises
    ------
    ValueError
        One of ``SMOKE_CASE_IDS`` resolves to no case in ``evals.golden.adversarial.CASES``.
    """
    found_ids = {case.case_id for case in ADVERSARIAL_CASES} & SMOKE_CASE_IDS
    missing = SMOKE_CASE_IDS - found_ids
    if missing:
        raise ValueError(f"smoke case id(s) not found in the golden set: {sorted(missing)}")
    return tuple(case for case in ADVERSARIAL_CASES if case.case_id in SMOKE_CASE_IDS)
