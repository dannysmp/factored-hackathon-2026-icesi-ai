"""
Golden Set: Multilingual Category
===================================

Overview
--------
The 10 multilingual-ambiguity cases of the golden set's category mix: code-switching between
Spanish and Portuguese (4), between English and Spanish (3), and accent-flavored Spanish
phrasing — Mexican, Colombian and Argentine (3). Every case is a policy question, so the only
thing under test is the system's language handling, not its dispute-filing or clarification
logic (already covered by the normal and ambiguous categories).

Scope
-----
In: the 10 `Case` records.
Out: the other five category groups (their own modules); running or scoring these cases.

Design Principles
-------------------
- **What `lang` means here, ruled by the architect (2026-09-27) for this exact question, first
  flagged as open when the case schema was reviewed.** `lang` names the language a
  correct reply must render in for the whole conversation — the conversation's sticky language,
  per `plan/docs/architecture.md`'s dialogue-state design (the controller persists one language
  per session, not per turn) and ADR-16's language-routing clause ("a miss abstains and offers
  the language switch; it never returns a hit from the other language's corpus"). `lang` is never
  the dominant or opening language by a mechanical rule; it is the language a correct system
  should lock onto and stay in, decided per case and recorded in `description`. For a
  code-switching case, that is normally the language the customer's turn opens in (the language
  NLU should lock onto first); for an accent-flavored case it is simply that accent's language.
- **The customer's wording code-switches freely; `lang` does not.** `user_turns` mixes Spanish,
  Portuguese and English within a single turn where the subtype calls for it; `lang` stays fixed
  at whichever value the case's own reasoning (in `description`) establishes.
- **Every case is a policy question**, matching `evals.golden.normal`'s policy-answer convention:
  `seed_ref` names a customer only, `expected_intent=Intent.POLICY_ANSWER`, no
  `expected_reason_code`. This isolates language handling as the single thing scored, the same
  way `evals.golden.ambiguous` isolates clarification behavior.

Runtime Contract
-----------------
`CASES`: the 10 `Case` records, Spanish/Portuguese code-switching first, then English/Spanish,
then accent-flavored Spanish.
"""

from __future__ import annotations

from contracts.service_v1.envelope import Intent  # Expected reply intent: POLICY_ANSWER throughout
from evals.models import Case, CaseCategory  # The record shape and its category vocabulary

# -----------------------------------------------------------------------------
# Code-switching: Spanish and Portuguese mixed in one turn
# -----------------------------------------------------------------------------

_ES_PT_CASES = (
    Case(
        case_id="multi-espt-01",
        category=CaseCategory.MULTILINGUAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3CUI4FEZFBQM",
        user_turns=("¿Cuántos días tenho para contestar um cargo que no reconozco en mi tarjeta?",),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Opens in Spanish ("¿Cuántos días"), code-switches into Portuguese words'
            ' ("tenho", "contestar", "um cargo") mid-sentence; the system should lock onto'
            " Spanish, the opening language, and reply in it. Grounded in the filing-windows"
            " section (unrecognized charge, 120 days)."
        ),
    ),
    Case(
        case_id="multi-espt-02",
        category=CaseCategory.MULTILINGUAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3KHQR5PGLSCE",
        user_turns=(
            "Quantos dias tenho para reportar, eh, un cargo que no reconozco en mi tarjeta?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Opens in Portuguese ("Quantos dias tenho"), switches into Spanish'
            ' ("un cargo que no reconozco") mid-turn; the system should lock onto Portuguese,'
            " the opening language, and reply in it. Same filing-windows section as"
            " multi-espt-01, mirrored to the other opening language."
        ),
    ),
    Case(
        case_id="multi-espt-03",
        category=CaseCategory.MULTILINGUAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3L2193M91XBS",
        user_turns=("¿Puedo contestar uma transferência hecha desde mi conta de ahorros?",),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            "Opens in Spanish, code-switches into Portuguese words for the transaction itself."
            " Grounded in the who-can-dispute section."
        ),
    ),
    Case(
        case_id="multi-espt-04",
        category=CaseCategory.MULTILINGUAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3LZYXZ9296GP",
        user_turns=("Preciso saber, ¿cuánto tiempo tenho para apresentar uma disputa por fraude?",),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Opens in Portuguese, switches into Spanish ("¿cuánto tiempo") and back.'
            " Grounded in the fraud-claims section (180 days)."
        ),
    ),
)

# -----------------------------------------------------------------------------
# Code-switching: English and Spanish mixed in one turn
# -----------------------------------------------------------------------------

_EN_ES_CASES = (
    Case(
        case_id="multi-enes-01",
        category=CaseCategory.MULTILINGUAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3MVX9UDFTFOJ",
        user_turns=(
            "How many days do I have, o sea, cuántos días tengo to report a duplicate charge?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Opens in English, switches into Spanish ("o sea, cuántos días tengo") mid-turn;'
            " the system should lock onto English. Grounded in the filing-windows section"
            " (duplicate charge, 60 days)."
        ),
    ),
    Case(
        case_id="multi-enes-02",
        category=CaseCategory.MULTILINGUAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3OFDD68FE4MT",
        user_turns=("Necesito saber, I mean, when does a person review my dispute?",),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Opens in Spanish, switches into English ("I mean, when does...") mid-turn;'
            " the system should lock onto Spanish. Grounded in the human-review section."
        ),
    ),
    Case(
        case_id="multi-enes-03",
        category=CaseCategory.MULTILINGUAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3P461TJ1FOWP",
        user_turns=(
            "What do I need ready, o los documentos que necesito, if I didn't receive"
            " a service I paid for?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            "Opens in English, code-switches into Spanish mid-sentence for the clarifying"
            " aside. Grounded in the evidence section (service not received)."
        ),
    ),
)

# -----------------------------------------------------------------------------
# Accent-flavored Spanish: Mexican, Colombian and Argentine phrasing
# -----------------------------------------------------------------------------

_ACCENT_FLAVORED_CASES = (
    Case(
        case_id="multi-accent-mx-01",
        category=CaseCategory.MULTILINGUAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3QPF8RPO0SLH",
        user_turns=(
            "Oye, ¿qué onda con cuántos días tengo pa' reportar un cargo que no reconozco?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Mexican colloquial phrasing ("qué onda", "pa\'"); tests that regional wording'
            " doesn't break intent or language detection. Grounded in the filing-windows"
            " section."
        ),
    ),
    Case(
        case_id="multi-accent-co-01",
        category=CaseCategory.MULTILINGUAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3S9S7W10MRQK",
        user_turns=(
            "Ey parcero, ¿cuánto tiempo tengo pa' meter una disputa por un fraude, oíste?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Colombian colloquial phrasing ("parcero", "oíste"). Grounded in the'
            " fraud-claims section."
        ),
    ),
    Case(
        case_id="multi-accent-ar-01",
        category=CaseCategory.MULTILINGUAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-3WYQWFKO9AN2",
        user_turns=(
            "Che, ¿vos sabés cuánto tiempo tengo para presentar una disputa"
            " por un monto incorrecto?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        description=(
            'Argentine voseo phrasing ("che", "vos sabés"). Grounded in the response-time'
            " section (wrong amount, 3 days)."
        ),
    ),
)

#: All 10 multilingual cases: es/pt code-switching, then en/es, then accent-flavored Spanish.
CASES: tuple[Case, ...] = _ES_PT_CASES + _EN_ES_CASES + _ACCENT_FLAVORED_CASES
