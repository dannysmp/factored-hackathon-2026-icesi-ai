# Limitations and remaining work

This is the honest account of what this system does and does not do today, and what is still to
be built before release. It grows as work lands; a status here is accurate as of the commit that
carries it, not a promise about a later one.

## Data limitations

- **The source data is Spanish-only.** No Portuguese or English customer text exists anywhere in
  the provided dataset, and the call transcripts carry no dispute-related language at all. Every
  Portuguese and English scripted case and policy-corpus document in this project is therefore
  team-generated, never observed, and is labeled as such wherever it appears.
- **No native Portuguese speaker reviewed the Portuguese wording.** A reviewer agent, briefed in
  Portuguese, Spanish and English, flags unnatural or ambiguous phrasing for the team to correct
  instead. It never assigns the labels the system is scored against. This is a real limitation,
  not a formality, and is disclosed next to every Portuguese result, not only here.
- **The repeat-complainer signal is not yet wired to a live data source.** The rule itself (a
  customer is a repeat complainer when their latest complaint on or before the reference date
  carries the flag) is defined and is exercised by scripted golden-set cases, but the live
  scoped-read that would compute it for a real conversation currently always reports "not a
  repeat complainer." A conversation that should escalate on this signal will not, until that
  wiring lands.
- **Filing windows are counted on the bank's own operating date** (America/Bogota), not on each
  customer's local calendar date. A customer in a different time zone could see a slightly
  different day count than they would expect near a deadline.
- **The evaluation golden set's language mix, category mix and utterances are team-defined**, not
  sampled from real customer behavior, and its target automation rate is offline and not
  representative of real customers. Every case states its own provenance (observed, team-generated
  or injected) so a reader never has to guess it.

## Machine learning

- **The transaction risk model does not route any case today.** Calibration ran, searched for a
  threshold that clears the pre-registered precision floor at no more than a 5% routed share, and
  found none: the model card records this as a negative result and keeps routing switched off. A
  fraud claim still always reaches a person regardless of this signal, since that rule does not
  depend on the risk score.
- **The intent classifier's labeled evaluation set is only just becoming available.** The keyword
  baseline it will be compared against has not been built yet.

## Conversation and evaluation

- **The evaluation harness can define and score a case, but cannot yet run one.** The golden set
  (135 scripted cases across every category the evaluation plan names), the scoring formulas and
  an independent oracle that recomputes the policy decision for a stored case row all exist. The
  piece that executes a case against a running system variant, the two baseline systems it compares
  against, the failure injector and the judge that scores language and grounding quality do not
  exist yet. No evaluation report can be produced until they do.
- **The human-agent console is not yet built.** The customer-facing chat exists; the read-only
  queue view a human agent would use to handle an escalated case does not.
- **Structured, queryable logging is not yet built.** The system does not currently emit a
  structured event when it refuses to start due to invalid configuration, or the other structured
  events later monitoring will depend on; an operator watching only the process output would see
  nothing at the moment a misconfiguration stops the service.

## Deployment

- **The deployment path has been exercised once, end to end, against a minimal build** (the health
  endpoint and the static page only, no sign-in enabled) to prove the pipeline itself works. A full
  functional deployment, run twice from a clean account before release, has not happened yet.
- **Both demonstration sign-ins are gated by an access code kept out of the repository.** Hiding
  the code is not, by itself, a security boundary; it is a demonstration convenience layered on
  top of real authentication and authorization, which are enforced regardless of whether the code
  is known.

## Security posture

Every control's actual implementation status, not just its design intent, is tracked in
[SECURITY.md](../SECURITY.md), which this document defers to rather than duplicating.

- **A document number typed unprompted into a free-text message is not detected or redacted.**
  The conversation never asks a customer for a document number, no field in the understanding
  contract can carry one, and no reply ever echoes one back — the system has no path through which
  a document number is solicited, stored or displayed. Unlike a card number, a document number
  carries no checksum a detector could key on, so a content filter over free text would be a
  length-only heuristic with an unmeasured false-positive cost against legitimate reference and
  case numbers. The deliberate choice is to rely on never asking for, accepting or echoing the
  value, not on scanning for and stripping it after the fact; a customer who volunteers one anyway
  in free text is not protected against by any filter today.

## Not attempted

A zero-shot multilingual intent classifier comparison was considered as further work if time
allowed once every other item above is closed. It has not been started, and is recorded here as
descoped work, not a missed requirement.
