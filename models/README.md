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
