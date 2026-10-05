"""
Evaluation Runner
=================

Overview
--------
Runs golden-set cases against a system variant and collects one ``CaseResult`` per case. The
proposed system (P) is driven over HTTP by ``proposed_system``; B0, the same application with the
keyword understanding stub, runs through the same path; B1, the naive tool-calling agent, has its
own conversation loop under ``evals.runner.baselines``.

Scope
-----
In: driving a case through a variant (``runner``, ``proposed_system``), resolving a case's seed
reference against the store (``seed_resolution``), choosing the CI smoke subset (``smoke``) and the
baselines.
Out: scoring, metrics and reporting (``evals.scoring``, ``evals.metrics``, ``evals.report``) and
the command line (``evals.cli``).

Design Principles
-----------------
Importing this package has no side effects; a variant opens connections and clients only when it
is asked to run.
"""
