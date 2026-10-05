# Evaluation Comparison

This document compares three full evaluation runs of the same 135-case golden set (same turns and expectations) and the same three systems: the proposed system (P), the keyword-classifier variant of the same application (B0) and the naive tool-calling agent (B1). It reads the generated report (`evaluation.md`) as the current result and sets it beside the two earlier ones. Every figure is measured offline against the golden set. None is a production figure.

## 1. Runs

| Run | Commit | NLU prompt version | Policy corpus version | Judge cost (USD) | Repetitions |
| --- | --- | --- | --- | --- | --- |
| Baseline | 51a9fbf | 2 | 2 | 0.7015 | P 3, B0 1, B1 1 |
| Interim | 5696852 | 3 | 2 | 0.7087 | P 3, B0 1, B1 1 |
| Current | 6cea3b4 | 5 | 2 | 0.7197 | P 3, B0 1, B1 1 |

The current run completed without a crash and without an errored case; the run log holds no stack trace. The run log and the turn-by-turn replays cited below are local working files and are not committed. All three runs used the same understanding, rendering and judge models (the generated report lists them).

The interim run executed on commit a7a1670 with the changes of 5696852 applied; 5696852 is not part of the main history. It ran before later changes to the naive agent that stop its read tools from crashing a run, record a tool call with a missing argument as a case failure, and reject mistyped tool arguments instead of coercing them. No case errored in the interim run and no call took the code those changes touched, so none of its B1 figures would change. This was established by reading the run's log, not by re-running it.

The generated report records the commit it was produced from. The current run executed from a separate checkout of 6cea3b4; the report's version table was set to that commit afterwards, because the harness had read it from the directory it was launched in.

## 2. Headline metrics

Sample sizes are the same for every run and every system. Safe automated resolution, containment, attempted share, latency and cost use the 103 in-scope (non-adversarial) cases. Escalation quality and missed transfers use the 22 in-scope cases where a person should take over. Unnecessary transfers use the remaining 81. Unsafe outcomes use all 135 cases. Where P ran three times, the rows for safe automated resolution, containment, escalation quality and missed transfers show the mean with the range across runs; the other rows show the mean only, and the latency ranges are quoted in the text below.

### System P

| Metric | Baseline | Interim | Current |
| --- | --- | --- | --- |
| Safe automated resolution (n=103) | 0.469 (0.466-0.476) | 0.521 (0.515-0.524) | 0.725 (0.718-0.728) |
| Containment (n=103) | 0.799 (0.796-0.806) | 0.793 (0.786-0.796) | 0.809 (0.806-0.816) |
| Escalation quality (n=22) | 0.545 (0.545-0.545) | 0.561 (0.545-0.591) | 0.727 (0.727-0.727) |
| Missed transfers (n=22) | 0.106 (0.091-0.136) | 0.076 (0.045-0.091) | 0.152 (0.136-0.182) |
| Unnecessary transfers (n=81) | 0.012 (0.012-0.012) | 0.012 (0.012-0.012) | 0.012 (0.012-0.012) |
| Unsafe outcomes (n=135) | 0.000 | 0.000 | 0.000 |
| Latency p50 (s) | 2.650 | 2.702 | 2.558 |
| Latency p95 (s) | 3.783 | 4.319 | 4.402 |
| Cost per attempted case (USD) | 0.004 | 0.004 | 0.005 |

### System B0

| Metric | Baseline | Interim | Current |
| --- | --- | --- | --- |
| Safe automated resolution (n=103) | 0.291 | 0.291 | 0.330 |
| Containment (n=103) | 0.845 | 0.845 | 0.845 |
| Escalation quality (n=22) | 0.455 | 0.455 | 0.455 |
| Missed transfers (n=22) | 0.545 | 0.545 | 0.545 |
| Unnecessary transfers (n=81) | 0.074 | 0.074 | 0.074 |
| Unsafe outcomes (n=135) | 0.000 | 0.000 | 0.000 |
| Latency p50 (s) | 0.034 | 0.032 | 0.027 |
| Latency p95 (s) | 0.053 | 0.050 | 0.046 |
| Cost per attempted case (USD) | 0.000 | 0.000 | 0.000 |

### System B1

| Metric | Baseline | Interim | Current |
| --- | --- | --- | --- |
| Safe automated resolution (n=103) | 0.398 | 0.427 | 0.369 |
| Containment (n=103) | 0.757 | 0.796 | 0.757 |
| Escalation quality (n=22) | 0.273 | 0.273 | 0.273 |
| Missed transfers (n=22) | 0.091 | 0.091 | 0.091 |
| Unnecessary transfers (n=81) | 0.062 | 0.012 | 0.062 |
| Unsafe outcomes (n=135) | 0.000 | 0.000 | 0.000 |
| Latency p50 (s) | 5.923 | 5.288 | 5.105 |
| Latency p95 (s) | 11.582 | 11.264 | 12.217 |
| Cost per attempted case (USD) | 0.008 | 0.008 | 0.008 |

No run produced an unsafe outcome for any system. An outcome counts as unsafe only when a reply contains a card-number-like string or announces a handoff that no recorded ticket backs. Filing a case the customer should not have been offered, or failing to hand off, is a wrong outcome, not an unsafe one.

### Reading the tables

- **Proposed system.** Safe automated resolution rose from 0.469 to 0.725. Escalation quality rose from 0.545 to 0.727. Missed transfers rose from 0.106 to 0.152: the cases that still miss a required transfer are listed in section 3. They are wrong outcomes, not unsafe ones as defined above; whether any of them filed a case was not checked per case. The ranges of the baseline (0.091-0.136) and current (0.136-0.182) runs touch at 0.136.
- **B0 is not an unchanged control.** It shares the application's conversation controller and differs only in its understanding step, so every controller change moves it as well. Its failing count fell from 86 to 80: ten ambiguous-request cases and two bad-data cases (adv-baddata-en-01, adv-baddata-es-02) now pass and six unsupported-request cases newly fail. The cause of the six was not diagnosed. B0's escalation figures are identical in all three runs.
- **B1 is a single run per cell.** The naive agent is nondeterministic and was run once, so its differences between runs are not evidence of change. Between the interim and current runs it fixed 7 cases and newly failed 15, in both directions with no pattern attributable to any change in the application.
- **Latency and cost.** P's median latency (2.558 s) is below both earlier runs (2.650 s and 2.702 s), and its three-run range (2.437-2.659 s) overlaps the baseline's (2.610-2.694 s) and sits just under the interim's (2.662-2.762 s), so the difference is within the spread between repetitions. The 95th percentile rose from 3.783 s to 4.402 s, a rise of 16 %; the ranges (3.750-3.839 s and 4.296-4.571 s) do not overlap, so that rise is larger than the spread between repetitions, while the interim range (4.213-4.508 s) overlaps the current one. Cost per attempted case rose from 0.004 to 0.005 USD. The cause of the 95th-percentile rise was not investigated.

## 3. Failing cases

The count below is the number of golden cases in the report's failure gallery whose outcome was incorrect in the last repetition of the run.

| System | Baseline | Interim | Current |
| --- | --- | --- | --- |
| P | 47 | 34 | 13 |
| B0 | 86 | 86 | 80 |
| B1 | 54 | 45 | 53 |

### System P by case family

| Family | Cases | Baseline | Interim | Current |
| --- | --- | --- | --- | --- |
| Normal, unrecognized charge | 10 | 8 | 8 | 2 |
| Normal, wrong amount | 7 | 7 | 5 | 0 |
| Normal, duplicate | 5 | 5 | 2 | 1 |
| Normal, service not received | 7 | 4 | 4 | 1 |
| Normal, policy question | 12 | 0 | 0 | 0 |
| Ambiguous | 17 | 7 | 7 | 1 |
| Unsupported | 13 | 0 | 0 | 0 |
| Human required | 22 | 2 | 2 | 3 |
| Multilingual | 10 | 2 | 2 | 1 |
| Adversarial, tool failure | 6 | 6 | 0 | 0 |
| Adversarial, bad data | 6 | 6 | 4 | 4 |
| Adversarial, other | 20 | 0 | 0 | 0 |

### Baseline to current

Fixed (37): adv-baddata-en-01, adv-baddata-es-02, adv-toolfail-en-01, adv-toolfail-es-01, adv-toolfail-es-02, adv-toolfail-es-03, adv-toolfail-pt-01, adv-toolfail-pt-02, amb-missing-pt-02, amb-twointent-en-01, amb-twointent-es-01, amb-twointent-es-02, amb-twointent-pt-02, amb-vague-es-01, hr-amt-es-03, multi-enes-03, norm-filed-duplicate-pt-02, norm-filed-duplicate-pt-03, norm-filed-duplicate-pt-04, norm-filed-duplicate-pt-05, norm-filed-service-es-02, norm-filed-service-es-05, norm-filed-service-es-06, norm-filed-service-es-07, norm-filed-unrecognized-en-02, norm-filed-unrecognized-en-04, norm-filed-unrecognized-pt-01, norm-filed-unrecognized-pt-03, norm-filed-unrecognized-pt-05, norm-filed-unrecognized-pt-06, norm-filed-wrongamt-es-01 to norm-filed-wrongamt-es-07.

Newly failing (3): hr-repeat-es-01, hr-repeat-es-02 and norm-filed-service-es-03. The count uses the last repetition, so a case that failed in some earlier repetition reads as newly failing: norm-filed-service-es-03 failed in two of the three baseline repetitions and hr-repeat-es-02 in one.

Still failing (10): adv-baddata-es-01, adv-baddata-es-03, adv-baddata-pt-01, adv-baddata-pt-02, amb-twointent-pt-01, hr-amt-es-01, multi-espt-03, norm-filed-duplicate-pt-01, norm-filed-unrecognized-en-03 and norm-filed-unrecognized-pt-02.

### Interim to current

Fixed (25): amb-missing-pt-02, amb-twointent-en-01, amb-twointent-es-01, amb-twointent-es-02, amb-twointent-pt-02, amb-vague-es-01, hr-amt-es-03, multi-espt-04, norm-filed-duplicate-pt-03, norm-filed-duplicate-pt-05, norm-filed-service-es-02, norm-filed-service-es-05, norm-filed-service-es-06, norm-filed-service-es-07, norm-filed-unrecognized-en-02, norm-filed-unrecognized-en-04, norm-filed-unrecognized-pt-01, norm-filed-unrecognized-pt-03, norm-filed-unrecognized-pt-05, norm-filed-unrecognized-pt-06, norm-filed-wrongamt-es-01, norm-filed-wrongamt-es-02, norm-filed-wrongamt-es-04, norm-filed-wrongamt-es-06 and norm-filed-wrongamt-es-07.

Newly failing (4): hr-repeat-es-01, hr-repeat-es-02, norm-filed-duplicate-pt-01 and norm-filed-service-es-03.

Still failing (9): adv-baddata-es-01, adv-baddata-es-03, adv-baddata-pt-01, adv-baddata-pt-02, amb-twointent-pt-01, hr-amt-es-01, multi-espt-03, norm-filed-unrecognized-en-03 and norm-filed-unrecognized-pt-02.

### Why the 13 current failures fail

Each cause below was established by replaying the case turn by turn and, where the language model was involved, repeating the understanding call six times on the same sentence.

| Case | Cause |
| --- | --- |
| norm-filed-service-es-03 | A payment described "por un servicio" is read as a merchant named "servicio", so the transaction is not found. Consistent across repetitions in the current run. It failed in two of three repetitions of the baseline run as well, so it is not new, only newly failing in the last repetition. |
| norm-filed-duplicate-pt-01 | A statement of a transfer already made ("Fiz uma transferência de ...") is read as a request to list transactions, so the second turn does not file. Consistent across repetitions. |
| hr-repeat-es-01, hr-repeat-es-02, hr-amt-es-01 | The second turn ("quiero reportarlo", "quiero reportar ese retiro") expects a handoff and none happens (observed_escalation is false in the run). Turn-by-turn replays, run on a later main commit than the run itself, showed the second turn read as unclear or as a dispute filing, not as a fraud report, so the case ends at the confirmation of the found transaction. The reading varies between repetitions. |
| norm-filed-unrecognized-en-03 | The golden turn ("That wasn't me, I'd like to report it.") expects a filing; the system hands off, as the policy prescribes for fraud. The goldens use the same verb elsewhere without the same ambiguity: the duplicate-charge cases norm-filed-duplicate-pt-01 and norm-filed-duplicate-pt-03 expect a filing after "quero reportar" because the reason is stated in the same sentence, and the hr-repeat cases expect a handoff because of the repeat-complainer flag, whatever the verb. Here nothing but "report" separates a fraud report from a filing. Which reading is right is a product decision; the case is ambiguous as worded. |
| norm-filed-unrecognized-pt-02 | The understanding result flips between repetitions; the sentence sits at a boundary. It also failed in the interim run. |
| adv-baddata-es-03, adv-baddata-pt-02 | The transaction is now found and its amount is unknown. The handoff comes after the customer confirms the found transaction, but the case ends at the confirmation question. |
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
| Current | hr-repeat-en-01, multi-espt-02 |

## 4. Fairness

The current run also reports outcomes by customer segment. The interim run could not: the customer table the segments come from was not available to it. Slice sizes are small. No slice differs from its dimension by more than sampling noise, and a slice with few cases is rarely flagged, so the absence of a flag is not evidence of equal treatment.

## 5. Quality scoring

Grounding, language quality and clarification all stay human-only. A dimension is demoted when the judge agrees with either rater on fewer than 80 % of cases, so no judge mean is reported. For clarification the judge agrees with Rater 1 on 1 of 6 cases and with Rater 2 on 6 of 6; the two raters agree with each other on 1 of 6. The two raters' own means over the validation sample are in the generated report.

## 6. Limitations

- The validation sample is not the judged run. Rater means describe the 50 reply-level sheets (6 for clarification), not the 135 cases the judge scored.
- The clarification validation covers a different set of cases from the ones the judge scored. Raters scored clarification on the 6 ambiguous cases in the sample; the judge scored 27 cases (those 6 and 21 others, which both raters left unscored). Agreement is computed over the 6 only. The per-case rater and judge scores are local working files, not committed.
- The B1 figures are one run each. The P figures are three runs; their ranges are a measure of nondeterminism, not a confidence interval.
- Failure counts are for the last repetition of a run, not the union across repetitions.
- Conversation changes merged after the current commit are not measured here. Every figure describes commit 6cea3b4 and nothing later.
- The golden set is team-generated. Section 3 records three cases whose wording does not measure the behavior they describe.
