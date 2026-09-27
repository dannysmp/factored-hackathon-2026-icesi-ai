"""
Evaluation Package
==================

Overview
--------
The held-out golden set (scripted scenarios with an expected outcome), the metrics engine that
scores a harness run against it, and (as they are added) the runner, baselines, judge and report
generator.

Scope
-----
In: everything that defines or scores a fixed evaluation workload.
Out: the online service (``app/``) and the offline pipelines (``pipelines/``); this package
consumes their outputs (the seed, the contracts) but runs nothing in the request path.

Design Principles
-----------------
Importing this package has no side effects. The golden set is data, not code with a business
rule buried in it: every case is a plain record, and scoring is pure functions over records.
"""
