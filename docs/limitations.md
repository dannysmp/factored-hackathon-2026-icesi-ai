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
- **The intent classifier's labeled evaluation set is only just becoming available.** The primary
  evaluation — per-language accuracy and a confusion matrix against that labeled set — stays in
  scope; a secondary keyword-baseline comparison point is descoped (see Not attempted).

## Conversation and evaluation

- **The evaluation harness can now run a case, not only define and score one, and every piece a
  full report needs now exists.** The golden set (135 scripted cases across every category the
  evaluation plan names), the scoring formulas, an independent oracle that recomputes the policy
  decision for a stored case row, the runner, both baseline systems (B0, B1), the failure
  injector, the LLM judge and its agreement-computation code, and the report generator
  (`reports/evaluation.md`) all exist. The judge's agreement code is tested today against a
  disclosed synthetic placeholder sample (`evals/golden/judge_validation_sample.py`,
  `PROVENANCE = "team_generated_synthetic"`); the real ≥50-case human-rated sample two raters
  return is due 2026-10-04 and has not landed. A 16-case adversarial slice (prompt injection, poisoned
  retrieval, cross-customer access) runs on every change, against the proposed system and B0,
  over synthetic seed data only, and blocks merge on any case turning unsafe. The full 32-case
  adversarial set can now also run outside CI: a loader (`app.persistence.load_eval_bank`)
  combines the operational seed with the evaluation scenario bank, so every case that needs a
  deliberately inconsistent transaction (an orphan reference, a null field, an injected merchant
  name, an unconvertible amount) resolves against real, loaded data, not only the smoke slice's
  synthetic subset. What has not happened yet is the first full `make evaluate` run itself and its
  recorded report — the pieces exist, but no evidence-producing run against the complete golden
  set has been executed.
- **One case in the full evaluation run produced a single, non-reproducible unsafe outcome.**
  The English fraud-claim case in the human-required category turned unsafe in one of three
  repeated runs against the proposed system, in the handoff-ticket path the case exercises.
  Twenty further live repeats of the same case produced zero unsafe outcomes and found no
  structural defect anywhere in that path. This is recorded as a disclosed, non-reproducible
  finding, not a fixed defect: nothing in the code changed, because nothing reproducible was
  found to fix, and a rare, non-reproducible finding from a real evaluation run is a fact worth
  stating plainly rather than treating as resolved once no cause is found.
- **The human-agent console is now live, read-only.** The queue and ticket-detail screens draw
  from real backend data (`LiveQueueClient`, `LiveTicketDetailClient`), and `app.main.create_app`
  registers the console's routes whenever the agent demo broker is enabled — the same flag that
  gates whether an agent token can ever be issued in the first place. It remains a viewer: no
  write action exists yet, matching its own design (ADR-17).
- **Structured logging runs across the service and every CLI entrypoint, including a configuration
  failure at start-up.** Every line carries a stable event name, the request's trace id and, once
  authenticated, its session id, with any card-shaped digit run redacted before the line is
  written. A configuration failure is caught before logging would otherwise be installed, so it
  still emits a structured `config_invalid` event (naming the failing setting, never its value)
  through a fallback logger before the process exits, rather than surfacing only as an
  unstructured traceback.

## Deployment

- **The deployment path has been exercised once, end to end, against a minimal build** (the health
  endpoint and the static page only, no sign-in enabled) to prove the pipeline itself works. A full
  functional deployment, run twice from a clean account before release, has not happened yet.
- **Both demonstration sign-ins are gated by an access code kept out of the repository.** Hiding
  the code is not, by itself, a security boundary; it is a demonstration convenience layered on
  top of real authentication and authorization, which are enforced regardless of whether the code
  is known.
- **The BI dashboard's logo, application name and instance-wide colors are not themed.** Metabase
  gates its native application-branding settings (application name, logo, favicon and
  instance-wide color overrides) behind a paid Pro or Enterprise license, and this deployment runs
  the open-source edition with no such license. What the open-source edition does expose — each
  panel's own chart colors, a text card naming its business question, and panel order — is applied
  from the design-token palette instead, verified in
  [`reports/dashboard-theme-checklist.md`](../reports/dashboard-theme-checklist.md).

## Security posture

Every control's actual implementation status, not just its design intent, is tracked in
[SECURITY.md](../SECURITY.md), which this document defers to rather than duplicating.

- **A document number typed unprompted into a free-text message is not detected or redacted.**
  The conversation never asks a customer for a document number. The understanding contract has
  exactly three free-text fields with no restriction on what they hold — a transaction's merchant
  as the customer describes it, the dispute detail, and a policy question — any of which can carry
  a document number if a customer types one there; every other field (the category, a
  confirmation, an amount, a date) is a closed enum or a narrowly patterned value that cannot.
  None of the three free-text fields is ever echoed back to that or any other customer: the
  merchant hint is used only to match against the customer's own real transactions, never
  displayed itself (a reply always states the matched transaction's own stored merchant, not the
  customer's typed hint); the dispute detail is not read anywhere; a policy question is used only
  to retrieve a matching policy section, never quoted back. Unlike a card number, a document
  number carries no checksum a detector could key on, so a content filter over free text would be
  a length-only heuristic with an unmeasured false-positive cost against legitimate reference and
  case numbers. The deliberate choice is to rely on never asking for or echoing the value, not on
  scanning for and stripping it after the fact; a customer
  who volunteers one anyway in free text is not protected against by any content filter today —
  the value still reaches the outbound model request unmasked, the way a card number's digits are
  masked before that same request is sent.

## Not attempted

A zero-shot multilingual intent classifier comparison was considered as further work if time
allowed once every other item above is closed. It has not been started, and is recorded here as
descoped work, not a missed requirement.

A keyword-rule baseline for intent classification, compared against the labeled intent set as a
secondary reference point, was descoped the same way: the primary evaluation it would have stood
beside — per-language accuracy and a confusion matrix for the intent classifier itself — is
unaffected and stays in scope. Nothing downstream depends on the keyword baseline, and it is
recorded here as descoped work, not a missed requirement.
