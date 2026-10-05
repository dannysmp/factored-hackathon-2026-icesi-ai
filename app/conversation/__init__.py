"""
Conversation Package
====================

The dialogue layer: the controller that turns one customer message into a reply (``controller``),
the structured state it keeps between turns (``state``) and where that lives (``store``). A
message is understood through the ``Understanding`` port (``understanding``, with the
model-backed implementation in ``llm_understanding`` and the deterministic transaction-date
resolution in ``date_expressions``), checked for a missing element by the deterministic guard
(``guard``), and answered from a policy corpus (``policy_answer``). The reply is built from
grounded facts (``facts``, ``handoff``) and rendered from a template (``renderer``) or, where
eligible, by the model (``reply``, ``model_renderer``) and then checked against its envelope
(``slot_values``, ``verifier``). ``language`` holds the pure language-switching rule.
"""
