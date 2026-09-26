# pipelines/

Offline data tooling. Nothing here runs inside the service's request path.

| Module | Purpose |
|---|---|
| `sources.py` | Registry of the 13 source tables: role, on-disk layout, expected rows, keys, foreign keys and declared columns |
| `inventory.py` | File-level facts: partition layout, byte-order marks, header conformance and a digest of the snapshot |
| `profile.py` | Measures the raw data against the data dictionary; `python -m pipelines.profile` or `make profile` |
| `profile_models.py` | Immutable result objects of a profiling run |
| `profile_report.py` | Renders the profile as `reports/data-profile.md`, with a verdict on each assumption |

Planned stages: cleaning and quarantine, per-table contracts, analytical marts.
