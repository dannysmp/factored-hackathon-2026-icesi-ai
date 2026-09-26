# contracts/

Versioned data contracts. Structure (columns, types, nullability, keys) comes from the table
registry in `pipelines/sources.py`; each contract module adds what the registry cannot express:

- allowed values of coded columns, taken from the values observed in the data;
- numeric ranges;
- canonical spellings, such as `Mexico` → `México`;
- what to do with a reference that points at no existing row: quarantine the row, or only flag
  it (used for the source's branch references, which never resolve).

A released contract never changes. A change is a new module (`v2.py`) and a new version string,
which every cleaned artefact records in its manifest.
