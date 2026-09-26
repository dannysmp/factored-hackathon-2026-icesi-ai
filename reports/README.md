# reports/

Generated reports. `make` targets rebuild them from the data and the code.

| File | Produced by | Tracked |
|---|---|---|
| `data-profile.md` | `make profile` | Yes: the figures other documents cite |
| `data-profile.json` | `make profile` | No: machine-readable copy of the same profile |
| `data-quality.md` | `make pipeline` | Yes: counts of kept, superseded and quarantined rows per table |
| `risk-features.md` | `make features` | Yes: the periods, the features and their coverage, the columns left out on purpose and why |
| `workflow-analysis.md` | `make analyze` | Yes: dispute demand, resolution, sentiment, handling cost and target outcomes; the source of every figure quoted elsewhere |
