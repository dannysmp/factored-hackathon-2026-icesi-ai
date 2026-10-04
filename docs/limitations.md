# Limitations and remaining work

This is the honest account of what this system does and does not do today. A status here is
accurate as of the commit that carries it, not a promise about a later one. Items that depend on
results still to come say so and name the command that completes them.

## Data limitations

- **The source data is Spanish-only.** No Portuguese or English customer text exists anywhere in
  the provided dataset, and the call transcripts carry no dispute-related language at all. Every
  Portuguese and English scripted case and policy-corpus document in this project is therefore
  team-generated, never observed, and is labeled as such wherever it appears.
- **No native Portuguese speaker reviewed the Portuguese wording.** A reviewer agent, briefed in
  Portuguese, Spanish and English, flags unnatural or ambiguous phrasing for the team to correct
  instead. It never assigns the labels the system is scored against. This is a real limitation,
  not a formality, and is disclosed next to every Portuguese result, not only here.
- **The repeat-complainer signal is the seed's point-in-time flag, not a live recomputation.** The
  rule (a customer is a repeat complainer when their latest complaint on or before the reference
  date carries the flag) is applied by the policy engine, and the scoped read supplies it from the
  customer's own stored flag. The service never re-derives it from the complaints table, so a
  complaint filed after the seed was built does not change it.
- **There is a single operating time zone.** Filing windows and every reference date are counted
  on the bank's own operating date (America/Bogota, UTC-5), not on each customer's local calendar
  date, and no per-customer time zone is stored or applied. A customer in a different time zone
  could see a slightly different day count than they would expect near a deadline.
- **The evaluation golden set's language mix, category mix and utterances are team-defined**, not
  sampled from real customer behavior, and its target automation rate is offline and not
  representative of real customers. Every case states its own provenance (observed, team-generated
  or injected) so a reader never has to guess it.
- **The golden set has no case that expects a denial of an ineligible filing.** An independent
  rater confirmed the labels of all 135 cases, but none of them declares an ineligible outcome:
  the evaluation does not exercise the eight ineligibility reason codes, which only the policy
  engine's own tests cover. See "Golden-set adjudication" in `evals/README.md`.

## Machine learning

- **The transaction risk model does not route any case today.** Calibration ran, searched for a
  threshold that clears the pre-registered precision floor at no more than a 5% routed share, and
  found none: the model card records this as a negative result and keeps routing switched off. A
  fraud claim still always reaches a person regardless of this signal, since that rule does not
  depend on the risk score.
- **There is no labeled intent set and no intent-set comparison.** Building a labeled set of
  utterances, scoring the language model's intent understanding against it per language, and
  comparing it with a keyword baseline or a zero-shot multilingual classifier are all out of
  scope. Intent understanding is measured only through the end-to-end golden-set evaluation, which
  scores outcomes, not intent labels; there is no per-language intent accuracy or confusion matrix.
  See Not attempted.

## Conversation and evaluation

- **The evaluation covers 135 scripted cases, three systems and a 32-case adversarial set.** The
  golden set, the scoring formulas, an independent oracle that recomputes the policy decision for
  a stored case row, the runner, both baselines (B0, B1), the failure injector, the language-model
  judge and the report generator all exist, and `reports/evaluation.md` records the full run
  against the live model. Its safe/unsafe classification is read directly off each run's recorded
  outcome. A 16-case adversarial subset (prompt injection, poisoned retrieval, cross-customer
  access) runs on every change against the proposed system and B0 over synthetic seed data, and
  blocks merge on any case turning unsafe; the full adversarial set runs outside CI against the
  operational seed combined with the evaluation scenario bank (`app.persistence.load_eval_bank`).
- **The judge's agreement with human raters is not yet measured on a real sample.** The agreement
  code is tested against a disclosed synthetic placeholder
  (`evals/golden/judge_validation_sample.py`, `PROVENANCE = "team_generated_synthetic"`), and
  the Judge validation section of `reports/evaluation.md` states that no agreement rate is
  reported. The judge's quality and correctness scores stay provisional until two raters return the
  50-case sheets.
  Running `make judge-validation RATER1=<sheet> RATER2=<sheet>` scores the same cases with the real
  judge and patches that section and the matching limitations line of that report with rater-to-rater
  and rater-to-judge agreement per dimension, each with its pair count and weighted kappa, the
  direction of the differences and the decision per dimension, and writes every case's scores to
  `reports/judge-validation-cases.csv`, a local working file that is not tracked in the repository;
  this bullet is to be rewritten with those figures,
  and with any dimension the judge is demoted on, at the same time. The written analysis of where
  the raters and the judge disagree is a person's job and is not generated.
- **The abstention check is a small sample.** A policy question the corpus does not cover must get
  "not held, here is a person" instead of a guess. That behavior is exercised by one unrelated
  banking question per language and a short list of everyday sentences with no policy content in
  the retrieval tests (`tests/test_lexical_retrieval.py`), by one nonsense query in the policy
  answer tests (`tests/test_policy_answer.py`). One nonsense query through the dialogue controller
  (`tests/test_dialogue_controller.py`) checks only that a reply comes back, not what it says. The
  golden set holds no uncovered policy question: its 13 unsupported-request cases (a transfer, a
  limit increase, a loan) are requests for services the system does not handle, not questions
  about the policy. A larger set of customer-worded uncovered questions per language was not run,
  so a pass shows the behavior on these inputs, not across the many ways a customer can ask about
  a topic the policy does not address.
- **Case `hr-fraud-en-01` produced one non-reproducible unsafe outcome.** This is the English
  fraud claim in the human-required category. In one of the three repeats of the full evaluation
  run it turned unsafe against the proposed system, in the handoff-ticket path the case exercises.
  Twenty further live repeats of the same case produced no unsafe outcome and found no structural
  defect anywhere in that path, so no cause was identified and nothing in the code changed for it.
  It is disclosed as an observation that did not reproduce, not as a fixed defect. The committed
  `reports/evaluation.md` records no unsafe outcome in its own three runs, so that report does
  not show it.
- **The human-agent console is a viewer; its write actions have no screen.** The queue and
  ticket-detail screens draw from real backend data. The backend also exposes four narrow agent
  writes (claim, release, note and status change under `/v1/agent/tickets/{ticket_ref}`), each
  scoped to the signed-in agent and audited. The console's interface never calls them, matching
  its design as a viewer, so those actions are reachable only through the API.
- **Structured logging runs across the service and every CLI entrypoint, including a configuration
  failure at start-up.** Every line carries a stable event name, the request's trace id and, once
  authenticated, its session id, with any card-shaped digit run redacted before the line is
  written. A configuration failure is caught before logging would otherwise be installed, so it
  still emits a structured `config_invalid` event (naming the failing setting, never its value)
  through a fallback logger before the process exits, rather than surfacing only as an
  unstructured traceback.

## Deployment

- **A deployment from a new account has not been shown to reproduce.** The provisioning scripts
  are idempotent and the procedure is written in
  [`infra/deployment-runbook.md`](../infra/deployment-runbook.md), but no run starting from an
  empty account is recorded. One persisting deployment onto an already-provisioned account is
  recorded in the runbook's run table; it reproduces the stack and the dashboard, not the account
  provisioning.
- **The deployed data has no backup and no restore.** Cases filed on the host live only in its
  `postgres-data` volume, and a deployment run with `teardown_after` on, which is the default,
  removes the host with that volume. The rest of the data is synthetic and can be rebuilt with one
  command, `python -m app.persistence.load_seed`, which every run of `infra/scripts/05-deploy.sh`
  also performs. That load truncates the serving tables, including filed cases, and reloads them
  from the seed except the cases, so a case filed on a persisting host is lost by the next
  deployment to it. Nothing preserves a case otherwise: there is no scheduled dump of the database
  and no restore procedure. The deployed system makes no claim of data durability.
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

**A document number typed unprompted into a free-text message is not detected or redacted.** The
conversation never asks a customer for a document number. The understanding contract has exactly
three free-text fields with no restriction on what they hold — a transaction's merchant as the
customer describes it, the dispute detail, and a policy question — any of which can carry a
document number if a customer types one there; every other field (the category, a confirmation,
an amount, a date) is a closed enum or a narrowly patterned value that cannot. None of the three
free-text fields is ever echoed back to that or any other customer: the merchant hint is used only
to match against the customer's own real transactions, never displayed itself (a reply always
states the matched transaction's own stored merchant, not the customer's typed hint); the dispute
detail is not read anywhere; a policy question is used only to retrieve a matching policy section,
never quoted back. Unlike a card number, a document number carries no checksum a detector could
key on, so a content filter over free text would be a length-only heuristic with an unmeasured
false-positive cost against legitimate reference and case numbers. The deliberate choice is to
rely on never asking for or echoing the value, not on scanning for and stripping it after the
fact; a customer who volunteers one anyway in free text is not protected against by any content
filter today — the value still reaches the outbound model request unmasked, the way a card
number's digits are masked before that same request is sent.

## Not attempted

- **A labeled intent set and everything compared against it.** The set itself, the per-language
  intent accuracy and confusion matrix, the keyword-rule baseline and the pretrained zero-shot
  multilingual classifier were descoped together. Nothing downstream depends on them.
- **Write actions in the console's interface.** See Conversation and evaluation.
