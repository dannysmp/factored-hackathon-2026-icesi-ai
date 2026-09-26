# models/

Risk-model training code, experiment log and model cards.

## `split.toml`

The dates that separate the training, validation and test periods of the transaction risk model.
The feature mart (`make features`) labels every row with its period from this file, so the model is
always evaluated on days that follow the ones it learned from. Changing a date is a new experiment.
