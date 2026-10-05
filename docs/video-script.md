# Video script

A walk through the deployed system: the language switch, three paths with one demonstration persona
each, the evaluation result and four design decisions. The total is about two minutes forty-five
seconds, under the three-minute limit.

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
| Normal filing | Joao, Portuguese | Written for his Farmacia Salud charge | Played end to end on the deployed system |
| Unsupported request | Ana, Spanish | Demo script 4, verbatim | Wording of the refusal checked in the fixture; not yet played on the deployed system |
| Fraud handoff | Mariana, Portuguese | Demo script 7, verbatim | Played on the deployed system |

Case and ticket numbers are read off the screen, never from this script: the deployed formats are
`CASE-` and `T-` followed by the date and eight characters.

## Opening (about 15 seconds)

"This is a transaction-dispute intake system for a simulated bank. A customer describes a problem
with a charge in Spanish, Portuguese or English. The model only understands the request: the
language and the intent. Everything the customer reads comes from fixed templates filled with
verified facts, and what happens next is decided by a deterministic policy engine."

On screen: the sign-in page, with the demonstration notice. Once signed in, the chat and the
console state that the session is a demonstration and show the date the data is current as of,
June 18, 2026.

## Language switch (about 8 seconds)

On the sign-in page, press Español, then Português, then English. Each button selects that
language's first demonstration persona. Narrate: "The assistant works in Spanish and Portuguese,
and in English as well. A customer simply writes in their language." End on Português, which
selects Joao for the first path.

## Path 1: normal filing, Joao in Portuguese (about 35 seconds)

Customer lines, in order:

1. "Olá, tem uma cobrança no meu cartão que eu não reconheço."
2. "Foi na Farmacia Salud, no dia 21 de abril, cerca de 99.948,89 ARS."
3. "Sim, é essa."
4. "Sim, registrar"

The system asks which charge, finds one transaction and shows it in US dollars, asks for
confirmation, then files. Stop on the filed screen with the case number. Narrate
over the confirmation: "Nothing is filed until the customer approves a plain-language summary,
and the case number is shown only after the record has been read back."

## Path 2: unsupported request, Ana in Spanish (about 12 seconds)

Sign out, choose Español and sign in as Ana. Customer line: "Quiero aumentar el límite de mi tarjeta de
crédito." The system refuses plainly and offers the card section of the bank's app or a person.
Narrate: "A request the system cannot act on is refused with a real next step. It is not guessed
at."

## Path 3: human-required, Mariana in Portuguese (about 35 seconds)

Sign out, choose Português, select Mariana's card and sign in. Customer line: "Tem uma cobrança que eu não reconheço e acho que é uma fraude,
alguém está usando meu cartão." The reply hands the case to an attendant, promises no outcome and
gives a ticket reference; show it on screen. Switch to the agent console, signed in as
agent-beatriz, find that ticket in the queue and open its packet, with every identifier masked.
Narrate: "A possible fraud always reaches a person, whatever the amount. The agent gets the
request, the verified facts, the actions taken and the open questions. The console is a viewer
today: its audited actions exist in the API and have no screen yet."

## Results (about 15 seconds)

Show the results table of `reports/evaluation.md`. Narrate: "On 135 scripted cases, this design
resolves 72.5% of in-scope cases safely and automatically. A keyword baseline reaches 33.0% and a
model-only agent 36.9%. Unsafe outcomes: zero on all 135, though that is an offline measurement,
not proof of zero risk. The automated judge that scores wording was checked against two human
raters and did not reach the 80% agreement bar on any dimension, so we do not report its scores."

## Design decisions (about 40 seconds, four at about 10 seconds each)

- **Decide, then render:** a deterministic policy engine decides eligibility and routing, because a
  financial decision must be reproducible with a stable reason code. The model never decides an
  outcome, and replies come from fixed templates filled with verified facts.
- **Authorization in the tool layer:** no tool accepts a customer identifier as an argument, so
  reaching another customer's data is not forbidden but inexpressible.
- **The risk model routes, policy decides:** a risk score can only send a case to human review. It
  never determines eligibility or the outcome.
- **Lexical retrieval with abstention:** policy questions are answered from a small versioned
  corpus with a relevance floor, and below that floor the system says it does not have the answer
  and offers a person.

## Closing (about 7 seconds)

"What is built, what is deferred and what remains is written in the repository's limitations
report. Nothing in this recording claims more than the code behind it does."

## Timing

| Part | Seconds |
|---|---|
| Opening | 15 |
| Language switch | 8 |
| Path 1 | 35 |
| Path 2 | 12 |
| Path 3 | 35 |
| Results | 15 |
| Design decisions | 40 |
| Closing | 7 |
| Total | 167 |

## Notes for the recording

- No secret value, access code, administration screen, key or unmasked identifier may appear on
  screen. A person watches the full recording before delivery, as the release checklist requires.
- The system's replies are shown on screen as the deployed system writes them. The Portuguese
  fraud reply on the deployed system omits the 24-hour contact sentence that demo script 7 shows.
- The fixture conversations in `docs/demo-scripts.md` cover each path in more than one language
  for a longer cut.
