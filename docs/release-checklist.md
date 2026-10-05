# Release checklist

This checklist states each obligation of a release and names the evidence a reviewer can open. A row is checked off only with its evidence linked. Every artifact that already exists is linked directly from its row; the rehearsal checklist and the slide content checklist are written as a release is prepared and carry no link until then. Every row is verified before the freeze; the rows that check a live link are checked once more immediately before the release is announced, and that second check is recorded with its time.

## Repository

| Item | Evidence | Done |
|---|---|---|
| The repository is public | [The repository page](https://github.com/dannysmp/factored-hackathon-2026-icesi-ai) opens without signing in (HTTP 200 unauthenticated on 2026-10-05); `gh repo view` reports visibility PUBLIC | [x] |
| The full history has no secret or private record | The history scan report from the release commit, and the last diff read by a person. Stated limitation: the full-history scan runs on every change ([the secret-scan job](../.github/workflows/ci.yml)) and [a committed report](../reports/secret-scan.md) records a clean scan of `main` at `b4e8517`, but that is not the release commit, which is scanned again once it is fixed, and the last diff has not been read by a person. | [ ] |
| No credential, key or environment file is tracked | [The secret-scan job](../.github/workflows/ci.yml), passing in [the run on main](https://github.com/dannysmp/factored-hackathon-2026-icesi-ai/actions/runs/37288522476); [the test that the model key stays out of the repository](../tests/test_model_key_stays_out_of_repo.py); `git ls-files` lists no environment, key or credential file other than `.env.example` | [x] |
| The README leads from the problem to a running demonstration without a question | A person who did not write it follows it from a clean checkout. Stated limitation: no read of a clean checkout by a person who did not write the README is recorded. | [ ] |
| Every model card, the limitations and remaining-work report and the demonstration scripts are present | [The model card](../models/model_card.json), [the limitations and remaining-work report](limitations.md) and [the demonstration scripts](demo-scripts.md), each present at the commit this checklist was last updated | [x] |
| Inputs are labeled real, de-identified, synthetic or team-generated | [The data-use section of the README](../README.md#where-each-input-comes-from) | [x] |
| No card number and no document-number-shaped value appears in the understanding step's request to an external model; the shapes the document-number rule cannot separate from an amount are disclosed | [The request-capture test](../tests/test_request_capture_pii.py) for the understanding step's request, and [the security posture section of the limitations report](limitations.md#security-posture) for the disclosed gaps | [x] |

## Deployed system

| Item | Evidence | Done |
|---|---|---|
| The deployed link opens over HTTPS with a valid certificate | Checked live at the freeze and again before the release is announced, with the time of each check recorded. The deployed link returned HTTP 200 with a verified certificate on 2026-10-05 at 12:28Z (`curl -sI` on the deployed link). Stated limitation: the second check, made immediately before release, is not recorded. | [ ] |
| The three demonstration paths run in the browser, in Spanish and Portuguese, and the normal path in English | The rehearsal checklist, second run, all passing. Stated limitation: no rehearsal record is committed; [the demonstration scripts](demo-scripts.md) write the paths down. | [ ] |
| The demonstration notice and the data reference date are visible on every screen | The rehearsal checklist. Stated limitation: no rehearsal record is committed, so the notice and the reference date on every screen are not verified by a committed record. | [ ] |
| The console is shown as a read-only viewer | The rehearsal checklist. Stated limitation: no rehearsal record is committed; that the console has no screen for write actions is stated in [the limitations report](limitations.md#not-attempted). | [ ] |
| The deployment, dispatched manually from the main branch, passes the smoke test | [The deploy workflow](../.github/workflows/deploy.yml) and [the manually dispatched run on main at 2cad12d, whose HTTPS smoke test and dashboard smoke test passed](https://github.com/dannysmp/factored-hackathon-2026-icesi-ai/actions/runs/37227276409) | [x] |
| The provisioning scripts are idempotent and the deployment procedure is written; a reproduction from a new account has not been shown, and that is disclosed | The [deployment runbook](../infra/deployment-runbook.md), and [the deployment section of the limitations report](limitations.md#deployment) for the reproduction that has not been shown | [x] |
| No long-lived cloud key exists in the repository or the pipeline | [The deploy workflow](../.github/workflows/deploy.yml) assumes its role through the repository's OIDC token and holds no static AWS key; [the secret-scan job](../.github/workflows/ci.yml) runs on every change | [x] |
| The demonstration sign-in can be switched off, and the switch has been tried | [The runbook entry](../infra/README.md#turning-the-demonstration-sign-in-off), with the date of the trial. Stated limitation: the trial table in the runbook is empty; the switch has not been tried. | [ ] |

## Slides

| Item | Evidence | Done |
|---|---|---|
| Four to six slides | The slide file. Stated limitation: the slide file is a presentation file held outside the repository; [the slide content](slide-content.md) states what each slide holds, and no check of the slide file is recorded here. | [ ] |
| Every figure about the problem comes from the workflow analysis report, and every result from the evaluation report | The slide content checklist with the check recorded by role. Stated limitation: the slide file is a presentation file held outside the repository; [the slide content](slide-content.md) states what each slide holds, and no check of the slide file is recorded here. | [ ] |
| No secret value, administration screen, key or unmasked identifier appears | The slide content checklist. Stated limitation: the slide file is a presentation file held outside the repository; [the slide content](slide-content.md) states what each slide holds, and no check of the slide file is recorded here. | [ ] |
| The slides cover the problem with data, the architecture and its trade-offs, controlled automation and security, the evaluation results, and the limitations and route to operation | The slide file. Stated limitation: the slide file is a presentation file held outside the repository; [the slide content](slide-content.md) states what each slide holds, and no check of the slide file is recorded here. | [ ] |

## Video

| Item | Evidence | Done |
|---|---|---|
| The video shows the working solution end to end: the normal path, the ambiguous or unsupported path and the human-required path with the handoff and the console | The recording, with the time of each path noted. Stated limitation: no recording is held in the repository. | [ ] |
| The core architectural decisions are named and explained in a sentence each | The recording; [the design decisions in the video script](video-script.md). Stated limitation: no recording is held in the repository. The decisions are written in [the video script](video-script.md). | [ ] |
| No secret value, administration screen, key or unmasked identifier appears in the recording | A person watches the whole recording before the release is announced, and records the check by role. Stated limitation: no recording is held in the repository. | [ ] |
| The video is uploaded and opens from its link | The link, opened live at the freeze and again before the release is announced. Stated limitation: no recording is held in the repository. | [ ] |

## Requirements walk

Every requirement is checked with a link to its evidence.

| Requirement | Evidence to open | Done |
|---|---|---|
| Working system for a banking environment | [The deployment run on main](https://github.com/dannysmp/factored-hackathon-2026-icesi-ai/actions/runs/37296135899) whose smoke tests passed, and [the demonstration scripts](demo-scripts.md) | [x] |
| Focused workflow, end to end | [The README's description of the workflow](../README.md#how-it-works) and [the demonstration scripts](demo-scripts.md) | [x] |
| Data-backed problem selection | [The workflow analysis report](../reports/workflow-analysis.md) and `make analyze`, the command that regenerates it | [x] |
| Baseline and improvement on the same workload | [The headline metrics](../reports/evaluation.md#3-headline-metrics) and [the repeated-run variability](../reports/evaluation.md#5-repeated-run-variability): the proposed system against both baselines on the same 135 cases; [the three-run comparison](../reports/evaluation-comparison.md) | [x] |
| Privacy, explainability, fairness, reliability, scalability by design | [SECURITY.md](../SECURITY.md); [the security posture](limitations.md#security-posture) and [the capacity and retention](limitations.md#capacity-and-retention) sections of the limitations report; [the ASVS Level 1 checklist](asvs-level1-checklist.md); [the disparity analysis](../reports/evaluation.md#8-fairness-and-disparity); explainability: [the stable reason code each decision carries](../README.md#how-it-works) and [the audit record tests](../tests/test_service_contract_audit.py); reliability: [the retry](../tests/test_reliability_retry.py), [breaker](../tests/test_reliability_breaker.py) and [tool port](../tests/test_reliability_tool_port.py) tests | [x] |
| Explicit trade-offs | [The design rules in the README](../README.md#how-it-works), [the design decisions in the video script](video-script.md) and [the limitations report](limitations.md) | [x] |
| Where AI and where deterministic logic | [The five stages and the design rules](../README.md#how-it-works); [the keyword baseline against the proposed system](../reports/evaluation.md#3-headline-metrics) | [x] |
| Three demonstration paths | [The demonstration scripts](demo-scripts.md); the rehearsal checklist. Stated limitation: the paths are written in [the demonstration scripts](demo-scripts.md); no rehearsal record is committed. | [ ] |
| Spanish and Portuguese, English added, language limits reported | [The outcomes by language](../reports/evaluation.md#8-fairness-and-disparity); [the limitations report](limitations.md#data-limitations) | [x] |
| Context, clarification and grounded answers | [The policy-answer tests](../tests/test_policy_answer.py), [the output verifier tests](../tests/test_verifier.py) (grounding) and [the dialogue controller tests](../tests/test_dialogue_controller.py) (clarification); [the failure gallery](../reports/evaluation.md#6-failure-gallery) lists the cases that failed. The judge-scored grounding and clarification measures are not validated ([judge validation](../reports/evaluation.md#9-judge-validation)). | [x] |
| Tools, and only verified actions reported | [The tool contract tests](../tests/test_service_contract_tools.py), [the output verifier tests](../tests/test_verifier.py) and [the dialogue controller tests](../tests/test_dialogue_controller.py) | [x] |
| Controlled automation | [The automation stages in the README](../README.md#how-it-works); [the policy engine tests](../tests/test_policy_engine.py), [the session authorization tests](../tests/test_session_auth_middleware.py) and [the dialogue controller tests](../tests/test_dialogue_controller.py) | [x] |
| Handoff packet | [The handoff builder tests](../tests/test_handoff_builder.py), [the ticket detail tests](../tests/test_persistence_ticket_detail.py) and [the third path of the demonstration scripts](demo-scripts.md) | [x] |
| Repeatable data preparation | [The pipeline commands](../pipelines/README.md), [the contracts](../contracts/README.md), [the quality report](../reports/data-quality.md) and [the freshness tests](../tests/test_silver.py) | [x] |
| Learned component against a baseline | [The model card](../models/model_card.json) and [the experiment log](../models/experiments.jsonl). Stated limitation: no validation threshold reached the required precision, so the risk model routes no case and the card records no test score; [the machine-learning section of the limitations report](limitations.md#machine-learning) states it. | [ ] |
| Held-out evaluation including failure conditions | [The unsafe-outcome section](../reports/evaluation.md#7-unsafe-outcomes) and [the failure gallery](../reports/evaluation.md#6-failure-gallery) over the 32 adversarial cases | [x] |
| Successful, unsafe, handoff, latency and cost with sample sizes | [The headline metrics](../reports/evaluation.md#3-headline-metrics), each with its sample size | [x] |
| Outcome definitions honored | [The metric definitions](../evals/metrics.py), [their tests](../tests/test_eval_metrics.py) and [the report](../reports/evaluation.md) | [x] |
| Tracing, retries, safe fallback, reproducible setup | [The dialogue controller tests](../tests/test_dialogue_controller.py) and the [retry](../tests/test_reliability_retry.py), [breaker](../tests/test_reliability_breaker.py) and [tool port](../tests/test_reliability_tool_port.py) tests, run with `uv run pytest tests/test_dialogue_controller.py tests/test_reliability_retry.py tests/test_reliability_breaker.py tests/test_reliability_tool_port.py`; the quickstart. Stated limitation: the reliability tests are linked, but no person has followed the quickstart from a clean checkout (the README row above), and no trace of a conversation is committed. | [ ] |
| Capacity limits, monitoring, access, retention, remaining work | [The capacity and retention section](limitations.md#capacity-and-retention) and [the deployment section](limitations.md#deployment) of the limitations report; [the access controls in SECURITY.md](../SECURITY.md). Stated limitation: no monitoring or alerting record is committed; the capacity section says what was and was not measured. | [ ] |
| Explanations from records | The audit timeline of a synthetic conversation, identifiers masked. Stated limitation: no audit timeline of a synthetic conversation is committed; [the audit record tests](../tests/test_service_contract_audit.py) cover what each record holds. | [ ] |
| Provided data only; labeled inputs; no card number or document-number shape in the understanding step's external request, with the rule's gaps disclosed | [The data-use section](../README.md#where-each-input-comes-from); [the request-capture test](../tests/test_request_capture_pii.py); [the security posture section of the limitations report](limitations.md#security-posture) | [x] |
| Authentication and per-customer access | [The authentication tests](../tests/test_auth_api.py), [the session middleware tests](../tests/test_session_auth_middleware.py) and [the tool contract tests](../tests/test_service_contract_tools.py) | [x] |
| Mock services documented | [The service contract](../contracts/README.md); [the authentication and sessions section of the README](../README.md#authentication-and-sessions); [the deployment limits](limitations.md#deployment) | [x] |
| The model does not invent policy; risk separated from policy | [The output verifier tests](../tests/test_verifier.py) and [the policy-answer tests](../tests/test_policy_answer.py); [the model card](../models/model_card.json) | [x] |
| Incremental processing proven | [The freshness tests](../tests/test_silver.py) | [x] |
| The judge rubric validated against human judgments, stricter than a single reviewer's opinion | The judge validation results: agreement of two raters with each other and with the judge. The validation was run, with two raters, and [its results](../reports/evaluation.md#9-judge-validation) show the judge's agreement with at least one rater below the 80% bar on every scored measure. Stated limitation: the judge is not validated and its scores are withheld. | [ ] |
| Disparity analysis and labeling notes | [The disparity section](../reports/evaluation.md#8-fairness-and-disparity); [the data limitations](limitations.md#data-limitations) | [x] |
| Public repository, deployed link, slides and video | The repository, deployed, slides and video sections above. Stated limitation: the slide and video rows above are not ticked. | [ ] |
| Evidence for each discipline | Rationale and documentation: [the README](../README.md) and [the limitations report](limitations.md). AI engineering: [the conversation layer and its evaluation](../reports/evaluation.md). Data analytics: [the workflow analysis](../reports/workflow-analysis.md). Data engineering: [the pipelines](../pipelines/README.md), [contracts](../contracts/README.md) and [reports](../reports/README.md). Machine learning: [the model card](../models/model_card.json) and [the experiment log](../models/experiments.jsonl); [the discipline map](../README.md#discipline-map) | [x] |

## Release verification

| Item | Evidence | Done |
|---|---|---|
| Everything above is verified before the freeze | This checklist with every row checked and its evidence linked. Stated limitation: the rows above that are not ticked each carry their reason. | [ ] |
| Nothing new is added after the freeze | The commit history after the freeze. Stated limitation: it applies after the freeze, which has not taken place at this commit. | [ ] |
| The release message carries the repository link, the deployed link, the slides and the video | The message as sent. Stated limitation: [the delivery message](delivery-message.md) is a template; the message as sent does not exist yet. | [ ] |
