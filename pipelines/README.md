# pipelines/

Offline data tooling. Nothing here runs inside the service's request path.

| Module | Purpose |
|---|---|
| `sources.py` | Registry of the 13 source tables: role, on-disk layout, expected rows, keys, foreign keys and declared columns |
| `inventory.py` | File-level facts: partition layout, byte-order marks, header conformance and a digest of the snapshot |
| `profile.py` | Measures the raw data against the data dictionary; `python -m pipelines.profile` or `make profile` |
| `profile_models.py` | Immutable result objects of a profiling run |
| `profile_report.py` | Renders the profile as `reports/data-profile.md`, with a verdict on each assumption |
| `raw.py` | Strict loading of a raw table, shared by the profiler and the cleaning stage |
| `silver.py` | The cleaning stage: typing, contract checks, de-duplication, reference handling, quarantine, manifests; `python -m pipelines.silver` or `make pipeline` |
| `outcomes.py`, `quality.py` | Result objects of a cleaning run and the `reports/data-quality.md` renderer |
| `gold.py` | The dispute demand marts: aggregates over the cleaned tables with a manifest of inputs and outputs |
| `risk_features.py` | The risk feature mart: features known when a transaction happened, the fraud label and its training, validation or test period (`models/split.toml`); `python -m pipelines.risk_features` or `make features` |
| `risk_signal.py` | Fraud prevalence per band of the amount, hour and velocity features in the training and validation periods (the test period is not read); `python -m pipelines.risk_signal` |
| `analysis.py`, `analysis_assumptions.toml` | Renders `reports/workflow-analysis.md` from the marts; every figure that is not measured comes from the assumptions file; `python -m pipelines.analysis` or `make analyze` |
| `analytics_load.py` | Loads `gold.py`'s dispute-demand marts into the Postgres `analytics` schema the BI dashboard reads (ADR-11), with a row-count and content-checksum parity check against the gold marts; `python -m pipelines.analytics_load` or `make load-analytics` |
| `eval_bank.py` | Frozen customer/transaction scenarios the golden set references for what `ops_seed` cannot hold structurally (an orphan transaction, a missing merchant name, an injected merchant name, an unknown amount); `python -m pipelines.eval_bank` or `make eval-bank` |

Planned stages: the remaining analytical marts (the labeled intent-evaluation seed).
