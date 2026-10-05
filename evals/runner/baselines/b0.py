"""
B0 Baseline: Deterministic Scripted Flow
==========================================

Overview
--------
Builds the B0 baseline, a "deterministic scripted flow: keyword/menu NLU, same policy engine and
tools, no LLM." B0 is not a second implementation of the dialogue controller — it is the proposed
system's own application, with its understanding port forced to the deterministic, keyword-based
classifier instead of the LLM, so the comparison measures exactly one thing that changed. That swap
already exists in ``app.main._understanding``, selected by ``LlmProvider.STUB`` (added for the CI
smoke job's own network-free run); this module names that same construction as B0's own definition,
rather than leaving every caller to remember the provider setting an evaluation run needs.

Scope
-----
In: ``build_b0_app``, forcing ``llm_provider`` to the value that already selects the deterministic
classifier.
Out: everything else about the running application — the tool port, the retriever, the policy, the
handoff outbox and the store are untouched, exactly as the comparison requires to isolate the LLM's
own contribution; running a case against the result (``evals.runner.proposed_system.run_case``,
already generic over any client and reused as-is).

Design Principles
-----------------
- **One implementation, not a fork.** ``app.main.create_app`` and its real ``_controller_factory``
  already build exactly the application B0 needs once ``llm_provider`` selects the deterministic
  classifier; this module reuses them unchanged rather than re-implementing controller
  construction under ``evals/``.
- **The enum member, never its string value.** ``Settings.model_copy`` skips pydantic validation on
  the fields it updates, so passing the literal string ``"stub"`` would leave ``llm_provider`` as a
  plain ``str`` instead of the ``LlmProvider`` member ``app.main._understanding``'s identity check
  requires. This module passes the enum member itself.
- **The prod restriction is re-asserted here, not only trusted from validation.** ``Settings``'s
  own ``_stub_llm_rules`` model validator refuses ``llm_provider=stub`` when ``app_env=prod`` — but
  only when ``Settings`` is actually constructed through validation. ``model_copy`` never
  validates, so a caller that already holds a ``Settings`` object with ``app_env=prod`` (however it
  was built) could otherwise sail straight through this function into a working B0 application in
  a production environment, defeating the very restriction that setting exists to enforce. This
  function checks ``app_env`` itself, before ever touching ``llm_provider``, so the restriction
  holds regardless of what ``model_copy`` does or does not revalidate.

Runtime Contract
-----------------
``build_b0_app(settings) -> FastAPI``. Raises ``ConfigError`` when ``settings.app_env`` is
``prod``.

Limitations
-----------
``app.conversation.understanding.FakeNlu`` never sets a dispute category on any ``NluResult``, in
any of its routed intents (checked directly in its source). A ``NORMAL``-category case whose
scripted turns reach the point where the controller needs a category to proceed — the reason-
clarification step of a filing conversation — cannot complete through B0 the same way it cannot
complete through a stub-driven P run either; this is a genuine limitation of the shared classifier,
not something this module introduces or is responsible for closing.
"""

from __future__ import annotations

# Third-party libraries
from fastapi import FastAPI

# Local modules
from app.config import AppEnvironment, ConfigError, LlmProvider, Settings  # Provider and env
from app.main import create_app  # The one application both P and B0 are built from


def build_b0_app(settings: Settings) -> FastAPI:
    """The B0 baseline application: ``settings``, with ``llm_provider`` forced to the value
    ``app.main._understanding`` already maps to the deterministic, keyword-based classifier.

    Every other setting is passed through unchanged: the same store, the same tool port
    construction, the same policy, the same retriever, the same handoff outbox.

    Raises
    ------
    ConfigError
        ``settings.app_env`` is ``prod``. B0 is an evaluation-only variant; it is refused in
        production for the same reason ``Settings`` itself refuses ``llm_provider=stub`` there,
        re-asserted here because ``model_copy`` does not revalidate that rule.
    """
    if settings.app_env is AppEnvironment.PROD:
        raise ConfigError("the B0 baseline is not allowed when APP_ENV=prod")
    b0_settings = settings.model_copy(update={"llm_provider": LlmProvider.STUB})
    return create_app(b0_settings)
