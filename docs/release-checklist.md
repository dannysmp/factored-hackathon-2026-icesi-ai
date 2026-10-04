# Release checklist

This checklist states each obligation of a release and names the evidence a reviewer can open. A row is checked off only with its evidence linked. Every artifact that already exists is linked directly from its row. The evidence is produced for the release and linked at the release commit, so some artifacts named here — the rehearsal checklist, the slide content checklist and the two clean-account run records — are written as the release is prepared and have no link yet. The delivery date is 2026-10-05. Every row is verified before the freeze, 23:59 America/Bogota on 2026-10-04; the rows that check a live link are checked once more immediately before the release is announced, and that second check is recorded with its time.

## Repository

| Item | Evidence | Done |
|---|---|---|
| The repository is public | The repository page opens without signing in | [ ] |
| The full history has no secret or private record | The history scan report from the release commit, and the last diff read by a person | [ ] |
| No credential, key or environment file is tracked | The scan report; a listing of tracked files | [ ] |
| The README leads from the problem to a running demonstration without a question | A person who did not write it follows it from a clean checkout | [ ] |
| Every model card, the limitations and remaining-work report and the demonstration scripts are present | [The model card](../models/model_card.json), [the limitations and remaining-work report](limitations.md) and [the demonstration scripts](demo-scripts.md), each at the release commit | [ ] |
| Inputs are labeled real, de-identified, synthetic or team-generated | [The data-use section of the README](../README.md#where-each-input-comes-from) | [ ] |
| No card number appears in any request to an external model; a document number typed into free text is not detected, and that exception is disclosed | [The request-capture test](../tests/test_request_capture_pii.py) for the understanding step's request, and [the security posture section of the limitations report](limitations.md#security-posture) for the exception | [ ] |

## Deployed system

| Item | Evidence | Done |
|---|---|---|
| The deployed link opens over HTTPS with a valid certificate | Checked live at the freeze and again before the release is announced, with the time of each check recorded | [ ] |
| The three demonstration paths run in the browser, in Spanish and Portuguese, and the normal path in English | The rehearsal checklist, second run, all passing | [ ] |
| The demonstration notice and the data reference date are visible on every screen | The rehearsal checklist | [ ] |
| The console is shown as a read-only viewer | The rehearsal checklist | [ ] |
| The deployment, dispatched manually from the main branch, passes the smoke test | [The deploy workflow](../.github/workflows/deploy.yml) and [the manually dispatched run on main at 2cad12d, whose HTTPS smoke test and dashboard smoke test passed](https://github.com/dannysmp/factored-hackathon-2026-icesi-ai/actions/runs/37227276409) | [x] |
| The stack is reproduced from a clean account with the written commands, then torn down | The two clean-account run records, following the [deployment runbook](../infra/deployment-runbook.md) | [ ] |
| No long-lived cloud key exists in the repository or the pipeline | The pipeline configuration; the scan report | [ ] |
| The demonstration sign-in can be switched off, and the switch has been tried | [The runbook entry](../infra/README.md#turning-the-demonstration-sign-in-off), with the date of the trial | [ ] |

## Slides

| Item | Evidence | Done |
|---|---|---|
| Four to six slides | The slide file | [ ] |
| Every figure about the problem comes from the workflow analysis report, and every result from the evaluation report | The slide content checklist with the check recorded by role | [ ] |
| No secret value, administration screen, key or unmasked identifier appears | The slide content checklist | [ ] |
| The slides cover the problem with data, the architecture and its trade-offs, controlled automation and security, the evaluation results, and the limitations and route to operation | The slide file | [ ] |

## Video

| Item | Evidence | Done |
|---|---|---|
| The video shows the working solution end to end: the normal path, the ambiguous or unsupported path and the human-required path with the handoff and the console | The recording, with the time of each path noted | [ ] |
| The core architectural decisions are named by their record number and explained in a sentence each | The recording; the decision records | [ ] |
| No secret value, administration screen, key or unmasked identifier appears in the recording | A person watches the whole recording before the release is announced, and records the check by role | [ ] |
| The video is uploaded and opens from its link | The link, opened live at the freeze and again before the release is announced | [ ] |

## Requirements walk

Every requirement is checked with a link to its evidence.

| Requirement | Evidence to open | Done |
|---|---|---|
| Working system for a banking environment | The deployed link and the demonstration paths | [ ] |
| Focused workflow, end to end | The scope statement; the demonstration paths | [ ] |
| Data-backed problem selection | [The workflow analysis report](../reports/workflow-analysis.md) and `make analyze`, the command that regenerates it | [ ] |
| Baseline and improvement on the same workload | The evaluation report, proposed system against both baselines | [ ] |
| Privacy, explainability, fairness, reliability, scalability by design | The security and privacy, reliability and capacity sections; [the ASVS Level 1 checklist](asvs-level1-checklist.md); the disparity analysis in the evaluation report | [ ] |
| Explicit trade-offs | The decision records; the evaluation report | [ ] |
| Where AI and where deterministic logic | The decision records; the rule-based baseline comparison | [ ] |
| Three demonstration paths | [The demonstration scripts](demo-scripts.md); the rehearsal checklist | [ ] |
| Spanish and Portuguese, English added, language limits reported | The evaluation report by language; the limitations report | [ ] |
| Context, clarification and grounded answers | The conversation criteria results; the policy-question results | [ ] |
| Tools, and only verified actions reported | The tool layer tests; the verification results | [ ] |
| Controlled automation | The automation matrix; the authorization and confirmation tests | [ ] |
| Handoff packet | A packet from a synthetic conversation shown in the console with identifiers masked; the packet completeness check | [ ] |
| Repeatable data preparation | The pipeline commands, contracts, quality report and freshness fixture | [ ] |
| Learned component against a baseline | The model card, the experiment log and the pre-registration | [ ] |
| Held-out evaluation including failure conditions | The adversarial suite results | [ ] |
| Successful, unsafe, handoff, latency and cost with sample sizes | The evaluation report | [ ] |
| Outcome definitions honored | The metric definitions and the report | [ ] |
| Tracing, retries, safe fallback, reproducible setup | [The dialogue controller tests](../tests/test_dialogue_controller.py) and the [retry](../tests/test_reliability_retry.py), [breaker](../tests/test_reliability_breaker.py) and [tool port](../tests/test_reliability_tool_port.py) tests, run with `uv run pytest tests/test_dialogue_controller.py tests/test_reliability_retry.py tests/test_reliability_breaker.py tests/test_reliability_tool_port.py`; the quickstart | [ ] |
| Capacity limits, monitoring, access, retention, remaining work | The capacity, retention and remaining-work sections | [ ] |
| Explanations from records | The audit timeline of a synthetic conversation, identifiers masked | [ ] |
| Provided data only; labeled inputs; no card number externally, with the free-text document-number exception disclosed | [The data-use section](../README.md#where-each-input-comes-from); [the request-capture test](../tests/test_request_capture_pii.py); [the security posture section of the limitations report](limitations.md#security-posture) | [ ] |
| Authentication and per-customer access | The authorization tests | [ ] |
| Mock services documented | The contract and limitations of the case service and the demonstration sign-in | [ ] |
| The model does not invent policy; risk separated from policy | The grounding checks; the model card | [ ] |
| Incremental processing proven | The freshness fixture test | [ ] |
| The judge rubric validated against human judgments, stricter than a single reviewer's opinion | The judge validation results: agreement of two raters with each other and with the judge | [ ] |
| Disparity analysis and labeling notes | The disparity section of the evaluation report | [ ] |
| Public repository, deployed link, slides and video | The repository, deployed, slides and video sections above | [ ] |
| Evidence for each discipline | Rationale and documentation: the README and decision records. AI engineering: the conversation layer and its evaluation. Data analytics: the workflow analysis and the insights in the evaluation report. Data engineering: the pipeline, contracts and reports. Machine learning: the model card and the experiment log | [ ] |

## Release verification

| Item | Evidence | Done |
|---|---|---|
| Everything above is verified before the freeze | This checklist with every row checked and its evidence linked | [ ] |
| Nothing new is added after the freeze | The commit history after the freeze | [ ] |
| The release message carries the repository link, the deployed link, the slides and the video | The message as sent | [ ] |
