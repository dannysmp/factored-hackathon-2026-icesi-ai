# Delivery message

The draft of the message that accompanies the release: the four links and the instructions for signing in to the deployed system. Every value that does not exist in the repository is a marked blank, filled in by the maintainer when the message is sent.

## Filling it in

| Blank | Where it comes from |
|---|---|
| `<repository link>` | The public repository's address |
| `<deployed link>` | `https://<host>/`, where `<host>` is the address the deployment step of the [deployment runbook](../infra/deployment-runbook.md) reports |
| `<console link>` | The deployed link followed by `console.html`, so that the deployed link's trailing slash is kept: `https://<host>/console.html` |
| `<slides link>` | The uploaded slide file, opened live before sending |
| `<video link>` | The uploaded recording, opened live before sending |
| `<customer access code>` | The deployment parameter `demo-signin-access-code`, read by step 6 of the runbook |
| `<agent access code>` | The deployment parameter `demo-agent-access-code`, read by step 6 of the runbook |

The access codes are pasted into the message itself and nowhere else: not into this file, a ticket, a pull request or the repository. The runbook copies each one to the clipboard so it is never printed. The deployed system must be a persisting deployment, one whose teardown after the run was turned off, and must still be running when the message is read.

## The message

Subject: Transaction disputes: repository, deployed system, slides and video

Hello,

This is the delivery of the transaction-dispute intake system: a customer reports a problem with a card transaction in Spanish, Portuguese or English, and the system decides what the bank's policy allows, files the case when it may, verifies the filing and hands the case to a person with a structured packet when it must.

**Links**

- Repository: `<repository link>`
- Deployed system: `<deployed link>`
- Slides: `<slides link>`
- Video: `<video link>`

**Signing in to the deployed system**

The system is a demonstration, so it signs in with named profiles rather than a bank identity. Each sign-in asks for a profile and an access code.

- Customer chat, at `<deployed link>`: the access code is `<customer access code>`.
- Human-agent console, at `<console link>`: the access code is `<agent access code>`.

The two codes are different and each opens only its own side. Customer profiles, with the case each one shows:

| Profile | Language | What the conversation demonstrates |
|---|---|---|
| Ana | Spanish | A dispute the policy allows: the case is filed and read back |
| João | Portuguese | A dispute the policy allows, in Portuguese |
| Emma | English | A dispute the policy allows, in English |
| Carlos | Spanish | A dispute the policy sends to a person, with a handoff packet |
| Mariana | Portuguese | A customer with repeated complaints, which the policy routes to a person |

Agent profiles for the console are Beatriz (Portuguese and Spanish) and Diego (Spanish, fraud specialty). A case handed over as Carlos or Mariana appears in the console's queue; open it to see the packet the person receives.

Things to know:

- A customer session lasts 30 minutes and an agent session 60 minutes, then the sign-in is asked for again.
- A profile is reserved for the full session length from the moment it signs in, even if the page is reloaded or the tab is closed, because the page keeps no sign-in once it is left. A reserved profile is refused with a generic message until its time runs out, so choose another profile or wait. Each address can hold five live sessions on each side, which is one per customer profile, so readers sharing a network may exhaust it.
- Several wrong access codes from the same address are temporarily refused, so copy the code rather than retyping it.
- The accounts and transactions are synthetic data; nobody's real information is involved.
- An access code is the only credential for a demonstration session, so please do not forward this message. It gates the demonstration only: the system's authentication and authorization are enforced whether or not a code is known.
- What the system does not do, and what is still to be measured, is listed in `docs/limitations.md` in the repository.

Regards,
`<sender name>`

## Before sending

Each of these is a check against the release checklist, and none needs a secret value on screen.

- All four links open from a private browser window.
- `curl -s -w '\n%{http_code}\n' https://<host>/v1/auth/demo-personas` answers `200` and lists profiles of both audiences, as in step 5 of the runbook.
- Each code has been tried once, with a different profile for each. That try reserves its profile for 30 minutes (customer) or 60 minutes (agent), so send the message after that time has passed, or keep readers off that profile.
- The clipboard is cleared after each paste, and the message is the only place either code is written.
