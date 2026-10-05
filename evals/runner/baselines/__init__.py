"""
Baseline Systems
================

Overview
--------
The two baselines the proposed system is compared with. B0 is the proposed system's own
application with its language understanding replaced by the deterministic keyword classifier
(``b0``). B1 is a naive tool-calling agent with its own model client, tool dispatcher and
conversation loop (``b1``, ``b1_tools``, ``naive_agent_client``).

Scope
-----
In: building and running the baselines, and the pieces that exist only for them.
Out: the proposed system (``evals.runner.proposed_system``) and the machinery shared by every
variant (``evals.runner.runner``).

Design Principles
-----------------
Importing this package has no side effects.
"""
