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

Planned stages: analytical marts.
