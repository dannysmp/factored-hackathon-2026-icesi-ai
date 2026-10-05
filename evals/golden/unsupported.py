"""
Golden Set: Unsupported Category
================================

Overview
--------
The unsupported-request cases of the golden set: 13 cases (6 Spanish, 5 Portuguese, 2 English)
asking for banking services the dispute-intake system does not handle at all: a new transfer, a
credit-limit increase, a loan, a new account, investment advice or insurance. That differs from a
request the system understands but cannot grant. The correct reply is a safe abstention, never a
guess at what the customer meant.

Scope
-----
In: the 13 `Case` records.
Out: the other category modules; running or scoring these cases.

Design Principles
-----------------
- **Out of scope by product, not by policy.** The who-can-dispute section of
  `policy/corpus/{lang}/dispute-policy.md` states that loans, investments and insurance have their
  own channels. These cases test that the system recognizes such a request and abstains instead of
  forcing it through the dispute flow.
- **No transaction.** Every `seed_ref` names a customer only (`ops_seed:CLI-...`, as in
  `evals.golden.ambiguous`): a request is unsupported because of its topic, not the customer's
  data, so a distinct, active customer is all a case needs.
- **One scripted turn.** Each case is the customer's single opening request; the correct
  behavior, abstaining, does not depend on any follow-up detail.

Runtime Contract
-----------------
`CASES`: the 13 `Case` records, Spanish first, then Portuguese, then English.
"""

from __future__ import annotations

from contracts.service_v1.envelope import Intent  # Expected reply intent: ABSTAIN throughout
from evals.models import Case, CaseCategory  # The record shape and its category vocabulary

CASES: tuple[Case, ...] = (
    Case(
        case_id="unsup-es-01",
        category=CaseCategory.UNSUPPORTED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-24JNOSPTQNRX",
        user_turns=("Quiero hacer una transferencia a la cuenta de un familiar.",),
        expected_intent=Intent.ABSTAIN,
        description="Requesting a new transfer; not a dispute over an existing transaction.",
    ),
    Case(
        case_id="unsup-es-02",
        category=CaseCategory.UNSUPPORTED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-24VFD2M19VTQ",
        user_turns=("Necesito subir el límite de mi tarjeta de crédito.",),
        expected_intent=Intent.ABSTAIN,
        description="Credit-limit change request, outside the dispute-intake system entirely.",
    ),
    Case(
        case_id="unsup-es-03",
        category=CaseCategory.UNSUPPORTED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-26HXVICBM28N",
        user_turns=("Quiero solicitar un préstamo personal.",),
        expected_intent=Intent.ABSTAIN,
        description=(
            "Loan application; loans have their own channel per the dispute policy's scope."
        ),
    ),
    Case(
        case_id="unsup-es-04",
        category=CaseCategory.UNSUPPORTED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-27DALILLZI4Q",
        user_turns=("Quiero abrir una cuenta de ahorros nueva.",),
        expected_intent=Intent.ABSTAIN,
        description="New-account request, not a dispute over an existing product.",
    ),
    Case(
        case_id="unsup-es-05",
        category=CaseCategory.UNSUPPORTED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2E8CTWZ3Y393",
        user_turns=("¿Me pueden ayudar a invertir mi dinero?",),
        expected_intent=Intent.ABSTAIN,
        description=(
            "Investment advice; investments have their own channel per the dispute policy's scope."
        ),
    ),
    Case(
        case_id="unsup-es-06",
        category=CaseCategory.UNSUPPORTED,
        lang="es",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2G3YCLULGEB4",
        user_turns=("Necesito información sobre un seguro para mi carro.",),
        expected_intent=Intent.ABSTAIN,
        description=(
            "Insurance question; insurance has its own channel per the dispute policy's scope."
        ),
    ),
    Case(
        case_id="unsup-pt-01",
        category=CaseCategory.UNSUPPORTED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2H5FDIXG52JR",
        user_turns=("Quero fazer uma transferência para outra pessoa.",),
        expected_intent=Intent.ABSTAIN,
        description="Portuguese transfer request, not a dispute over an existing transaction.",
    ),
    Case(
        case_id="unsup-pt-02",
        category=CaseCategory.UNSUPPORTED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2JABA7OGQJL2",
        user_turns=("Preciso aumentar o limite do meu cartão de crédito.",),
        expected_intent=Intent.ABSTAIN,
        description="Portuguese credit-limit change request.",
    ),
    Case(
        case_id="unsup-pt-03",
        category=CaseCategory.UNSUPPORTED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2JMCVQXH2OLZ",
        user_turns=("Quero solicitar um empréstimo.",),
        expected_intent=Intent.ABSTAIN,
        description="Portuguese loan application.",
    ),
    Case(
        case_id="unsup-pt-04",
        category=CaseCategory.UNSUPPORTED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2N24CEP6C90T",
        user_turns=("Quero abrir uma nova conta corrente.",),
        expected_intent=Intent.ABSTAIN,
        description="Portuguese new-account request.",
    ),
    Case(
        case_id="unsup-pt-05",
        category=CaseCategory.UNSUPPORTED,
        lang="pt",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2UY854MN6WG3",
        user_turns=("Vocês podem me ajudar a investir meu dinheiro?",),
        expected_intent=Intent.ABSTAIN,
        description="Portuguese investment-advice request.",
    ),
    Case(
        case_id="unsup-en-01",
        category=CaseCategory.UNSUPPORTED,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-2UYX0QFPR3VV",
        user_turns=("I'd like to make a transfer to another person's account.",),
        expected_intent=Intent.ABSTAIN,
        description="English transfer request, not a dispute over an existing transaction.",
    ),
    Case(
        case_id="unsup-en-02",
        category=CaseCategory.UNSUPPORTED,
        lang="en",
        provenance="team_generated",
        seed_ref="ops_seed:CLI-34N9IOYSDKUE",
        user_turns=("I need to increase my credit card limit.",),
        expected_intent=Intent.ABSTAIN,
        description="English credit-limit change request.",
    ),
)
