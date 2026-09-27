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

## `experiments.jsonl`

The experiment log: one JSON line per run, appended, never edited. Each line carries the code and
mart versions, the split, the seed, the row and positive counts per period and each model's
validation metric, so every run that has been done stays on record.
