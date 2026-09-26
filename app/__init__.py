"""
Dispute Intake Application Package
==================================

Overview
--------
Backend of the AI-first transaction-dispute intake system: configuration, the FastAPI
composition root and (in later epics) the policy engine, tool layer and dialogue controller.

Scope
-----
In: everything that runs inside the service process.
Out: offline data pipelines (``pipelines/``), model training (``models/``) and
evaluation tooling (``evals/``), which live beside this package.

Design Principles
-----------------
Importing this package has no side effects: no configuration is read, no connection is opened.
"""
