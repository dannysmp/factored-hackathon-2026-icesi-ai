"""
Data Pipelines Package
======================

Overview
--------
Offline data tooling: the source table registry, raw-data inventory and profiling, and (as
they are added) the contracts, cleaning stages and analytical marts built on top of them.

Scope
-----
In: everything that reads the raw data and produces reports or derived datasets.
Out: the online service (``app/``); nothing here runs inside the request path.

Design Principles
-----------------
Importing this package has no side effects; every entry point takes the data directory as an
explicit argument.
"""
