# Evaluation Comparison

This document compares four full evaluation runs of the same 135-case golden set (same turns and expectations) and the same three systems: the proposed system (P), the keyword-classifier variant of the same application (B0) and the naive tool-calling agent (B1). It reads the generated report (`evaluation.md`) as the current result and sets it beside the three earlier ones. Every figure is measured offline against the golden set. None is a production figure.

## 1. Runs

| Run | Commit | NLU prompt version | Policy corpus version | Judge cost (USD) | Repetitions |
| --- | --- | --- | --- | --- | --- |
| Baseline | 51a9fbf | 2 | 2 | 0.7015 | P 3, B0 1, B1 1 |
| Interim | 5696852 | 3 | 2 | 0.7087 | P 3, B0 1, B1 1 |
| Previous | 6cea3b4 | 5 | 2 | 0.7197 | P 3, B0 1, B1 1 |
| Current | a73f4bc | 5 | 2 | 0.7159 | P 3, B0 1, B1 1 |

The previous run completed without a crash and without an errored case; the run log holds no stack trace. The run log and the turn-by-turn replays cited below are local working files and are not committed. All four runs used the same understanding, rendering and judge models (the generated report lists them).

The interim run executed on commit a7a1670 with the changes of 5696852 applied; 5696852 is not part of the main history. It ran before later changes to the naive agent that stop its read tools from crashing a run, record a tool call with a missing argument as a case failure, and reject mistyped tool arguments instead of coercing them. No case errored in the interim run and no call took the code those changes touched, so none of its B1 figures would change. This was established by reading the run's log, not by re-running it.

The generated report records the commit it was produced from. The previous run executed from a separate checkout of 6cea3b4; the report's version table was set to that commit afterwards, because the harness had read it from the directory it was launched in. The current run measures a73f4bc: the same 135 cases, the same scorer, the same thresholds and the same judge bar, with the evaluation code, the golden set, the scoring and the judge unchanged between the two commits. The code differs by every change merged between them, which include conversation-layer fixes as well as the seed fix below, so a movement in a case that does not depend on the seed is not attributed to a single change.

### The previous missed-transfer rate was measured on a stale seed

The previous run's missed-transfer rate for P, 0.152, was measured against an operational seed that predated the repeat-complainer flag: the customers table loaded with no customer marked as a repeat complainer, because the seed loader inserted only the columns the seed file carried and the column defaults to false. The load succeeded silently and the repeat-complainer routing rule could never fire. hr-repeat-es-01 and hr-repeat-es-02 missed in every run for that reason, and hr-repeat-en-01 in one. The loader now refuses a seed without the column, and the current run used a rebuilt seed with 28 repeat complainers. The previous figure measured a defective environment, not the policy rule; both figures are shown below.

## 2. Headline metrics

Sample sizes are the same for every run and every system. Safe automated resolution, containment, attempted share, latency and cost use the 103 in-scope (non-adversarial) cases. Escalation quality and missed transfers use the 22 in-scope cases where a person should take over. Unnecessary transfers use the remaining 81. Unsafe outcomes use all 135 cases. Where P ran three times, every row except unsafe outcomes, latency and cost shows the mean with the range across runs; those three rows show the mean only, and the latency ranges are quoted in the text below.

### System P

| Metric | Baseline | Interim | Previous | Current |
| --- | --- | --- | --- | --- |
| Safe automated resolution (n=103) | 0.469 (0.466-0.476) | 0.521 (0.515-0.524) | 0.725 (0.718-0.728) | 0.735 (0.728-0.738) |
| Containment (n=103) | 0.799 (0.796-0.806) | 0.793 (0.786-0.796) | 0.809 (0.806-0.816) | 0.790 (0.786-0.796) |
| Escalation quality (n=22) | 0.545 (0.545-0.545) | 0.561 (0.545-0.591) | 0.727 (0.727-0.727) | 0.833 (0.818-0.864) |
| Missed transfers (n=22) | 0.106 (0.091-0.136) | 0.076 (0.045-0.091) | 0.152 (0.136-0.182) | 0.061 (0.045-0.091) |
| Unnecessary transfers (n=81) | 0.012 (0.012-0.012) | 0.012 (0.012-0.012) | 0.012 (0.012-0.012) | 0.012 (0.012-0.012) |
| Unsafe outcomes (n=135) | 0.000 | 0.000 | 0.000 | 0.000 |
| Latency p50 (s) | 2.650 | 2.702 | 2.558 | 2.912 |
| Latency p95 (s) | 3.783 | 4.319 | 4.402 | 4.549 |
| Cost per attempted case (USD) | 0.004 | 0.004 | 0.005 | 0.004 |

### System B0

| Metric | Baseline | Interim | Previous | Current |
| --- | --- | --- | --- | --- |
| Safe automated resolution (n=103) | 0.291 | 0.291 | 0.330 | 0.330 |
| Containment (n=103) | 0.845 | 0.845 | 0.845 | 0.845 |
| Escalation quality (n=22) | 0.455 | 0.455 | 0.455 | 0.455 |
| Missed transfers (n=22) | 0.545 | 0.545 | 0.545 | 0.545 |
| Unnecessary transfers (n=81) | 0.074 | 0.074 | 0.074 | 0.074 |
| Unsafe outcomes (n=135) | 0.000 | 0.000 | 0.000 | 0.000 |
| Latency p50 (s) | 0.034 | 0.032 | 0.027 | 0.073 |
| Latency p95 (s) | 0.053 | 0.050 | 0.046 | 0.114 |
| Cost per attempted case (USD) | 0.000 | 0.000 | 0.000 | 0.000 |

### System B1

| Metric | Baseline | Interim | Previous | Current |
| --- | --- | --- | --- | --- |
| Safe automated resolution (n=103) | 0.398 | 0.427 | 0.369 | 0.369 |
| Containment (n=103) | 0.757 | 0.796 | 0.757 | 0.738 |
| Escalation quality (n=22) | 0.273 | 0.273 | 0.273 | 0.364 |
| Missed transfers (n=22) | 0.091 | 0.091 | 0.091 | 0.045 |
| Unnecessary transfers (n=81) | 0.062 | 0.012 | 0.062 | 0.074 |
| Unsafe outcomes (n=135) | 0.000 | 0.000 | 0.000 | 0.000 |
| Latency p50 (s) | 5.923 | 5.288 | 5.105 | 6.045 |
| Latency p95 (s) | 11.582 | 11.264 | 12.217 | 12.898 |
| Cost per attempted case (USD) | 0.008 | 0.008 | 0.008 | 0.009 |

No run produced an unsafe outcome for any system. An outcome counts as unsafe only when a reply contains a card-number-like string or announces a handoff that no recorded ticket backs. Filing a case the customer should not have been offered, or failing to hand off, is a wrong outcome, not an unsafe one.

### Reading the tables

- **Proposed system.** Safe automated resolution rose from 0.469 to 0.735; between the previous and current runs the change, 0.725 to 0.735, is inside the run-to-run ranges, which touch (0.718-0.728 and 0.728-0.738), so it is not claimed as an improvement. Escalation quality rose from 0.727 to 0.833 and containment fell from 0.809 to 0.790: cases that should reach a person now do, so fewer are contained. Missed transfers fell from 0.152 to 0.061 (range 0.045-0.091). **P does not lead on this metric:** B1 missed 0.045 on its single run, below P's 0.061, and P's range includes B1's value, so the two cannot be told apart on this evidence, but P does not come out ahead. B0 missed 0.545. The cases that still miss a required transfer are listed in section 3; they are wrong outcomes, not unsafe ones as defined above.
- **B0 is not an unchanged control.** It shares the application's conversation controller and differs only in its understanding step, so every controller change moves it as well. Its failing count fell from 86 to 80 between the interim and previous runs and stayed at 80 in the current one. Its escalation figures are identical in all four runs.
- **B1 is a single run per cell.** The naive agent is nondeterministic and was run once, so its differences between runs are not evidence of change. Between the previous and current runs its containment fell from 0.757 to 0.738, its escalation quality rose from 0.273 to 0.364, its missed transfers fell from 0.091 to 0.045 and its unnecessary transfers rose from 0.062 to 0.074: one-case differences.
- **Latency and cost.** P is slower in the current run: the median rose from 2.558 s to 2.912 s and the 95th percentile from 4.402 s to 4.549 s. B0, which makes no model call, and B1 are slower as well (B0 median 0.027 s to 0.073 s, B1 5.105 s to 6.045 s), so the run environment is the likelier cause, though this was not isolated; the latency of the two runs is not comparable like for like. P's median range was 2.724-3.012 s and its 95th percentile range 4.342-4.816 s. Cost per attempted case moved from 0.005 to 0.004 USD; the current range, 0.004-0.005, overlaps the previous value.

## 3. Failing cases

The count below is the number of golden cases in the report's failure gallery whose outcome was incorrect in the last repetition of the run.

| System | Baseline | Interim | Previous | Current |
| --- | --- | --- | --- | --- |
| P | 47 | 34 | 13 | 11 |
| B0 | 86 | 86 | 80 | 80 |
| B1 | 54 | 45 | 53 | 50 |

### System P by case family

| Family | Cases | Baseline | Interim | Previous | Current |
| --- | --- | --- | --- | --- | --- |
| Normal, unrecognized charge | 10 | 8 | 8 | 2 | 1 |
| Normal, wrong amount | 7 | 7 | 5 | 0 | 0 |
| Normal, duplicate | 5 | 5 | 2 | 1 | 1 |
| Normal, service not received | 7 | 4 | 4 | 1 | 1 |
| Normal, policy question | 12 | 0 | 0 | 0 | 0 |
| Ambiguous | 17 | 7 | 7 | 1 | 1 |
| Unsupported | 13 | 0 | 0 | 0 | 0 |
| Human required | 22 | 2 | 2 | 3 | 2 |
| Multilingual | 10 | 2 | 2 | 1 | 1 |
| Adversarial, tool failure | 6 | 6 | 0 | 0 | 0 |
| Adversarial, bad data | 6 | 6 | 4 | 4 | 4 |
| Adversarial, other | 20 | 0 | 0 | 0 | 0 |

### Baseline to previous

Fixed (37): adv-baddata-en-01, adv-baddata-es-02, adv-toolfail-en-01, adv-toolfail-es-01, adv-toolfail-es-02, adv-toolfail-es-03, adv-toolfail-pt-01, adv-toolfail-pt-02, amb-missing-pt-02, amb-twointent-en-01, amb-twointent-es-01, amb-twointent-es-02, amb-twointent-pt-02, amb-vague-es-01, hr-amt-es-03, multi-enes-03, norm-filed-duplicate-pt-02, norm-filed-duplicate-pt-03, norm-filed-duplicate-pt-04, norm-filed-duplicate-pt-05, norm-filed-service-es-02, norm-filed-service-es-05, norm-filed-service-es-06, norm-filed-service-es-07, norm-filed-unrecognized-en-02, norm-filed-unrecognized-en-04, norm-filed-unrecognized-pt-01, norm-filed-unrecognized-pt-03, norm-filed-unrecognized-pt-05, norm-filed-unrecognized-pt-06, norm-filed-wrongamt-es-01 to norm-filed-wrongamt-es-07.

Newly failing (3): hr-repeat-es-01, hr-repeat-es-02 and norm-filed-service-es-03. The count uses the last repetition, so a case that failed in some earlier repetition reads as newly failing: norm-filed-service-es-03 failed in two of the three baseline repetitions and hr-repeat-es-02 in one.

Still failing (10): adv-baddata-es-01, adv-baddata-es-03, adv-baddata-pt-01, adv-baddata-pt-02, amb-twointent-pt-01, hr-amt-es-01, multi-espt-03, norm-filed-duplicate-pt-01, norm-filed-unrecognized-en-03 and norm-filed-unrecognized-pt-02.

### Interim to previous

Fixed (25): amb-missing-pt-02, amb-twointent-en-01, amb-twointent-es-01, amb-twointent-es-02, amb-twointent-pt-02, amb-vague-es-01, hr-amt-es-03, multi-espt-04, norm-filed-duplicate-pt-03, norm-filed-duplicate-pt-05, norm-filed-service-es-02, norm-filed-service-es-05, norm-filed-service-es-06, norm-filed-service-es-07, norm-filed-unrecognized-en-02, norm-filed-unrecognized-en-04, norm-filed-unrecognized-pt-01, norm-filed-unrecognized-pt-03, norm-filed-unrecognized-pt-05, norm-filed-unrecognized-pt-06, norm-filed-wrongamt-es-01, norm-filed-wrongamt-es-02, norm-filed-wrongamt-es-04, norm-filed-wrongamt-es-06 and norm-filed-wrongamt-es-07.

Newly failing (4): hr-repeat-es-01, hr-repeat-es-02, norm-filed-duplicate-pt-01 and norm-filed-service-es-03.

Still failing (9): adv-baddata-es-01, adv-baddata-es-03, adv-baddata-pt-01, adv-baddata-pt-02, amb-twointent-pt-01, hr-amt-es-01, multi-espt-03, norm-filed-unrecognized-en-03 and norm-filed-unrecognized-pt-02.

### Previous to current

Fixed (3): hr-repeat-es-01, norm-filed-service-es-03 and norm-filed-unrecognized-pt-02.

Newly failing (1): norm-filed-service-es-04. Its cause was not established.

Still failing (10): adv-baddata-es-01, adv-baddata-es-03, adv-baddata-pt-01, adv-baddata-pt-02, amb-twointent-pt-01, hr-amt-es-01, hr-repeat-es-02, multi-espt-03, norm-filed-duplicate-pt-01 and norm-filed-unrecognized-en-03.

hr-repeat-es-01 is handed off in all three current runs, hr-repeat-en-01 is handed off in all three, and hr-repeat-es-02 is handed off in two of three, which is why it is still listed: the failing gallery reads the last run. These changes follow the seed fix described above. Single-case movements outside the repeat-complainer cases are within the run-to-run variation recorded for each run and are not attributed to a cause.

### Why the 11 current failures fail

Each cause below was established by replaying the case turn by turn and, where the language model was involved, repeating the understanding call on the same sentence.

| Case | Cause |
| --- | --- |
| norm-filed-service-es-04 | Not established. It passed in the previous run's last repetition and fails in the current one; single-case movements outside the repeat-complainer cases are within the run-to-run variation. |
| norm-filed-duplicate-pt-01 | A statement of a transfer already made ("Fiz uma transferência de ...") is read as a request to list transactions, so the second turn does not file. Consistent across repetitions. |
| hr-amt-es-01 | The second turn ("quiero reportar ese retiro") expects a handoff and none happens (observed_escalation is false). The language-understanding step reads the turn as unclear in four of four replays, so the conversation never reaches the policy check; the amount rule hands the case off correctly when it is reached. No prompt change was made. |
| hr-repeat-es-02 | Handed off in two of three runs and missed in the last. Four later isolated replays on the same seed handed off every time. The failing run's transcript was not captured, so its cause is not established. |
| norm-filed-unrecognized-en-03 | The golden turn ("That wasn't me, I'd like to report it.") expects a filing; the system hands off, as the policy prescribes for fraud. The goldens use the same verb elsewhere without the same ambiguity: the duplicate-charge cases norm-filed-duplicate-pt-01 and norm-filed-duplicate-pt-03 expect a filing after "quero reportar" because the reason is stated in the same sentence, and the hr-repeat cases expect a handoff because of the repeat-complainer flag, whatever the verb. Here nothing but "report" separates a fraud report from a filing. Which reading is right is a product decision; the case is ambiguous as worded. |
| adv-baddata-es-03, adv-baddata-pt-02 | The transaction is found and its amount is unknown. The handoff comes after the customer confirms the found transaction, but the case ends at the confirmation question. |
| adv-baddata-es-01, adv-baddata-pt-01 | The golden case is a single turn, but the system asks up to two clarifying questions before handing off. |
| amb-twointent-pt-01 | The request is answered as a policy question. Whether it should instead ask which of the two intents to take first is a design decision, not a defect found here. |
| multi-espt-03 | The reply comes in Portuguese where Spanish is expected. |

### Golden wording

Three golden cases are worded so that they measure something other than the behavior they describe: norm-filed-unrecognized-en-03 (the verb "report" can be read as a fraud report or as a request to file), adv-baddata-es-01 and adv-baddata-pt-01 (a single-turn handoff where the system asks up to two clarifying questions first). The P failing count therefore falls by up to three for wording reasons, not because of any change to the product, once those cases are reworded. adv-baddata-es-03 and adv-baddata-pt-02 also have a single turn, but they are not reworded: the transaction is found, its amount is unknown, and the system ends at a confirmation question where the case expects a handoff, so the failure is a behavior of the system. This report uses the golden set as it stood at the current commit.

### Run-to-run variability of P

| Run | Cases that changed outcome between its three repetitions |
| --- | --- |
| Baseline | norm-filed-unrecognized-pt-02, norm-filed-duplicate-pt-03, norm-filed-service-es-03, hr-repeat-es-02 |
| Interim | hr-amt-es-03, multi-espt-04 |
| Previous | hr-repeat-en-01, multi-espt-02 |
| Current | hr-repeat-es-02, multi-espt-02 |

## 4. Fairness

The previous run also reported outcomes by customer segment. The interim run could not: the customer table the segments come from was not available to it. The current three-run measurement did not compute it: the working tree it was launched from held no pipeline output to read each customer's segment from, and the per-case outcomes were not stored, so the slice could not be derived afterwards. The evaluation report states where the segment slice stands. Its language, country and accent-phrasing slices are computed. Slice sizes are small. No slice differs from its dimension by more than sampling noise, and a slice with few cases is rarely flagged, so the absence of a flag is not evidence of equal treatment.

## 5. Quality scoring

Grounding, language quality and clarification all stay human-only. A dimension is demoted when the judge agrees with either rater on fewer than 80 % of cases, so no judge mean is reported. For clarification the judge agrees with Rater 1 on 1 of 6 cases and with Rater 2 on 6 of 6; the two raters agree with each other on 1 of 6. The two raters' own means over the validation sample are in the generated report.

## 6. Limitations

- The validation sample is not the judged run. Rater means describe the 50 reply-level sheets (6 for clarification), not the 135 cases the judge scored.
- The clarification validation covers a different set of cases from the ones the judge scored. Raters scored clarification on the 6 ambiguous cases in the sample; the judge scored 27 cases (those 6 and 21 others, which both raters left unscored). Agreement is computed over the 6 only. The per-case rater and judge scores are local working files, not committed.
- The B1 figures are one run each. The P figures are three runs; their ranges are a measure of nondeterminism, not a confidence interval.
- Failure counts are for the last repetition of a run, not the union across repetitions.
- Conversation changes merged after the current commit are not measured here. Every figure of the current run describes commit a73f4bc and nothing later.
- The decline path is measured by no golden case: the 29 cases that expect a filing end at the confirmation question, and none answers it with a refusal.
- The golden set is team-generated. Section 3 records three cases whose wording does not measure the behavior they describe.
