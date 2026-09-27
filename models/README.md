# models/

Risk-model training code, experiment log and model cards.

## `split.toml`

The dates that separate the training, validation and test periods of the transaction risk model.
The feature mart (`make features`) labels every row with its period from this file, so the model is
always evaluated on days that follow the ones it learned from. Changing a date is a new experiment.

## What the features can and cannot say

The velocity, gap and distance features of a transaction use only that customer's strictly earlier
transactions. The customer's country is the exception: the cleaned layer keeps the latest version of
each customer, so it is a snapshot, and `reports/risk-features.md` measures how many transactions
belong to a customer whose record is newer than the transaction. Training compares results with and
without `customer_country` and `country_mismatch`.

## `probe.py`

The pre-registered signal probe (`plan/product/preregistration-risk-probe.md`): a logistic
regression baseline and a small, capped gradient-boosted classifier, fitted on the training period
of the risk feature mart on identical features and scored on the validation period with the area
under the precision-recall curve against the period's base rate. `python -m models.probe` appends
its result to `experiments.jsonl`. It only decides whether the boosted-model and calibration slices
proceed in their planned order; the precision floor, the threshold, the bootstrap interval, the
routing decision and the model card are theirs.

## `boosted.py`

The full boosted-model comparison and the with/without ablation of `customer_country` and
`country_mismatch`: an uncapped `HistGradientBoostingClassifier` against the logistic baseline, on
identical features fitted on the training period only. The comparison itself is a
customer-resampled paired bootstrap on the test period, read exactly once: the customer identifier
is obtained by joining the mart's transaction identifier to the cleaned transactions table for
grouping only, and is never a feature and never logged. `python -m models.boosted` appends its
result to `experiments.jsonl`, including which model the bootstrap interval selects and why. The
precision floor, the threshold and the model card stay with the calibration slice.

## `calibration.py`

The calibration slice of the pre-registered probe: fits the model `boosted.py`'s bootstrap last
selected (read from `experiments.jsonl`, never re-decided here), reports its calibration (Brier
score, expected calibration error, a binned curve) on validation and on test, and searches
validation for the lowest threshold whose precision is at least the pre-registered floor with a
routed share of at most 5 %. With a threshold, the test period is scored once and a
customer-resampled bootstrap gives the 95 % interval of its precision; routing is switched on only
if that precision clears the floor and the interval's lower bound is above the test prevalence.
Without one, routing stays off and the card records why. `python -m models.calibration` appends
its result to `experiments.jsonl` and writes it as the current model card, `model_card.json`.

## `model_card.json`

The current, machine-readable model card: the selected model, the threshold decision (or its
absence), the test-period result and its interval when a threshold was chosen, the calibration
diagnostics, the routing decision and its rationale, and the data provenance, intended use,
leakage review and limitations `AC-E6-09` asks for. Unlike `experiments.jsonl`, it is overwritten
on every run: it reports the current state, not a history. A test in `tests/` asserts it stays
consistent with the policy file's own `risk_score_threshold` and `risk_routing_enabled`.

## `experiments.jsonl`

The experiment log: one JSON line per run, appended, never edited. Each line carries the code and
mart versions, the split, the seed, the row and positive counts per period and each model's
validation metric, so every run that has been done stays on record.
