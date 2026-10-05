# Demonstration scripts

Seven scripted conversations: the three demonstration paths — normal, ambiguous or unsupported,
and human-required — each in Spanish and Portuguese, plus an English variant of the normal path.
Every system reply below is the template renderer's actual output for the transaction and
customer wording shown, not an approximation of it: each conversation was produced by running
`render()` from `app.conversation.renderer` against the recorded envelopes in
`tests/fixtures/scripted_flows.py`, the same fixtures the renderer's own test suite runs against.
The customer's transaction, merchant and reference date are synthetic, invented for this
demonstration; the reply wording, formatting and reference-date line are the deterministic,
grounded output of the real system.

The reference date of the data shown throughout is June 18, 2026, and every reply carries that
line, unchanged, as the system does on every turn.

Each script names the intent and template the reply came from, so a reader can trace any line back
to `app/conversation/renderer.py` and confirm it was not written by hand for this document.

## How these scripts are produced

The customer's lines are authored to be natural and to drive the
conversation through the same sequence of intents the scripted-flow fixtures record; the system's
lines are the renderer's real, unedited output. A browser recording that plays out these same
paths against the customer chat's live turn endpoint is made separately; these scripts are the
demonstration's script, not its recording.

---

## 1. Normal path in Spanish: filing an eligible dispute

| Turn | Speaker | Line |
|---|---|---|
| 1 | Customer | Hola, hay un cargo en mi tarjeta que no reconozco. |
| 1 | System (`clarify` / `clarify_transaction`) | ¿Podría decirme el comercio, el monto o la fecha del cargo que quiere disputar? |
| 2 | Customer | Fue en Tienda Sol, creo que fueron 250 pesos, el 12 de junio. |
| 2 | System (`present_transactions` / `present_one`) | Encontré una transacción de 250,00 MXN en Tienda Sol el 12 de junio de 2026. ¿Es esta la que quiere disputar? |
| 3 | Customer | Sí, esa es. |
| 3 | System (`clarify` / `clarify_reason`) | ¿Cuál es el motivo? Puede ser un cargo no reconocido, un cargo duplicado, un monto incorrecto, un servicio no recibido o un fraude. |
| 4 | Customer | No reconozco ese cargo, nunca compré ahí. |
| 4 | System (`confirm_filing` / `confirm_filing`) | Voy a presentar una disputa por cargo no reconocido sobre el cargo de 250,00 MXN del 12 de junio de 2026. Un asesor la revisará; esto no garantiza un resultado. ¿Confirma que desea presentarla? |
| 5 | Customer | Sí, confirmo. |
| 5 | System (`filing_result` / `filing_result`) | Su disputa quedó registrada con el número de caso D-2001. Espere una primera respuesta antes del 25 de junio de 2026. |
| 6 | Customer | Gracias, eso es todo. |
| 6 | System (`farewell` / `farewell`) | Gracias por escribir. Que tenga un buen día. |

Every turn also carries: `Fecha de referencia de los datos: 18 de junio de 2026`.

**What this demonstrates:** slot collection across turns (merchant, amount, date), presenting one
matching transaction and asking for confirmation before treating it as selected, collecting the
dispute reason as a closed category, filing only after explicit confirmation, verifying the write
before reporting it (`D-2001` is read back, not merely echoed), and stating the concrete
first-response date computed from policy, not invented by the model.

## 2. Normal path in Portuguese: filing an eligible dispute

| Turn | Speaker | Line |
|---|---|---|
| 1 | Customer | Olá, tem uma cobrança no meu cartão que eu não reconheço. |
| 1 | System (`clarify` / `clarify_transaction`) | Você poderia me dizer o estabelecimento, o valor ou a data da cobrança que quer contestar? |
| 2 | Customer | Foi na Tienda Sol, acho que foram 250 pesos, no dia 12 de junho. |
| 2 | System (`present_transactions` / `present_one`) | Encontrei uma transação de 250,00 MXN em Tienda Sol no dia 12 de junho de 2026. É esta que você quer contestar? |
| 3 | Customer | Sim, é essa. |
| 3 | System (`clarify` / `clarify_reason`) | Qual é o motivo? Pode ser uma cobrança não reconhecida, uma cobrança em duplicidade, um valor incorreto, um serviço não recebido ou uma fraude. |
| 4 | Customer | Não reconheço essa cobrança, nunca comprei lá. |
| 4 | System (`confirm_filing` / `confirm_filing`) | Vou apresentar uma contestação por cobrança não reconhecida sobre a cobrança de 250,00 MXN do dia 12 de junho de 2026. Um atendente vai analisar o pedido; isso não garante um resultado. Você confirma que quer apresentá-la? |
| 5 | Customer | Sim, confirmo. |
| 5 | System (`filing_result` / `filing_result`) | Sua contestação foi registrada com o número do caso D-2001. Você deve receber uma primeira resposta até 25 de junho de 2026. |
| 6 | Customer | Obrigado, é só isso. |
| 6 | System (`farewell` / `farewell`) | Agradeço o contato. Tenha um bom dia. |

Every turn also carries: `Data de referência dos dados: 18 de junho de 2026`.

**What this demonstrates:** the same path in Portuguese, with no wording borrowed from Spanish
(the merchant name is introduced with the Portuguese preposition "em", not the Spanish "en") and
its own, independently written phrasing for every reply — Portuguese is a full reply language, not
a translation applied at render time.

## 3. Normal path in English: filing an eligible dispute

| Turn | Speaker | Line |
|---|---|---|
| 1 | Customer | Hi, there's a charge on my card I don't recognize. |
| 1 | System (`clarify` / `clarify_transaction`) | Could you tell me the merchant, the amount or the date of the charge you want to dispute? |
| 2 | Customer | It was at Tienda Sol, I think it was 250 pesos, on June 12th. |
| 2 | System (`present_transactions` / `present_one`) | I found a transaction of 250.00 MXN at Tienda Sol on June 12, 2026. Is this the one you want to dispute? |
| 3 | Customer | Yes, that's the one. |
| 3 | System (`clarify` / `clarify_reason`) | What's the reason? It can be an unrecognized charge, a duplicate charge, a wrong amount, a service not received, or fraud. |
| 4 | Customer | I don't recognize that charge, I never bought anything there. |
| 4 | System (`confirm_filing` / `confirm_filing`) | I'll file an unrecognized charge dispute for the charge of 250.00 MXN on June 12, 2026. A person will review it; this does not guarantee an outcome. Do you confirm you want to file it? |
| 5 | Customer | Yes, I confirm. |
| 5 | System (`filing_result` / `filing_result`) | Your dispute was filed with case number D-2001. Expect a first response by June 25, 2026. |
| 6 | Customer | Thanks, that's all. |
| 6 | System (`farewell` / `farewell`) | Thank you for writing in. Have a good day. |

Every turn also carries: `Reference date of the data: June 18, 2026`.

**What this demonstrates:** English support is additional, not primary, but held to the same
grounding standard — the same case, the same case number, the same first-response date, in
correct English (including the grammatical article before the category name).

## 4. Ambiguous or unsupported path in Spanish: a request outside the system's scope

| Turn | Speaker | Line |
|---|---|---|
| 1 | Customer | Quiero aumentar el límite de mi tarjeta de crédito. |
| 1 | System (`refuse` / `refuse_unsupported`) | Eso no lo puedo hacer aquí. Puedo ayudarle a disputar una transacción, consultar un caso o explicarle la política; para eso use la sección de tarjetas de la aplicación del banco, o la paso con un asesor. |

**What this demonstrates:** a request the automation matrix names as never automated here
(a card action, not a dispute) is refused plainly, in one reply, with a concrete alternative
channel and the offer of a person — never a guess at how to raise a credit limit, and never a
stall. This line is unambiguous: it exercises only the system's unsupported-action classification,
never the separate, more urgent lost-or-stolen-card path a customer reporting a lost card would
reach instead.

## 5. Ambiguous or unsupported path in Portuguese: a request outside the system's scope

| Turn | Speaker | Line |
|---|---|---|
| 1 | Customer | Quero aumentar o limite do meu cartão de crédito. |
| 1 | System (`refuse` / `refuse_unsupported`) | Isso eu não posso fazer por aqui. Posso ajudar a contestar uma transação, consultar um caso ou explicar a política. Para isso, use a seção de cartões do aplicativo do banco, ou posso encaminhar você para um atendente. |

**What this demonstrates:** the same refusal, correctly worded in Portuguese, with the same
redirection and the same offer of a person.

## 6. Human-required path in Spanish: a fraud claim

| Turn | Speaker | Line |
|---|---|---|
| 1 | Customer | Hay un cargo que no reconozco y creo que es un fraude, alguien está usando mi tarjeta. |
| 1 | System (`handoff` / `handoff_fraud`) | Esto lo atiende un asesor de inmediato por ser un posible fraude. No prometo un resultado. Su referencia es T-100. |

**What this demonstrates:** a fraud claim always escalates to a person, regardless of the
transaction's amount or the risk model's score — the risk score only ever routes a case to human
review, it never decides the outcome, and a fraud claim escalates by category alone. The reference
number is the handoff ticket a human agent will see in the console, not a case number, since no
filing decision was made by the system here.

## 7. Human-required path in Portuguese: a fraud claim

| Turn | Speaker | Line |
|---|---|---|
| 1 | Customer | Tem uma cobrança que eu não reconheço e acho que é uma fraude, alguém está usando meu cartão. |
| 1 | System (`handoff` / `handoff_fraud`) | Como pode se tratar de fraude, um atendente cuida disso de imediato. Não posso prometer um resultado. Sua referência é T-100. |

**What this demonstrates:** the same escalation path, correctly worded in Portuguese.

---

## Running the personas

- A persona profile holds one session at a time. A second sign-in under the same profile is refused
  as "in use" until the first session ends.
- Signing out frees the profile at once. Sign out at the end of each path. Closing the tab does not
  free it: the profile stays in use until its session expires, 30 minutes for a customer and 60 for
  an agent.
- A filing made under a persona is kept, and a transaction with an open case is refused a second
  filing. If a path that files a case no longer works because it was already run, delete the
  personas' accumulated cases with `make reset-demo-personas`, which needs `DATABASE_URL` pointing
  at the database to restore.

---

## Review status

The Spanish wording review was performed on every Spanish line, and its corrections are applied in
these scripts. The Portuguese lines went through the wording check by a reviewer agent instead of a
native reader, since no native Portuguese speaker reviews this project's wording (see
`docs/limitations.md`), and its corrections are applied too.
