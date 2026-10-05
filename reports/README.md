# reports/

Generated reports. `make` targets rebuild them from the data and the code.

| File | Produced by | Tracked |
|---|---|---|
| `data-profile.md` | `make profile` | Yes: the figures other documents cite |
| `data-profile.json` | `make profile` | No: machine-readable copy of the same profile |
| `data-quality.md` | `make pipeline` | Yes: counts of kept, superseded and quarantined rows per table |
| `risk-features.md` | `make features` | Yes: the periods, the features and their coverage, the columns left out on purpose and why |
| `risk-signal.md` | `python -m pipelines.risk_signal` | Yes: fraud prevalence per band of the amount, hour and velocity features, training and validation periods only |
| `workflow-analysis.md` | `make analyze` | Yes: dispute demand, resolution, sentiment, handling cost and target outcomes; the source of every figure quoted elsewhere |
| `dashboard-theme-checklist.md` | `infra/scripts/10-configure-metabase-dashboard.sh` | Yes: each operations-dashboard panel's business question, mart and chart color, confirmed by reading it back from Metabase's own API |
| `evaluation.md` | `make evaluate FULL=1`, then `make judge-validation` | Yes: the latest full evaluation of the proposed system and both baselines against the golden set |
| `evaluation-comparison.md` | Written by hand from successive `evaluation.md` reports | Yes: three full runs set side by side, with the cause of every remaining failure |
