# Video script

A walk through the deployed system: the problem, the language switch, three paths with one
demonstration persona each, the evaluation result, four design decisions and the route to operation.
The total is about three minutes twenty seconds against a target of three minutes, a length chosen
for this video; the Timing table gives the pace the narration assumes.

## Before recording

- Run `make reset-demo-personas` against the deployed database. A duplicate open case for a
  transaction is refused, so filing is only eligible again after the reset.
- A persona's sign-in is held for 30 minutes (60 for an agent). Every path below uses a different
  persona, and none of them may be used for rehearsal in the 30 minutes before the take.
- Play against the deployed link. The persona's access code is entered off screen and never shown.

## Where the lines come from

The system's replies are fixed templates filled with verified facts, so wording follows the
renderer and the figures follow the persona's own data. The scripted fixtures in
`docs/demo-scripts.md` use invented details (Tienda Sol, 250 MXN, D-2001, T-100) that no persona
has, so a persona's customer lines are written for that persona's transactions.

| Path | Persona | Customer lines | Checked against |
|---|---|---|---|
| Normal filing | Joao, Portuguese | Written for his Farmacia Salud charge; the first and third lines are those of demo script 2 | Played end to end on the deployed system |
| Unsupported request | Ana, Spanish | Demo script 4, verbatim | Played on the deployed system |
| Fraud handoff | Carlos, Spanish | Demo script 6, verbatim | Played on the deployed system |

Case and ticket numbers are read off the screen, never from this script: the deployed formats are
`CASE-` and `T-` followed by the date and eight characters.

## The problem

Narration: "In the banking dataset we were given, there are 12,297 disputed charges in 37 months,
and the cases that do close take a median of 15 days."

On screen: the figures as slide 2 of the deck states them: 12,297 disputed charges in 37 months, and
15.0 days as the median time to resolve the cases that do close.

## Opening

Narration: "This system takes a bank customer's transaction dispute, in Spanish, Portuguese or
English. The model only understands the request. Replies are fixed templates filled with verified
facts, and a deterministic policy engine decides."

On screen: the sign-in page, with the demonstration notice. Once signed in, the chat and the
console state that the session is a demonstration and show the date the data is current as of,
June 18, 2026.

## Language switch

On the sign-in page, press Español, then Português, then English. A button selects that language's
first demonstration persona, unless that language is already selected. Narration: "Spanish and
Portuguese are required; English is added. The customer simply writes in their language." End on
Português, which selects Joao for the first path.

## Path 1: normal filing, Joao in Portuguese

Customer lines, in order:

1. "Olá, tem uma cobrança no meu cartão que eu não reconheço."
2. "Foi na Farmacia Salud, no dia 21 de abril, cerca de 99.948,89 ARS."
3. "Sim, é essa."
4. "Sim, registrar"

The reason comes from the first line, so the system asks only which charge, finds one transaction and shows it in US dollars, asks for
confirmation, then files. Stop on the filed screen with the case number. Narration over the
confirmation: "Nothing is filed until the customer approves a plain-language summary, and the case
number appears only after the record is read back."

## Path 2: unsupported request, Ana in Spanish

Sign out, choose Español and sign in as Ana. Customer line: "Quiero aumentar el límite de mi
tarjeta de crédito." The system refuses plainly and offers the card section of the bank's app or a
person. Narration: "A request outside its scope is refused plainly, with a real next step."

## Path 3: human-required, Carlos in Spanish

Sign out, choose Español if it is not selected, select Carlos's card and sign in. Customer line:
"Hay un cargo que no reconozco y creo que es un fraude, alguien está usando mi tarjeta." The reply
hands the case to an advisor, promises no outcome and gives a ticket reference; show it on screen.
Switch to the agent console, signed in as agent-beatriz, find that ticket in the queue and open its
packet, with every identifier masked. Narration: "A possible fraud goes to a person, whatever the
amount. The agent sees the request, verified facts, actions taken and open questions. The console
is a viewer today."

## Results

Show the headline table of `reports/evaluation.md`, captioned as an offline measurement on
team-written cases. Narration: "On 135 team-written cases, 32 of them adversarial, among them prompt
injection, attempts to reach another customer's data and corrupted data, there were zero unsafe
outcomes. Safe automated resolution is 73.5% over the 103 cases in scope, against 33.0% and 36.9%
for the two baselines. The automated judge did not match both human raters on 80% of replies, so we
do not report its scores."

## Design decisions

Four decisions, each narrated in one sentence:

- **Decide, then render:** "A deterministic policy engine decides, with stable reason codes; the
  model never decides an outcome, and replies are filled templates."
- **Authorization in the tool layer:** "No tool accepts a customer identifier, so reaching another
  customer's data cannot even be expressed."
- **The risk model routes, policy decides:** "A risk score can only send a case to a person, never
  decide eligibility."
- **Lexical retrieval with abstention:** "Policy answers come from a small cited corpus; below a
  relevance floor, the system says so and offers a person."

## Closing

Narration: "To run this in a bank, it would need the bank's own identity, a managed database with
backups, and real conversations in each language to validate it. What is built, what is deferred
and what remains are in the limitations report. Nothing here claims more than the code does."

## Timing

The narration assumes a pace of 150 words a minute (2.5 words a second). The remaining time in each
part is for sign-ins, typing, replies and console navigation.

| Part | Seconds | Narration words | Seconds of speech |
|---|---|---|---|
| The problem | 12 | 27 | 11 |
| Opening | 15 | 33 | 13 |
| Language switch | 7 | 15 | 6 |
| Path 1 | 33 | 22 | 9 |
| Path 2 | 12 | 13 | 5 |
| Path 3 | 33 | 28 | 11 |
| Results | 30 | 65 | 26 |
| Design decisions | 40 | 69 | 28 |
| Closing | 20 | 49 | 20 |
| Total | 202 | 321 | 129 |

## Notes for the recording

- No secret value, access code, administration screen, key or unmasked identifier may appear on
  screen. A person watches the full recording before delivery, as the release checklist requires.
- The system's replies are shown on screen as the deployed system writes them.
- The fixture conversations in `docs/demo-scripts.md` cover each path in more than one language
  for a longer cut.
