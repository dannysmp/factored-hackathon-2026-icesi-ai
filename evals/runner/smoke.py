"""
Smoke Case Selection
====================

Overview
--------
The fixed subset of the golden set that the CI smoke job runs on every pull request: the
prompt-injection subtypes (through a user message and through a poisoned data field) and the
unauthorized-access subtype of the adversarial category, 16 cases in total.

Scope
-----
In: ``SMOKE_CASE_IDS``, naming the cases explicitly, and ``smoke_cases``, resolving them against
the golden set.
Out: seeding the store the smoke cases need (a CI-only synthetic substitute in a separate module);
running or scoring the cases (``evals.runner.runner``); the other adversarial subtypes (expired
session, tool failure, bad data), which the integration tests cover but the smoke subset omits.

Design Principles
-----------------
- **A fixed, explicit list, not a prefix match.** Adversarial case ids group by subtype
  (``adv-injection-*``, ``adv-poisoned-*``, ``adv-unauthorized-*``), but naming the 16 ids means a
  case added under the same prefix does not silently change what the smoke job runs.
- **Fails loudly if the golden set changes underneath it.** ``smoke_cases`` raises if any id in
  ``SMOKE_CASE_IDS`` no longer resolves, rather than running fewer cases than the job is meant to
  cover.

Runtime Contract
-----------------
``SMOKE_CASE_IDS: frozenset[str]``.
``smoke_cases() -> tuple[Case, ...]``, in ``evals.golden.adversarial.CASES`` order (a ``frozenset``
has no stable iteration order to preserve).

Limitations
-----------
Cases are drawn only from ``evals.golden.adversarial``, where every injection and
unauthorized-access case lives; a smoke case from another category module would need its own
import and a change to ``smoke_cases``.
"""

from __future__ import annotations

from evals.golden.adversarial import CASES as ADVERSARIAL_CASES
from evals.models import Case

#: The 16 smoke cases: every `adv-injection-*` and `adv-poisoned-*` case (10, prompt injection
#: through a user message and through a poisoned data field) and every `adv-unauthorized-*`
#: case (6).
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
