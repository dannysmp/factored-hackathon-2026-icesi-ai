# Video script

A walk through the deployed system: the problem, the language switch, three paths with one
demonstration persona each, the evaluation result, four design decisions and the route to operation.
The total is about three minutes forty-five seconds; the Timing table gives the length of each part
and of its narration.

## Before recording

- Signing out releases a persona at once. A tab closed without signing out holds the persona for 30
  minutes (60 for an agent), so sign out at the end of each path. Every path below uses a different
  persona.
- Run `make reset-demo-personas` against the deployed database only if a filing made since the last
  deploy blocks a path. A duplicate open case for a transaction is refused, so filing is eligible
  again only after the reset.
- Play against the deployed link. The persona's access code is entered off screen and never shown.

## Where the lines come from

The system's replies are fixed templates filled with verified facts, so wording follows the
renderer and the figures follow the persona's own data. The scripted fixtures in
`docs/demo-scripts.md` use invented details (Tienda Sol, 250 MXN, D-2001, T-100) that no persona
has, so a persona's customer lines are written for that persona's transactions.

| Path | Persona | Customer lines | Checked against |
|---|---|---|---|
| Normal filing | João, Portuguese | Written for his Farmacia Salud charge; the first and third lines are those of demo script 2 | Played end to end on the deployed system |
| Unsupported request | Ana, Spanish | Demo script 4, verbatim | Played on the deployed system |
| Fraud handoff | Carlos, Spanish | Demo script 6, verbatim | Played on the deployed system |

Case and ticket numbers are read off the screen, never from this script: the deployed formats are
`CASE-` and `T-` followed by the date and eight characters.

## The problem

Narration: "Every month, a retail bank receives around three hundred and thirty disputes from
customers who don't recognize a charge. Only one in four of those cases is ever resolved or closed.
The ones that are take a median of fifteen days, and one in five misses the bank's own service-level
target. Meanwhile, customers wait, and many come back: almost one in seven of these complaints is
from someone who has complained before. That's the problem we set out to solve."

On screen: the deck's cover for the first four seconds, then slide 2.

## Opening

Narration: "So we built an assistant that takes a dispute from the customer's first message all the
way to a verified, filed case, or to the right person, in Spanish, Portuguese or English. The
language model only understands what the customer says. Every reply comes from a fixed template
filled with verified facts, and a deterministic policy engine makes every decision. In other words:
the AI listens, the rules decide."

On screen: slide 3 of the deck, then, for the last two seconds, the sign-in page with the
demonstration notice. Once signed in, the chat and the console state that the session is a
demonstration and show the date the data is current as of, June 18, 2026.

## Language switch

On the sign-in page, press Español, then Português, then English. A button selects that language's
first demonstration persona, unless that language is already selected. Narration: "It speaks the
customer's language: Spanish and Portuguese first, and English too. No menus and no forms. The
customer just writes." End on Português, which selects João for the first path.

## Path 1: normal filing, João in Portuguese

Customer lines, in order:

1. "Olá, tem uma cobrança no meu cartão que eu não reconheço."
2. "Foi na Farmacia Salud, no dia 21 de abril, cerca de 99.948,89 ARS."
3. "Sim, é essa."
4. "Sim, registrar"

The reason comes from the first line, so the system asks only which charge, finds one transaction
and shows it in US dollars, asks for confirmation, then files. Stop on the filed screen with the
case number. Narration over the confirmation: "João doesn't recognize a charge. The assistant finds
it, shows it to him, and files nothing until he approves a plain summary. And the case number only
appears once the record is confirmed. It never claims an action it hasn't verified."

## Path 2: unsupported request, Ana in Spanish

Sign out, choose Español and sign in as Ana. Customer line: "Quiero aumentar el límite de mi tarjeta
de crédito." The system refuses plainly and offers the card section of the bank's app or a person.
Narration: "Ana asks for something the assistant can't do. Instead of guessing, it says so plainly,
and points her to the right place."

## Path 3: human-required, Carlos in Spanish

Sign out, choose Español if it is not selected, select Carlos's card and sign in. Customer line:
"Hay un cargo que no reconozco y creo que es un fraude, alguien está usando mi tarjeta." The reply
hands the case to an advisor, promises no outcome and gives a ticket reference; show it on screen.
Switch to the agent console, signed in as agent-beatriz, find that ticket in the queue and open its
packet, with every identifier masked. Narration: "Carlos reports a possible fraud. That never stays
with the machine: it goes straight to a person, whatever the amount. And the agent doesn't get a raw
transcript. They get a packet: the request, the verified facts, what was done, and what's still
open."

## Results

On screen: slide 5 of the deck, presented as an offline measurement on team-written cases.
Narration: "We tested it on one hundred and thirty-five cases, thirty-two of them attacks: prompt
injection, attempts to reach another customer's data, and corrupted records. Zero unsafe outcomes.
It resolved seventy-three and a half percent of in-scope cases safely on its own, about twice either
baseline. And of the cases that should reach a person, eighty-three percent arrive with the right
reason in the packet. We even checked our own automated judge against two people. It didn't meet our
bar, so we don't use its scores."

## Design decisions

On screen: slide 4 of the deck, the architecture. The narration gives one sentence to each of the
four decisions.

Narration:

> "Four decisions make this possible.
>
> First, decide, then render. A deterministic policy engine decides, with a stable reason for
> every outcome. The model never does.
>
> Second, authorization lives in the tools. No tool even accepts a customer identifier, so
> reaching someone else's data can't be expressed.
>
> Third, the risk model routes; policy decides. A risk score can only send a case to a person. It
> never decides eligibility.
>
> And finally, retrieval with abstention. Policy answers come from a small, cited corpus. Below a
> relevance threshold, the assistant says it doesn't know, and offers a person."

## Closing

On screen: slide 6 of the deck, the limitations and next steps. Narration: "To run this in a bank,
it would need the bank's own identity, a managed database with backups, and real conversations in
each language to validate it. Everything it does, and everything it doesn't yet, is measured and
documented in the repository. An assistant that knows when not to act, and proves what it did. Thank
you."

## Timing

Seconds is the length of each part in the video; seconds of speech is the length of its recorded
narration. The recorded narration runs at about 142 words a minute (514 words in 217.7 seconds).
The remaining time in each part is for sign-ins, typing, replies and console navigation.

| Part | Seconds | Narration words | Seconds of speech |
|---|---|---|---|
| The problem | 32 | 80 | 31.5 |
| Opening | 32 | 69 | 29.0 |
| Language switch | 10 | 21 | 9.5 |
| Path 1 | 18 | 41 | 17.6 |
| Path 2 | 11 | 22 | 10.2 |
| Path 3 | 18 | 44 | 17.5 |
| Results | 39 | 85 | 38.7 |
| Design decisions | 44 | 95 | 43.1 |
| Closing | 21 | 57 | 20.6 |
| Total | 225 | 514 | 217.7 |

## Notes for the recording

- No secret value, access code, administration screen, key or unmasked identifier may appear on
  screen. A person watches the full recording before it is published, as the release checklist
  requires.
- The system's replies are shown on screen as the deployed system writes them.
- The fixture conversations in `docs/demo-scripts.md` cover each path in more than one language
  for a longer cut.
