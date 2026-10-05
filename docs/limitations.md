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
  rater reviewed all 135 cases and changed none of their labels, but none of them declares an
  ineligible outcome: the evaluation does not exercise the eight ineligibility reason codes,
  which only the policy engine's own tests cover. See "Golden-set adjudication" in `evals/README.md`.

## Machine learning

- **The transaction risk model does not route any case today.** Calibration ran, searched for a
  threshold that clears the precision floor fixed in advance at no more than a 5% routed share, and
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
- **The judge is not validated on any dimension, so its quality scores are not reported.** Two
  raters and the judge scored the same 50 cases (6 for clarification, which only ambiguous cases
  carry). Agreement is the share of cases scored identically, and a dimension is demoted to
  human-only when the judge agrees with either rater below 80%. All three are demoted. Grounding:
  judge-to-rater agreement 60% and 28%, and the raters agree with each other on only 38%.
  Language quality: 62% and 60%, with the raters at 94%; the judge scores lower than both raters in
  nearly every case where they differ. Clarification: 17% and 100% over six cases, a sample too
  small to settle anything. The judge-scored quality section of `reports/evaluation.md` therefore
  states "not reportable by the judge" in place of the judge's means and shows the raters' means
  beside it. 46 of the 50 sheet rows told the raters no case-specific facts were on record, which
  makes grounding a weaker test than a reply set beside the facts it should cite, and the
  disagreement between the two raters on grounding is unexplained. The written analysis of where
  raters and judge disagree is a person's job and is not generated.
  Running `make judge-validation RATER1=<sheet> RATER2=<sheet>` scores the same cases with the
  real judge, patches that section and the matching limitations line of the report, and writes
  every case's scores to `reports/judge-validation-cases.csv`, a local working file that is not
  tracked in the repository.
  The report's figures describe the commit it names, not the current head, which carries later
  behaviour and scoring fixes.
- **A transaction described by a kind of transaction or place is found by its other details, from a
  fixed word list.** When a customer names a transfer, a charge or an online store where a
  merchant would go, the lookup ignores that word and searches by the amount, date or card; a
  message with nothing else asks which transaction is meant. The list is exact-match and covers
  Spanish, Portuguese and English. A phrase outside it, or a merchant name the model guesses
  from the customer's words, still narrows the search to a merchant that may not exist and can
  answer "not found" for a transaction the customer owns. A currency the model supplies for an
  amount written with only a bare `$` is discarded; a currency the customer states is kept. An
  amount is matched against the transaction's dollar figure or its amount in its own currency,
  and the amount and currency must come from the same figure. The behavior was verified with
  scripted understanding results, not across live model output.
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
- **Text enlarged beyond 200% on a narrow phone can make the page scroll sideways, and part of the
  header title can be cut off.** Every interactive control stays reachable by scrolling. At 200%
  text size on a 375 px phone the layout fits without scrolling. At 300% on 375 px and 320 px
  phones, depending on language and width, the sign-in language buttons and the persona language
  tag, the Yes and No quick replies and the console header and queue overflow, and in the Spanish
  chat and the console the start of the header title sits left of the page origin where it cannot
  be scrolled to. This concerns text-only enlargement. At browser page zoom of 400% on a 320 px
  screen the sign-in, chat and console pages do not scroll sideways; the console queue table
  scrolls inside its own region.
- **Structured logging runs across the service and every CLI entrypoint, including a configuration
  failure at start-up.** Every line carries a stable event name, the request's trace id and, once
  authenticated, its session id, with any card-shaped digit run redacted before the line is
  written. A configuration failure is caught before logging would otherwise be installed, so it
  still emits a structured `config_invalid` event (naming the failing setting, never its value)
  through a fallback logger before the process exits, rather than surfacing only as an
  unstructured traceback.
- **Model use has no per-session token budget.** Two limits bound it: the turn cap
  (`DIALOGUE_MAX_TURNS`, 30 customer turns a session) and the daily spend limit
  (`LLM_DAILY_SPEND_LIMIT_USD`, 10 US dollars across all customers). Neither caps the tokens a
  single session or customer may use, so one session can spend up to the turn cap, and a few
  sessions can use the whole day's limit before other customers are handed to a person.
- **Concurrent requests on one session are not serialized.** The turn cap counts the turns a
  session has already saved, so requests that arrive together on the same session can each reach
  the model before the first is saved, and each can pass the cap and the spend check. The cap is
  a bound on a conversation, not a rate limit.

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

## Capacity and retention

- **The system runs on one host and one backend process, and no load or throughput test has been
  run.** The backend, web server, Postgres and reverse proxy share a single `t3.large` instance,
  with the optional dashboard as a fifth service, and the backend starts one server process. No
  figure for concurrent users, requests per second or latency under load is claimed. The one
  capacity reading is the memory footprint of the running containers that a dashboard deployment
  logs (see [`infra/README.md`](../infra/README.md)). Serving more customers means measuring first,
  then moving the database off the host and moving the in-memory state below to a shared store
  before running more than one backend process.
- **Three controls hold their state in memory, per process.** The sign-in attempt limit, the
  demonstration sign-in issuance limit and the session revocation list are each cleared by a
  restart and are not shared between processes. With more than one backend process the limits
  would be multiplied, and a revoked session could keep working on a process that did not see the
  revocation. Tokens still expire on their own.
- **No retention period or purge procedure is implemented.** Conversation state, the per-turn
  timeline, handoffs and audit records are kept until the host's database volume is removed; the
  application deletes none of them on a schedule. Filed cases are also cleared by the seed reload
  that every deployment performs (see Deployment). A retention period for each kind of record and a
  purge procedure are remaining work. For a demonstration, `make reset-demo-personas` deletes the
  cases the demonstration personas accumulated.

## Security posture

Every control's actual implementation status, not just its design intent, is tracked in
[SECURITY.md](../SECURITY.md), which this document defers to rather than duplicating.

**A document number typed unprompted into a free-text message is redacted by shape, with known
gaps.** The conversation never asks a customer for a document number. The understanding contract
has exactly three free-text fields with no restriction on what they hold — a transaction's
merchant as the customer describes it, the dispute detail, and a policy question — any of which can
carry a document number if a customer types one there. None of the three is ever echoed back to
that or any other customer: the merchant hint is used only to match against the customer's own
real transactions, never displayed itself; the dispute detail is not read anywhere; a policy
question is used only to retrieve a matching policy section, never quoted back. Before the
customer's message is sent to the model, an unbroken run of seven or more digits and the two
punctuated Brazilian tax-number shapes (a personal tax number such as `123.456.789-09` and a
company tax number such as `12.345.678/0001-95`) are replaced with a fixed placeholder, the same
way a card number is. A document number has no checksum, so the rule is by shape, and it is chosen
so that a money amount written with separators (`$27.556.276,44`, `1,475,202.64`) is never
touched. That choice leaves gaps. A national identity number typed with thousands-style dots
(`1.094.921.834`) has the shape of an amount and reaches the model unmasked. An amount typed as
seven or more unbroken digits (`1250000`) is redacted like an identifier; it is only a search hint
for the customer's own transactions, and policy reads the stored amount, so the cost is one more
question to the customer. A tax number written with a hyphen and short dotted groups (`900.123.456-7`,
`12.345.678-5`) has no recognized shape and reaches the model unmasked; typed without dots it is an
unbroken run and is redacted, leaving only its check digit. An unbroken decimal amount (`1250000.50`)
loses its whole part and keeps the decimal part, and a date typed as eight unbroken digits
(`20260612`) is redacted, so it is not available as a search hint. A longer run of digits around a
tax-number shape (`123.456.789-091`) is a different figure and is left alone. An identity number
split by other characters, or written in words, is not detected. The customer's own message text
is the only thing this rule applies to; the controller's own parsing always reads the original
text.

## Not attempted

- **A labeled intent set and everything compared against it.** The set itself, the per-language
  intent accuracy and confusion matrix, the keyword-rule baseline and the pretrained zero-shot
  multilingual classifier were descoped together. Nothing downstream depends on them.
- **Write actions in the console's interface.** See Conversation and evaluation.
