"""
Golden Set: Human-required Category
====================================

Overview
--------
The 22 human-required cases of the golden set's category mix (10 Spanish, 8 Portuguese, 4
English): a fraud claim, an amount at or above the routing threshold, a repeat complainer, and a
customer who asks for a person outright. Each of the first three subtypes exercises one routing
rule of `app.domain.policy.engine` in isolation; the fourth exercises the direct handoff request
that never reaches the policy engine at all.

Scope
-----
In: the 22 `Case` records and the real `data/gold/ops_seed` rows they are grounded in.
Out: the other five category groups (their own modules); running or scoring these cases.

Design Principles
-------------------
- **One routing rule per case, provably isolated.** Every amount stays under the escalation
  threshold except the amount-threshold subtype's own cases, which stay within their category's
  filing window so the routing trigger — not an expired window — is what fires; no case's
  customer is flagged a repeat complainer except the repeat-complainer subtype's own four.
- **Grounded in real seeded rows.** Every `seed_ref` is a real `transaction_id` from
  `data/gold/ops_seed/transactions.parquet`, joined against an Active customer and an in-scope
  product; the repeat-complainer cases are further grounded in a real `is_repeat_complainer` flag
  from `data/silver/silver/complaints.parquet`, latest complaint on or before the reference date.
  No case in this module needs `data/gold/eval_bank` (unlike the adversarial category).
- **Provenance is `team_generated` throughout.** The transaction each case is grounded in is real;
  the customer's wording is not — the source call-transcript data carries no dispute language at
  all, so no case in the golden set can honestly claim `observed` wording. `team_generated` is the
  honest label for every case here.
- **Two scripted turns.** An opening line naming the transaction the way a customer would (date,
  amount in the transaction's own currency, merchant when one exists — never an internal
  identifier), then a second line that supplies the one fact the routing rule turns on: naming it
  fraud, confirming no explanation, or asking for a person directly.

Runtime Contract
-----------------
`CASES`: the 22 `Case` records, in the fixed order fraud claim, amount-threshold, repeat
complainer, then ask-for-human.

Limitations
-----------
The reference date is fixed at 2026-06-18 (the day after the newest seeded transaction); every
case's transaction date was chosen to fall inside its dispute category's filing window as of that
date. The "ask for a person" subtype declares no `expected_reason_code` because it is a direct
routing decision, not a policy-engine outcome, so the case never selects a dispute category.
"""

from __future__ import annotations

# Local modules
from app.domain.policy.models import ReasonCode  # Expected policy-engine reason for each subtype
from contracts.service_v1.envelope import Intent  # Expected reply intent: HANDOFF throughout
from evals.models import Case, CaseCategory  # The record shape and its category vocabulary

# -----------------------------------------------------------------------------
# Fraud claim — always escalates (app.domain.policy.engine's one deliberate exception)
# -----------------------------------------------------------------------------

_FRAUD_CLAIM_CASES = (
    Case(
        case_id="hr-fraud-es-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0188IO327FJNGIR0OCVS",
        user_turns=(
            "No reconozco una transferencia de $2.623,10 dólares que salió de mi cuenta"
            " el 6 de mayo.",
            "No fui yo quien la hizo, alguien más tuvo acceso a mi cuenta."
            " Quiero reportarlo como fraude.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
        description=(
            "Fraud claim on a real $2,623.10 transfer; always escalates regardless of amount."
        ),
    ),
    Case(
        case_id="hr-fraud-es-02",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-036JBH9C5ALAEJFRH7TG",
        user_turns=(
            "Detecté un retiro de $449,94 dólares el 19 de abril que yo no realicé.",
            "Es un fraude, no reconozco ese retiro para nada.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
        description="Fraud claim on a small $449.94 withdrawal; amount alone would not escalate.",
    ),
    Case(
        case_id="hr-fraud-es-03",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-065GGE109KK3G9UQWP91",
        user_turns=(
            "Hay una compra de $413,83 dólares en Ferretería el 31 de marzo que no hice.",
            "Sí, quiero denunciarlo como fraude, alguien está usando mi tarjeta.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
        description=(
            "Fraud claim naming a merchant; the merchant name is real, from the seeded row."
        ),
    ),
    Case(
        case_id="hr-fraud-pt-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-07SHPFP4XUCYHT2UM8OK",
        user_turns=(
            "Não reconheço uma compra de $471,19 dólares na Servicios Públicos no dia 28 de abril.",
            "Não fui eu, quero reportar como fraude.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
        description="Portuguese fraud claim, team-generated wording per the module's Limitations.",
    ),
    Case(
        case_id="hr-fraud-pt-02",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0D01M664N5G5XX3TVZDU",
        user_turns=(
            "Vi um saque de $208,93 dólares no dia 20 de fevereiro que não fiz.",
            "É fraude, alguém usou minha conta sem autorização.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
        description="Portuguese fraud claim on a withdrawal, well under the amount threshold.",
    ),
    Case(
        case_id="hr-fraud-en-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0XTPP8572WL9CI8Y6AAK",
        user_turns=(
            "I see a transfer of $2,727.12 from my account on June 14 that I never made.",
            "This is fraud, I didn't authorize that transfer at all.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_FRAUD_CLAIM,
        description="English fraud claim on a recent transfer, within days of the reference date.",
    ),
)

# -----------------------------------------------------------------------------
# Amount at or above the routing threshold ($5,000.00 USD, policy/dispute_policy_v1.yaml)
# -----------------------------------------------------------------------------

_AMOUNT_THRESHOLD_CASES = (
    Case(
        case_id="hr-amt-es-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0BOC7L84MTPZHPTDV50T",
        user_turns=(
            "No reconozco una transferencia de $27.556.276,44 pesos colombianos"
            " que salió el 27 de mayo.",
            "No sé qué fue eso, quiero reportarlo porque yo no la autoricé.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        description=(
            "COP transfer converting to about $6,889 USD, above the $5,000 routing threshold."
        ),
    ),
    Case(
        case_id="hr-amt-es-02",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0FG4JQRDW0EHMI78UVLZ",
        user_turns=(
            "Hay una transferencia de $23.689.228,51 pesos colombianos del 5 de abril"
            " que no reconozco.",
            "Quiero que revisen esa transferencia, no fui yo.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        description="Another COP transfer above threshold once converted (about $5,922 USD).",
    ),
    Case(
        case_id="hr-amt-es-03",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0QPX6D3ND3145O7OCC1X",
        user_turns=(
            "No reconozco una transferencia de $7.060,30 dólares realizada el 17 de marzo.",
            "No autoricé ese movimiento, quiero reportarlo.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        description=(
            "Transfer already stated in USD, well above the threshold, no conversion involved."
        ),
    ),
    Case(
        case_id="hr-amt-pt-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1EX263NHIQK7Z8LLT1EM",
        user_turns=(
            "Não reconheço uma transferência de $8.266,77 dólares feita no dia 11 de março.",
            "Não fui eu que autorizei essa transferência.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        description="Portuguese amount-threshold case, USD-denominated transfer above $5,000.",
    ),
    Case(
        case_id="hr-amt-pt-02",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-225654LKSPFRB1W2JPKC",
        user_turns=(
            "Vejo uma transferência de $35.360.662,46 pesos colombianos no dia 17 de abril"
            " que não reconheço.",
            "Quero reportar essa transferência, não fui eu.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        description=(
            "Portuguese amount-threshold case, COP transfer converting to about $8,840 USD."
        ),
    ),
    Case(
        case_id="hr-amt-en-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2AZPYPQ5GIOGR6652R6Q",
        user_turns=(
            "I don't recognize a transfer of $7,767.96 made on February 20.",
            "I didn't authorize that transfer, I'd like to report it.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_AMOUNT_ABOVE_THRESHOLD,
        description=(
            "English amount-threshold case, oldest transaction date still inside the window."
        ),
    ),
)

# -----------------------------------------------------------------------------
# Repeat complainer — customer's latest complaint on or before the reference date carries the flag
# -----------------------------------------------------------------------------

_REPEAT_COMPLAINER_CASES = (
    Case(
        case_id="hr-repeat-es-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-T25LT50H8NVM6Z51N0QA",
        user_turns=(
            "No reconozco un retiro de $103,19 dólares del 22 de mayo.",
            "No fui yo, quiero reportarlo.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_REPEAT_COMPLAINER,
        description=(
            "Small withdrawal, well under threshold; escalates only on the repeat-complainer flag."
        ),
    ),
    Case(
        case_id="hr-repeat-es-02",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-R2098P3GT0X3I84Z617M",
        user_turns=(
            "Hay un retiro de $223,47 dólares del 11 de junio que no hice.",
            "Quiero reportar ese retiro, no lo reconozco.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_REPEAT_COMPLAINER,
        description="Second repeat-complainer case, transaction close to the reference date.",
    ),
    Case(
        case_id="hr-repeat-pt-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-D43EMTM35IBKOLCXEDOI",
        user_turns=(
            "Não reconheço uma compra de $228,86 dólares na Tienda Don José no dia 16 de abril.",
            "Não fui eu, quero reportar essa compra.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_REPEAT_COMPLAINER,
        description="Portuguese repeat-complainer case naming the real seeded merchant.",
    ),
    Case(
        case_id="hr-repeat-en-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-8SET45HZHYHYF4K63X7Z",
        user_turns=(
            "I see a withdrawal of $117.49 on June 6 that I didn't make.",
            "I'd like to report that withdrawal, it wasn't me.",
        ),
        expected_intent=Intent.HANDOFF,
        expected_reason_code=ReasonCode.ESCALATE_REPEAT_COMPLAINER,
        description=(
            "English repeat-complainer case, transaction just days before the reference date."
        ),
    ),
)

# -----------------------------------------------------------------------------
# User asks for a human directly — never reaches the policy engine
# -----------------------------------------------------------------------------

_ASK_FOR_HUMAN_CASES = (
    Case(
        case_id="hr-human-es-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0LDA82PDF08CQKQL6A92",
        user_turns=(
            "Tengo un problema con un pago de $4.359.741,38 pesos colombianos del 16 de junio.",
            "Prefiero hablar con una persona directamente, no quiero seguir con el asistente.",
        ),
        expected_intent=Intent.HANDOFF,
        description="Direct request for a human; no dispute category is ever selected.",
    ),
    Case(
        case_id="hr-human-es-02",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0LJIMPPCVQSY5R8A4QVU",
        user_turns=(
            "No reconozco una compra en Boutique Moda del 25 de febrero por $113,32 dólares.",
            "Quiero hablar con un agente humano sobre esto, por favor.",
        ),
        expected_intent=Intent.HANDOFF,
        description=(
            "Customer opens with a dispute-shaped statement, then asks for a person outright."
        ),
    ),
    Case(
        case_id="hr-human-pt-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0PNKLH73M0PENRBW48N8",
        user_turns=(
            "Tenho uma dúvida sobre um saque de $105,24 dólares no dia 9 de abril.",
            "Prefiro falar com uma pessoa, pode me transferir para um atendente?",
        ),
        expected_intent=Intent.HANDOFF,
        description="Portuguese direct handoff request framed as a question, not a dispute.",
    ),
    Case(
        case_id="hr-human-pt-02",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0QJWDZO5JSNG6BFARC1Q",
        user_turns=(
            "Vi uma compra na Estación de Servicio no dia 7 de março de $177,58 dólares.",
            "Quero falar com um atendente humano sobre isso.",
        ),
        expected_intent=Intent.HANDOFF,
        description="Portuguese direct handoff request naming the real seeded merchant.",
    ),
    Case(
        case_id="hr-human-pt-03",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0R5862CVGF1QR0ZCE4Q0",
        user_turns=(
            "Há uma compra na Super Ahorro do dia 30 de maio de $357,72 dólares"
            " que quero entender melhor.",
            "Pode me passar para uma pessoa? Prefiro não continuar com o assistente automático.",
        ),
        expected_intent=Intent.HANDOFF,
        description=(
            "Portuguese direct handoff request, explicit about avoiding the automated flow."
        ),
    ),
    Case(
        case_id="hr-human-en-01",
        category=CaseCategory.HUMAN_REQUIRED,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-0TMP2LJHQY2YDJD0LPKN",
        user_turns=(
            "I have a question about a payment of 4,888,502.32 Colombian pesos on May 24.",
            "Can you transfer me to a human agent instead? I'd rather speak with a person.",
        ),
        expected_intent=Intent.HANDOFF,
        description="English direct handoff request on a COP-denominated payment.",
    ),
)

#: All 22 human-required cases, in the fixed subtype order the module's docstring states.
CASES: tuple[Case, ...] = (
    _FRAUD_CLAIM_CASES + _AMOUNT_THRESHOLD_CASES + _REPEAT_COMPLAINER_CASES + _ASK_FOR_HUMAN_CASES
)
