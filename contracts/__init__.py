"""
Data Contracts
==============

Overview
--------
Versioned contracts for the source tables. A contract states what a row must satisfy to enter
the cleaned layer beyond the structural facts already declared in ``pipelines.sources``
(columns, types, nullability, keys): the allowed values of coded columns, numeric ranges,
canonical spellings and what to do with references that point at no existing row.

Scope
-----
In: declarative rules, one module per contract version.
Out: applying the rules (``pipelines.silver``) and measuring the data (``pipelines.profile``).

Design Principles
-----------------
- A contract version never changes after release; a change is a new module (``v2``) and a
  new ``CONTRACT_VERSION``, so every cleaned artefact states the rules it was produced under.
- Rules are data, not code: the same declaration can be rendered as SQL, documentation or a
  test case.

Runtime Contract
----------------
``contracts.v1.contract_for(table_name) -> TableContract`` and ``contracts.v1.CONTRACT_VERSION``.
"""
