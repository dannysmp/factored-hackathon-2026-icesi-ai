"""
Golden Set: Normal Category
===========================

Overview
--------
The normal cases of the golden set: 41 cases (20 Spanish, 15 Portuguese, 6 English), made of 29
cases where an eligible dispute is filed and 12 where the customer asks a policy question that the
retrieval system answers directly. The filed-dispute cases span the four dispute categories that
never escalate on their own: unrecognized charge, duplicate charge, wrong amount and service not
received. Fraud claims always escalate and belong to the human-required category. Policy answers
are grounded in `policy/corpus/{lang}/dispute-policy.md`.

Scope
-----
In: the 41 `Case` records, the `data/gold/ops_seed` rows the filed-dispute cases are grounded in,
and the policy-corpus sections the policy-answer cases target.
Out: the other category modules; running or scoring these cases; the status-inquiry subtype (see
Limitations).

Design Principles
-----------------
- **Every filed-dispute case is eligible.** Its transaction is under the $5,000 routing threshold,
  belongs to a customer who is not a repeat complainer, and falls inside the filing window of the
  dispute category its scripted turns imply. This was checked against
  `data/gold/ops_seed/transactions.parquet` and `data/silver/silver/complaints.parquet`.
- **Policy answers need no transaction.** `seed_ref` for a policy-answer case names an `ops_seed`
  customer only (`ops_seed:CLI-...`, against `ops_seed:TRX-...` for filed disputes), since
  answering a policy question needs an authenticated session, not a transaction. The customer is
  distinct across every case in this module.
- **Provenance is `team_generated` throughout**: the source call-transcript data carries no
  dispute language, so no case can honestly claim observed wording.

Runtime Contract
-----------------
`CASES`: the 41 `Case` records, filed-dispute cases first (grouped by dispute category), then
policy-answer cases.

Limitations
-----------
No case covers a status inquiry about an already filed case: `data/gold/ops_seed` seeds no
pre-existing cases, so such a case would need a live filed case or a frozen one from
`data/gold/eval_bank`. The category therefore holds only filed-dispute and policy-answer cases.
"""

from __future__ import annotations

from app.domain.policy.models import (  # Expected policy-engine reason and dispute category
    DisputeCategory,
    ReasonCode,
)
from contracts.service_v1.envelope import Intent  # Expected reply intent
from evals.models import Case, CaseCategory  # The record shape and its category vocabulary

# -----------------------------------------------------------------------------
# Unrecognized charge (120-day filing window) — the default "I don't recognize this" framing
# -----------------------------------------------------------------------------

_UNRECOGNIZED_CHARGE_CASES = (
    Case(
        case_id="norm-filed-unrecognized-pt-01",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1HHR0LIVE7XTCER2ZF6J",
        user_turns=(
            "Não reconheço uma transferência de $2.763,79 dólares do dia 15 de abril.",
            "Não fui eu, quero contestar essa transferência.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description=(
            "Unrecognized-charge filing, no routing rule fires; well inside the 120-day window."
        ),
    ),
    Case(
        case_id="norm-filed-unrecognized-pt-02",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1RYZKN015R0H9EVI3MYL",
        user_turns=(
            "Não reconheço um saque de $337,58 dólares do dia 13 de maio.",
            "Não fui eu quem fez esse saque, quero contestar.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description="Unrecognized-charge filing on a withdrawal, well under the amount threshold.",
    ),
    Case(
        case_id="norm-filed-unrecognized-pt-03",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1VQS8MJL5DP3WGFK5LJ0",
        user_turns=(
            "Não reconheço uma transferência de $4.593.557,41 pesos colombianos do dia 7 de abril.",
            "Não fui eu, quero apresentar uma contestação.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description=(
            "Unrecognized-charge filing on a COP transfer, converted amount still well under"
            " threshold."
        ),
    ),
    Case(
        case_id="norm-filed-unrecognized-pt-04",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1ZMP442RZWA3HKLHD7BF",
        user_turns=(
            "Vejo um saque de $425,79 dólares do dia 1 de março que não reconheço.",
            "Não fui eu, quero contestar esse saque.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description=(
            "Unrecognized-charge filing, oldest transaction in this module still inside the window."
        ),
    ),
    Case(
        case_id="norm-filed-unrecognized-pt-05",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2996S3Z83C68UQP85IQH",
        user_turns=(
            "Não reconheço um pagamento de $551.045,30 pesos argentinos do dia 12 de maio.",
            "Não fui eu quem fez esse pagamento, quero contestar.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description="Unrecognized-charge filing on an ARS payment.",
    ),
    Case(
        case_id="norm-filed-unrecognized-pt-06",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2DQKMHW45J6XR9UDK9P7",
        user_turns=(
            "Vejo um saque de $157.976,51 pesos argentinos do dia 13 de março que não reconheço.",
            "Não fui eu, quero apresentar a contestação.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description=(
            "Unrecognized-charge filing, second-oldest transaction, still inside the window."
        ),
    ),
    Case(
        case_id="norm-filed-unrecognized-en-01",
        category=CaseCategory.NORMAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2E9T7T0KPGA3JC9A31P4",
        user_turns=(
            "I don't recognize a withdrawal of $397.56 on March 28.",
            "It wasn't me, I'd like to dispute that withdrawal.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description="English unrecognized-charge filing on a withdrawal.",
    ),
    Case(
        case_id="norm-filed-unrecognized-en-02",
        category=CaseCategory.NORMAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2FCK30A1I2H32LG4GGJ1",
        user_turns=(
            "I see a withdrawal of 350,933.94 Colombian pesos on April 7 that I don't recognize.",
            "I didn't make that withdrawal, I'd like to file a dispute.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description="English unrecognized-charge filing on a COP-denominated withdrawal.",
    ),
    Case(
        case_id="norm-filed-unrecognized-en-03",
        category=CaseCategory.NORMAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2IBAZ859LBO3PNHY6BMB",
        user_turns=(
            "I don't recognize a purchase at Cine Premium for $157.41 on May 4.",
            "That wasn't me, I'd like to report it.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description="English unrecognized-charge filing naming the real seeded merchant.",
    ),
    Case(
        case_id="norm-filed-unrecognized-en-04",
        category=CaseCategory.NORMAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2LV6GTHOG4656SDRJWMX",
        user_turns=(
            "I see a withdrawal of 98,202.78 Argentine pesos on April 3 that I don't recognize.",
            "I didn't make that withdrawal, I'd like to dispute it.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.UNRECOGNIZED_CHARGE,
        description="English unrecognized-charge filing on an ARS-denominated withdrawal.",
    ),
)

# -----------------------------------------------------------------------------
# Wrong amount (90-day filing window) — the amount charged doesn't match what was agreed
# -----------------------------------------------------------------------------

_WRONG_AMOUNT_CASES = (
    Case(
        case_id="norm-filed-wrongamt-es-01",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1878NN1PY2MDFM9Y3SKL",
        user_turns=(
            "Hice un retiro el 31 de marzo por $170.563,24 pesos argentinos.",
            "El monto no es el que acordé, quiero reportarlo por monto incorrecto.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.WRONG_AMOUNT,
        description="Wrong-amount filing on an ARS withdrawal, within the 90-day window.",
    ),
    Case(
        case_id="norm-filed-wrongamt-es-02",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-18RW8PX0XOO7J1AI5CMM",
        user_turns=(
            "Retiré dinero el 8 de mayo, salieron $31.874,78 pesos argentinos.",
            "Ese no es el monto correcto, quiero presentar una disputa.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.WRONG_AMOUNT,
        description="Wrong-amount filing, recent transaction.",
    ),
    Case(
        case_id="norm-filed-wrongamt-es-03",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1AYNYABNC5P8TK7U6KJA",
        user_turns=(
            "Compré una entrada en Conciertos Live el 28 de marzo por $10,99 dólares.",
            "Me cobraron un monto distinto al acordado, quiero reportarlo.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.WRONG_AMOUNT,
        description="Wrong-amount filing on a small purchase naming the real seeded merchant.",
    ),
    Case(
        case_id="norm-filed-wrongamt-es-04",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1JKCX9R52AUPXKPVZVYW",
        user_turns=(
            "Hice un retiro el 1 de junio por $345.138,81 pesos colombianos.",
            "El monto no coincide con lo que pedí, quiero presentar la disputa.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.WRONG_AMOUNT,
        description="Wrong-amount filing on a COP withdrawal, very recent transaction.",
    ),
    Case(
        case_id="norm-filed-wrongamt-es-05",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1L1SBG7518Y8IPOPI4SL",
        user_turns=(
            "Retiré $24,42 dólares el 24 de abril.",
            "Ese no fue el monto que solicité, quiero reportarlo.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.WRONG_AMOUNT,
        description="Wrong-amount filing, smallest transaction in this module.",
    ),
    Case(
        case_id="norm-filed-wrongamt-es-06",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1M3MF0NZ1XM6TSW88OHR",
        user_turns=(
            "Hice un retiro el 30 de mayo de $160.624,35 pesos argentinos.",
            "El monto está mal, quiero presentar una disputa por eso.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.WRONG_AMOUNT,
        description="Wrong-amount filing, transaction close to the reference date.",
    ),
    Case(
        case_id="norm-filed-wrongamt-es-07",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1OO2HNB7CVRZ46Y3XMV4",
        user_turns=(
            "Retiré dinero el 15 de mayo, fueron $165.767,95 pesos argentinos.",
            "No es el monto correcto, quiero reportarlo.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.WRONG_AMOUNT,
        description="Wrong-amount filing, last of the Spanish subset.",
    ),
)

# -----------------------------------------------------------------------------
# Duplicate charge (60-day filing window) — the same charge appears twice
# -----------------------------------------------------------------------------

_DUPLICATE_CHARGE_CASES = (
    Case(
        case_id="norm-filed-duplicate-pt-01",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1VLCMFVK93ROA4OD3GVT",
        user_turns=(
            "Fiz uma transferência de $1.277,60 dólares no dia 13 de junho.",
            "Fui cobrado duas vezes pela mesma transferência, quero reportar isso.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.DUPLICATE_CHARGE,
        description=(
            "Duplicate-charge filing on a very recent transfer, well inside the 60-day window."
        ),
    ),
    Case(
        case_id="norm-filed-duplicate-pt-02",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2SSQDF0WSJ322J0PG5MB",
        user_turns=(
            "Fiz um pagamento de $601,77 dólares no dia 12 de junho.",
            "Esse pagamento apareceu duas vezes na minha conta, quero apresentar uma contestação.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.DUPLICATE_CHARGE,
        description="Duplicate-charge filing on a payment.",
    ),
    Case(
        case_id="norm-filed-duplicate-pt-03",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2U4CAN0HJUELQYYTCY1L",
        user_turns=(
            "Fiz um saque de $175,79 dólares no dia 17 de junho.",
            "Esse saque foi descontado duas vezes, quero reportar a cobrança duplicada.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.DUPLICATE_CHARGE,
        description="Duplicate-charge filing, the most recent transaction in this module.",
    ),
    Case(
        case_id="norm-filed-duplicate-pt-04",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2HG92SOJZ74Q70B0F3AR",
        user_turns=(
            "Fiz um pagamento de $856,41 dólares no dia 21 de maio.",
            "Aparece duas vezes o mesmo pagamento na minha conta, quero contestar isso.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.DUPLICATE_CHARGE,
        description="Duplicate-charge filing on a payment closer to the edge of the 60-day window.",
    ),
    Case(
        case_id="norm-filed-duplicate-pt-05",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2T54KVZGITQQLUE5J4RL",
        user_turns=(
            "Fiz um saque de $105.207,70 pesos argentinos no dia 16 de maio.",
            "Fui cobrado duas vezes por esse saque, quero apresentar a contestação.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.DUPLICATE_CHARGE,
        description="Duplicate-charge filing on an ARS withdrawal.",
    ),
)

# -----------------------------------------------------------------------------
# Service not received (120-day filing window) — paid for something never received
# -----------------------------------------------------------------------------

_SERVICE_NOT_RECEIVED_CASES = (
    Case(
        case_id="norm-filed-service-es-01",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-13A83Z5HQ42NJZPSGVI6",
        user_turns=(
            "Compré una entrada en Teatro Nacional por $49,75 dólares el 23 de marzo.",
            "Nunca recibí el boleto ni me dejaron entrar, quiero presentar una disputa"
            " por servicio no recibido.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.SERVICE_NOT_RECEIVED,
        description=(
            "Service-not-received filing naming the real seeded merchant, oldest in this group."
        ),
    ),
    Case(
        case_id="norm-filed-service-es-02",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1409NSN4XA6WQU2X8XQH",
        user_turns=(
            "Pagué $218.779,45 pesos colombianos por una entrada en Conciertos Live"
            " el 10 de junio.",
            "El evento nunca se realizó y no me han devuelto el dinero, quiero reportarlo.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.SERVICE_NOT_RECEIVED,
        description="Service-not-received filing, event never took place.",
    ),
    Case(
        case_id="norm-filed-service-es-03",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-14F9YKO78D6VEORLQ398",
        user_turns=(
            "Hice un pago de $1.936,04 dólares el 6 de junio por un servicio.",
            "El servicio nunca se prestó, quiero presentar una disputa.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.SERVICE_NOT_RECEIVED,
        description="Service-not-received filing on a payment with no merchant on the seeded row.",
    ),
    Case(
        case_id="norm-filed-service-es-04",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1H8F95HH7HRFMKDBV4JG",
        user_turns=(
            "Compré algo en Tienda Don José por $245,54 dólares el 12 de mayo.",
            "El producto nunca llegó, quiero reportarlo como servicio no recibido.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.SERVICE_NOT_RECEIVED,
        description=(
            "Service-not-received filing naming a merchant reused from a different customer's row."
        ),
    ),
    Case(
        case_id="norm-filed-service-es-05",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-1WP2GMZCV0473FDPF0L1",
        user_turns=(
            "Pagué $96.135,17 pesos argentinos en Farmacia Salud el 15 de marzo.",
            "Nunca recibí el pedido que hice, quiero presentar la disputa.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.SERVICE_NOT_RECEIVED,
        description=(
            "Service-not-received filing on an ARS purchase, near the edge of the 120-day window."
        ),
    ),
    Case(
        case_id="norm-filed-service-es-06",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-23MZPPBH661OKIGMCI01",
        user_turns=(
            "Compré en Tienda Don José por $29.371,79 pesos argentinos el 14 de junio.",
            "No recibí lo que compré, quiero reportarlo.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.SERVICE_NOT_RECEIVED,
        description="Service-not-received filing, most recent transaction in this group.",
    ),
    Case(
        case_id="norm-filed-service-es-07",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:TRX-2E4PZ8HAB0SLB9LBDZEU",
        user_turns=(
            "Pagué unos exámenes en Laboratorio Central por $96.022,57 pesos argentinos"
            " el 1 de junio.",
            "Nunca me entregaron los resultados que pagué, quiero presentar una disputa.",
        ),
        expected_intent=Intent.CONFIRM_FILING,
        expected_reason_code=ReasonCode.ELIGIBLE,
        expected_category=DisputeCategory.SERVICE_NOT_RECEIVED,
        description=(
            "Service-not-received filing for a paid service whose result was never delivered."
        ),
    ),
)

# -----------------------------------------------------------------------------
# Grounded policy answer — no dispute filed, no transaction needed
# -----------------------------------------------------------------------------

_POLICY_ANSWER_CASES = (
    Case(
        case_id="norm-policy-es-01",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-007Q2YBOSD9N",
        user_turns=("¿Cuántos días tengo para reportar un cargo que no reconozco en mi tarjeta?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="filing-windows",
        description="Grounded in the filing-windows section (unrecognized charge, 120 days).",
    ),
    Case(
        case_id="norm-policy-es-02",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-01RJGK0AVYKO",
        user_turns=(
            "Si presento una disputa por monto incorrecto, ¿en cuánto tiempo me responden?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="response-time",
        description="Grounded in the response-time section (wrong amount, 3 days).",
    ),
    Case(
        case_id="norm-policy-es-03",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-03RRPROMFF0Y",
        user_turns=(
            "¿Qué necesito tener listo si quiero reportar que no recibí un servicio que pagué?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="evidence",
        description="Grounded in the evidence section (service not received).",
    ),
    Case(
        case_id="norm-policy-es-04",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-06GRSSYAZ5KU",
        user_turns=("¿Puedo disputar una transferencia que hice desde mi cuenta de ahorros?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="who-can-dispute",
        description="Grounded in the who-can-dispute section.",
    ),
    Case(
        case_id="norm-policy-es-05",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-07FTORND6GLN",
        user_turns=("¿Cuándo revisa una persona mi solicitud de disputa en vez del sistema?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="human-review",
        description=(
            "Grounded in the human-review section, stated qualitatively, as the corpus states it."
        ),
    ),
    Case(
        case_id="norm-policy-es-06",
        category=CaseCategory.NORMAL,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-09F7FO0LRYUI",
        user_turns=("¿Cuánto tiempo tengo para reportar un fraude en mi cuenta?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="fraud-claims",
        description="Grounded in the fraud-claims section (180 days).",
    ),
    Case(
        case_id="norm-policy-pt-01",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-0G4O4TLXL2F0",
        user_turns=("Posso contestar uma compra feita com meu cartão de débito?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="who-can-dispute",
        description="Portuguese case grounded in the who-can-dispute section.",
    ),
    Case(
        case_id="norm-policy-pt-02",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-0G4V8R44OD74",
        user_turns=("Quantos dias tenho para contestar uma cobrança duplicada?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="filing-windows",
        description=(
            "Portuguese case grounded in the filing-windows section (duplicate charge, 60 days)."
        ),
    ),
    Case(
        case_id="norm-policy-pt-03",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-0MSSRHGAKBF0",
        user_turns=("O que preciso ter em mãos para contestar um valor incorreto?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="evidence",
        description="Portuguese case grounded in the evidence section (wrong amount).",
    ),
    Case(
        case_id="norm-policy-pt-04",
        category=CaseCategory.NORMAL,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-0MTYLP24AUUJ",
        user_turns=("Antes de apresentar a contestação, o que vocês confirmam comigo?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="confirmation",
        description="Portuguese case grounded in the confirmation section.",
    ),
    Case(
        case_id="norm-policy-en-01",
        category=CaseCategory.NORMAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-0NQ3XMFEO9HH",
        user_turns=(
            "How soon will I hear back after I file a dispute for an unrecognized charge?",
        ),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="response-time",
        description=(
            "English case grounded in the response-time section (unrecognized charge, 3 days)."
        ),
    ),
    Case(
        case_id="norm-policy-en-02",
        category=CaseCategory.NORMAL,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-0VJ9H0AQO8S2",
        user_turns=("When does a person review my dispute instead of the system deciding?",),
        expected_intent=Intent.POLICY_ANSWER,
        expected_policy_section_id="human-review",
        description="English case grounded in the human-review section.",
    ),
)

#: All 41 normal cases: filed disputes by category, then policy answers.
CASES: tuple[Case, ...] = (
    _UNRECOGNIZED_CHARGE_CASES
    + _WRONG_AMOUNT_CASES
    + _DUPLICATE_CHARGE_CASES
    + _SERVICE_NOT_RECEIVED_CASES
    + _POLICY_ANSWER_CASES
)
