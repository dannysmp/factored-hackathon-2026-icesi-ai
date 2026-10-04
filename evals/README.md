# Evaluation harness

Golden set, adversarial cases, evaluation harness and judge rubric.

## Golden-set adjudication

Before the golden set is frozen, an independent rater read all 135 cases and judged, for each, whether the declared expected outcome class and expected reason code are right for the scenario. The rater's sheet is a working record kept outside the repository; this section records its result. Of the 135 cases, 125 are team-generated synthetic and 10 are injected (the poisoned-input and bad-data adversarial cases); the rater checked labels only, not that provenance.

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

No case declares an `INELIGIBLE` outcome. Every reason code other than `eligible` and the `escalate_*` codes (`product_out_of_scope`, `transaction_type_not_disputable`, `transaction_declined`, `transaction_pending`, `transaction_reversed`, `transaction_date_in_future`, `filing_window_expired`, `duplicate_open_case`) is absent from the 135 cases, so the set does not exercise a single denial of an ineligible filing. The policy engine's own tests cover those codes; the evaluation does not. The set is reported as it stands and the gap is disclosed in the limitations report.
