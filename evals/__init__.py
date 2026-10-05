"""
Evaluation Package
==================

Overview
--------
The evaluation workload and everything that measures a system against it: the held-out golden set
(scripted scenarios with an expected outcome), the runner and the three system variants (P, B0,
B1), the deterministic scoring and metrics engine, the LLM judge and its validation against human
raters, fairness slicing, repeated-run variability, and the report generator. ``evals.cli`` is the
entry point.

Scope
-----
In: everything that defines, runs or scores a fixed evaluation workload.
Out: the online service (``app/``) and the offline pipelines (``pipelines/``); this package
consumes their outputs (the seed, the contracts) but runs nothing in the request path.

Design Principles
-----------------
Importing this package has no side effects. The golden set is data, not code with a business rule
buried in it: every case is a plain record, and scoring is pure functions over records.
"""
