"""
Golden Set: Ambiguous Category
==============================

Overview
--------
The ambiguous cases of the golden set: 17 cases (8 Spanish, 6 Portuguese, 3 English) with a vague
transaction reference, a message missing both the transaction and the reason, or a message whose
intent could plausibly be a dispute or something else. Every case has the same correct reply, a
clarifying question rather than a guess, so every case declares `expected_intent=Intent.CLARIFY`
and no `expected_reason_code`.

Scope
-----
In: the 17 `Case` records and, for the vague-transaction-reference subtype, the `data/gold/ops_seed`
customers whose ambiguity rests on an actual pair of close-together transactions.
Out: the other category modules; running or scoring these cases.

Design Principles
-----------------
- **Every `seed_ref` names a customer, not a transaction** (`ops_seed:CLI-...`, as in
  `evals.golden.normal`'s policy-answer cases): an ambiguous case exists because no single
  transaction is identified yet, so anchoring it to one would contradict its premise.
- **A vague reference rests on real data.** Each of the 6 vague-transaction-reference cases names
  a customer verified to hold at least two eligible transactions within days of each other
  (checked against `data/gold/ops_seed/transactions.parquet`), so the ambiguity is genuine.
- **One scripted turn.** A second turn could resolve the ambiguity the case exists to test, so
  every case stops after the customer's single, deliberately under-specified opening line.
- **Provenance is `team_generated`**: the source call-transcript data carries no dispute language.

Runtime Contract
-----------------
`CASES`: the 17 `Case` records, vague-transaction-reference first, then missing-slots, then
two-plausible-intents.

Limitations
-----------
The two-transaction grounding is a property of the seeded data and is not re-checked against a
moving reference date.
"""

from __future__ import annotations

from contracts.service_v1.envelope import Intent  # Expected reply intent: CLARIFY throughout
from evals.models import Case, CaseCategory  # The record shape and its category vocabulary

# -----------------------------------------------------------------------------
# Vague transaction reference — the customer's own real data holds >= 2 close, matching candidates
# -----------------------------------------------------------------------------

_VAGUE_TRANSACTION_REFERENCE_CASES = (
    Case(
        case_id="amb-vague-es-01",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1KBO0R10KJX3",
        user_turns=(
            "Hice una transferencia hace poco desde mi cuenta de ahorros y quiero disputarla.",
        ),
        expected_intent=Intent.CLARIFY,
        description=(
            "Vague reference; the customer has two real transfers/withdrawals four days apart"
            " (2026-05-24, 2026-05-28) that both match."
        ),
    ),
    Case(
        case_id="amb-vague-es-02",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1OZDAODK2YXA",
        user_turns=("Tuve un cargo en mi tarjeta de débito la semana pasada que quiero reportar.",),
        expected_intent=Intent.CLARIFY,
        description=(
            "Vague reference; two real debit-card transactions a week apart"
            ' (2026-04-01, 2026-04-08) both match "tarjeta de débito".'
        ),
    ),
    Case(
        case_id="amb-vague-es-03",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-6SPU6GRCV3HW",
        user_turns=("Hay un cobro en mi tarjeta de crédito de hace unos días que no me cuadra.",),
        expected_intent=Intent.CLARIFY,
        description=(
            "Vague reference; two real credit-card transactions one day apart"
            " (2026-04-24, 2026-04-25) both match."
        ),
    ),
    Case(
        case_id="amb-vague-pt-01",
        category=CaseCategory.AMBIGUOUS,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-72568SLIAT8L",
        user_turns=("Fiz uma movimentação na minha conta esta semana e quero contestar.",),
        expected_intent=Intent.CLARIFY,
        description=(
            "Vague reference; two real, recent transactions four days apart"
            ' (2026-06-05, 2026-06-09) both match "esta semana".'
        ),
    ),
    Case(
        case_id="amb-vague-pt-02",
        category=CaseCategory.AMBIGUOUS,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-EF46WNLUD6R3",
        user_turns=("Tive uma cobrança na minha conta há alguns dias que quero contestar.",),
        expected_intent=Intent.CLARIFY,
        description=(
            "Vague reference; two real transactions five days apart"
            " (2026-02-28, 2026-03-05) both match."
        ),
    ),
    Case(
        case_id="amb-vague-en-01",
        category=CaseCategory.AMBIGUOUS,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-G2FCWRPILRNJ",
        user_turns=("I had a charge on my credit card recently that I want to dispute.",),
        expected_intent=Intent.CLARIFY,
        description=(
            "Vague reference; two real credit-card transactions six days apart"
            ' (2026-05-02, 2026-05-08) both match "recently".'
        ),
    ),
)

# -----------------------------------------------------------------------------
# Missing slots — neither the transaction nor the dispute reason is stated
# -----------------------------------------------------------------------------

_MISSING_SLOTS_CASES = (
    Case(
        case_id="amb-missing-es-01",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-0Z7H89O49MR1",
        user_turns=("Quiero reportar un problema con una transacción.",),
        expected_intent=Intent.CLARIFY,
        description="Missing slots: no transaction reference and no stated reason.",
    ),
    Case(
        case_id="amb-missing-es-02",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-18ONDBXWOVS3",
        user_turns=("Tengo un cargo que quiero disputar.",),
        expected_intent=Intent.CLARIFY,
        description="Missing slots: no date, amount or merchant identifies which charge.",
    ),
    Case(
        case_id="amb-missing-es-03",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1B7L6K4QKJXP",
        user_turns=("Necesito hacer una reclamación sobre un movimiento en mi cuenta.",),
        expected_intent=Intent.CLARIFY,
        description="Missing slots: no product, transaction or reason named.",
    ),
    Case(
        case_id="amb-missing-pt-01",
        category=CaseCategory.AMBIGUOUS,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1DG6B3LCNIQT",
        user_turns=("Quero contestar uma cobrança.",),
        expected_intent=Intent.CLARIFY,
        description="Missing slots, Portuguese: no transaction reference given at all.",
    ),
    Case(
        case_id="amb-missing-pt-02",
        category=CaseCategory.AMBIGUOUS,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1DWL4M2DDTUH",
        user_turns=("Tenho um problema com uma transação e quero resolver isso.",),
        expected_intent=Intent.CLARIFY,
        description="Missing slots, Portuguese: neither the transaction nor the reason is stated.",
    ),
    Case(
        case_id="amb-missing-en-01",
        category=CaseCategory.AMBIGUOUS,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1EZQYXUPM5AS",
        user_turns=("I want to dispute a charge.",),
        expected_intent=Intent.CLARIFY,
        description="Missing slots, English: the shortest possible under-specified opener.",
    ),
)

# -----------------------------------------------------------------------------
# Two plausible intents — the message could be a dispute, a status check or something else
# -----------------------------------------------------------------------------

_TWO_PLAUSIBLE_INTENTS_CASES = (
    Case(
        case_id="amb-twointent-es-01",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1JBEXH6YWCQV",
        user_turns=("Tengo un problema con mi tarjeta de crédito.",),
        expected_intent=Intent.CLARIFY,
        description=(
            "Could mean a dispute, a lost/stolen card report, or a policy question about the card."
        ),
    ),
    Case(
        case_id="amb-twointent-es-02",
        category=CaseCategory.AMBIGUOUS,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1JTQNH5R9RCN",
        user_turns=("Algo pasó con un pago que hice.",),
        expected_intent=Intent.CLARIFY,
        description="Could mean filing a new dispute or asking the status of one already filed.",
    ),
    Case(
        case_id="amb-twointent-pt-01",
        category=CaseCategory.AMBIGUOUS,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1PPBSFBB7B3J",
        user_turns=("Tenho uma questão sobre minha conta corrente.",),
        expected_intent=Intent.CLARIFY,
        description="Could mean a dispute, a general account question, or a policy question.",
    ),
    Case(
        case_id="amb-twointent-pt-02",
        category=CaseCategory.AMBIGUOUS,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1V70U7BEFD8L",
        user_turns=("Preciso falar sobre uma cobrança na minha conta.",),
        expected_intent=Intent.CLARIFY,
        description="Could mean filing a dispute or checking the status of an existing charge.",
    ),
    Case(
        case_id="amb-twointent-en-01",
        category=CaseCategory.AMBIGUOUS,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-1WVH79WJTDZ3",
        user_turns=("Something's wrong with a transaction on my account.",),
        expected_intent=Intent.CLARIFY,
        description="Could mean a dispute, a fraud report, or an unrelated account question.",
    ),
)

#: All 17 ambiguous cases: vague reference, then missing slots, then two plausible intents.
CASES: tuple[Case, ...] = (
    _VAGUE_TRANSACTION_REFERENCE_CASES + _MISSING_SLOTS_CASES + _TWO_PLAUSIBLE_INTENTS_CASES
)
