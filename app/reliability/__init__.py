"""
Reliability Package
====================

Bounded retries and circuit breakers around the two external dependencies the dialogue controller
calls (the LLM provider, the serving store), so a transient failure degrades to a safe handoff
only after being genuinely unrecoverable, never on the first attempt.
"""
