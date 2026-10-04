# Video script

Walks the three demonstration paths and names the core architectural decisions behind what the
viewer sees, each by its record number with a one-sentence explanation, as the release checklist
requires. This is a first draft of the script; the live conversation endpoint it plays against
now runs in the customer chat (`LiveChatClient`, not only recorded fixtures), and the recording
itself remains a later, separate deliverable.

Every line of dialogue quoted here is taken verbatim from `docs/demo-scripts.md`; this script adds
only the narration between paths and the closing walk through the decision records.

## Opening

Narration, about 20 seconds: "This is a transaction-dispute intake system for a simulated bank. A
customer describes a problem with a charge in Spanish, Portuguese or English; the system
authenticates them, finds the transaction, decides what policy allows, and either files the case or
brings in a person. The model understands and renders language. It never decides an outcome — that
is the policy engine's job, and it is deterministic and tested on its own."

Show the demonstration banner and the reference-date line on screen; narrate: "Every screen states
that this is a demonstration, and the date the data is current as of — June 18, 2026 throughout
this recording."

## Path 1: the normal path, filing an eligible dispute

About 45 seconds. Play demo script 1 (Spanish) from `docs/demo-scripts.md`, turns 1 through 6, on
screen in the customer chat interface. Narrate over the confirmation turn: "The system asks for
explicit confirmation before it files anything, and reads the case number back afterward — it
never tells the customer a write happened without first confirming the write is there."

## Path 2: the ambiguous or unsupported path, a request outside the system's scope

About 15 seconds. Play demo script 4 (Spanish), the single-turn credit-limit request and its
refusal. Narrate: "A request the system cannot act on — raising a credit limit — is refused
plainly, with a real next step, not guessed at."

## Path 3: the human-required path, a fraud claim with the handoff and the console

About 40 seconds. Play demo script 6 (Spanish), the fraud claim and its escalation reply. Then
switch to the human-agent console and show the resulting handoff ticket and its structured packet,
with every identifier masked. Narrate: "A fraud claim always reaches a person, regardless of the
amount or what a risk model would say about it. The console is a read-only viewer today, by
decision, not because the write path is unfinished — an agent sees exactly what the customer said,
masked, and the reasoning that led here."

## Decision records named

About 40 seconds.

Narrate each in one sentence, matching `plan/docs/architecture.md`:

- **ADR-1, decide-then-render:** the system uses a deterministic policy engine instead of an
  autonomous agent loop, because dispute eligibility has financial consequences that need
  reproducible decisions and stable reason codes, not a model's judgment call.
- **ADR-2, the typed envelope:** everything the customer reads is rendered only from a structured
  envelope of facts and sources, so grounding is enforced by the code that builds the reply, not
  by hoping the model doesn't invent a number.
- **ADR-3, authorization in the tool layer:** no tool in this system can even accept another
  customer's identifier as an argument, so cross-customer access is not just forbidden, it is
  inexpressible.
- **ADR-6, the risk scorer routes, policy decides:** a transaction's risk score can only send a
  case to human review; it never determines whether a dispute is eligible or what happens to it.
- **ADR-16, lexical retrieval with abstention:** policy questions are answered from a small,
  versioned corpus with a relevance floor, and a question below that floor gets a plain "I don't
  have that" and a person, never a guess.
- **ADR-17, the console as a read-only viewer:** the human-agent console shows cases and audit
  timelines across customers as its own authorization domain, and ships as a viewer first; its
  narrow audited writes exist in the API and have no screen, by that design, not as a missing feature.

## Closing

About 10 seconds. "The complete account of what is built, what is deliberately deferred, and what remains is in the
repository's limitations report and its technology evolution matrix — nothing in this recording
claims more than the code behind it does."

## Notes for the recording

- Total target length: about three minutes.
- No secret value, administration screen, key or unmasked identifier may appear on screen; this is
  checked by a person watching the full recording before delivery, per the release checklist.
- The Portuguese and English variants of each path (`docs/demo-scripts.md`, scripts 2, 3, 5 and 7)
  are available for a longer cut or a second recording, if the release schedule allows one; this
  script covers the required minimum: all three paths, with the handoff and the console, in one
  language.
