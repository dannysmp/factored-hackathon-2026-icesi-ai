"""
Conversation Package
====================

The dialogue layer: structured state between turns (``state``), where it lives (``store``),
language stickiness (``language``), understanding a message (``understanding``, and the
model-backed implementation of it in ``llm_understanding``), the deterministic missing-slot guard
(``guard``) and rendering an envelope into a reply (``renderer``).
"""
