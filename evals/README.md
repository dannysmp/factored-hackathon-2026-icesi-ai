# Evaluation harness

The evaluation package measures a dispute-intake system against a fixed, held-out workload: a golden set of scripted scenarios with an expected outcome, three system variants, deterministic scoring, an LLM judge checked against human raters, fairness slicing, repeated-run variability and a generated report. Every module opens with a docstring stating its purpose, scope and limitations; this file is the map.

## Contents

| Path | Purpose |
|---|---|
| `models.py` | The `Case` record and its vocabulary: category, language, provenance, expected outcome |
| `golden/` | The authored cases, one module per category, and the generated case-sheet CSV files in `golden/cases/` |
| `runner/` | Drives cases through a system variant: proposed system (P), keyword baseline (B0), naive tool-calling baseline (B1) |
| `scoring.py`, `oracle.py` | Deterministic per-case verdicts, and a check that no stored case is one the policy engine would have refused |
| `metrics.py`, `cost.py`, `facts.py` | Headline metrics, per-case model cost read from the run's logs, and the grounding facts a reply is judged against |
| `judge.py`, `judge_validation.py`, `h4_judge_validation.py` | The LLM judge, its agreement with two human raters, and the command that scores the returned rater sheets |
| `fairness.py`, `repeated_runs.py`, `profiles.py`, `injector.py` | Per-slice disparity, run-to-run variability, the country and segment of each case's customer, and injected tool failures |
| `report.py`, `cli.py` | The generated evaluation report and the command line that produces it |

## Running

`make evaluate SYSTEM={P|B0|B1} [SMOKE=1]` runs one variant and logs its headline metrics. `make evaluate FULL=1 [SMOKE=1]` runs every variant and writes `reports/evaluation.md`. `SMOKE=1` narrows the case set to the 16-case injection and authorization subset that CI runs. The store must already hold the operational seed (`make load-seed`) and, for the cases that need it, the evaluation bank (`make load-eval-bank`). `make judge-validation` scores the returned rater sheets and patches the report.

The case-sheet CSV files are generated from the case modules: `python -m evals.golden.case_sheet` rewrites them and `--check` fails when they are stale.

## Golden-set adjudication

An independent rater read all 135 cases and judged, for each, whether the declared expected outcome class and expected reason code are right for the scenario. The rater's sheet is kept outside the repository; this section records its result. Of the 135 cases, 125 are team-generated synthetic and 10 are injected (the poisoned-input and bad-data adversarial cases); the rater checked labels only, not that provenance.

The outcome class of a case is derived from its other columns: reason code `eligible` gives `ELIGIBLE`; a code starting `escalate` gives `ESCALATE`; any other non-empty code gives `INELIGIBLE`; otherwise the adversarial safe behavior, or the reply intent, upper-cased.

### Agreement

| Measure | Result |
|---|---|
| Cases rated | 135 of 135 |
| Marked `agree` | 117 (86.7%) |
| Marked `correct` | 18 (13.3%) |
| Agreement on outcome class and reason code, counting a correction that restates the declared label as agreement | 135 of 135 (100%) |
| Labels changed | 0 |

All 18 `correct` entries name the same outcome class (`ESCALATE`) and the same reason code the case already declares, and give a one-line rationale. The team reads them as confirmations of the declared label, because each restates it and none proposes a different one; the rater has not been asked to confirm that reading. Applying every one of them therefore leaves every case file unchanged. The strict figure (117 of 135) counts them as disagreements; the lenient figure (135 of 135) counts them as agreement. Neither is a chance-corrected statistic: there is one rater, and the labels under review were derived from the case columns rather than assigned independently.

| Reason code | Cases marked `correct` | Case identifiers |
|---|---|---|
| `escalate_fraud_claim` | 6 | `hr-fraud-es-01`, `hr-fraud-es-02`, `hr-fraud-es-03`, `hr-fraud-pt-01`, `hr-fraud-pt-02`, `hr-fraud-en-01` |
| `escalate_amount_above_threshold` | 6 | `hr-amt-es-01`, `hr-amt-es-02`, `hr-amt-es-03`, `hr-amt-pt-01`, `hr-amt-pt-02`, `hr-amt-en-01` |
| `escalate_repeat_complainer` | 4 | `hr-repeat-es-01`, `hr-repeat-es-02`, `hr-repeat-pt-01`, `hr-repeat-en-01` |
| `escalate_amount_unknown` | 2 | `adv-baddata-es-03`, `adv-baddata-pt-02` |

The counts of the set (135 cases; 41 normal, 17 ambiguous, 13 unsupported, 22 human-required, 10 multilingual, 32 adversarial) and the sample drawn for the judge validation are unchanged by the adjudication.

### Coverage gap

No case declares an `INELIGIBLE` outcome. Every reason code other than `eligible` and the `escalate_*` codes (`product_out_of_scope`, `transaction_type_not_disputable`, `transaction_declined`, `transaction_pending`, `transaction_reversed`, `transaction_date_in_future`, `filing_window_expired`, `duplicate_open_case`) is absent from the 135 cases, so the set does not exercise a single denial of an ineligible filing. The policy engine's own tests cover those codes; the evaluation does not. The set is reported as it stands, and the report's limitations section states the gap.
