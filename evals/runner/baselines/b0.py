"""
B0 Baseline: Deterministic Scripted Flow
========================================

Overview
--------
Builds the B0 baseline: a deterministic scripted flow with keyword and menu understanding, the same
policy engine and tools, and no LLM. B0 is not a second implementation of the dialogue controller.
It is the proposed system's own application with its understanding port forced to the deterministic
keyword classifier instead of the LLM, so a comparison with P measures exactly one change. That
swap is made by ``app.main._understanding`` when ``llm_provider`` is ``LlmProvider.STUB``; this
module names that construction as B0's definition so callers need not remember the provider
setting.

Scope
-----
In: ``build_b0_app``, forcing ``llm_provider`` to the value that selects the deterministic
classifier.
Out: everything else about the running application. The tool port, retriever, policy, handoff
outbox and store are untouched, which is what isolates the LLM's contribution. Running a case
against the result is ``evals.runner.proposed_system.run_case``, which is generic over any client.

Design Principles
-----------------
- **One implementation, not a fork.** ``app.main.create_app`` and its ``_controller_factory``
  already build the application B0 needs once ``llm_provider`` selects the deterministic
  classifier; this module reuses them rather than re-implementing controller construction.
- **The enum member, never its string value.** ``Settings.model_copy`` skips validation on the
  fields it updates, so the literal string ``"stub"`` would leave ``llm_provider`` a plain ``str``
  where ``app.main._understanding``'s identity check requires the ``LlmProvider`` member.
- **The production restriction is re-asserted, not trusted to validation.** ``Settings`` refuses
  ``llm_provider=stub`` when ``app_env=prod`` (``_stub_llm_rules``), but only when it is built
  through validation, and ``model_copy`` does not validate. Without its own check, a ``Settings``
  already holding ``app_env=prod`` would yield a working B0 application in production. The function
  tests ``app_env`` first, before touching ``llm_provider``.

Runtime Contract
----------------
``build_b0_app(settings) -> FastAPI``. Raises ``ConfigError`` when ``settings.app_env`` is
``prod``.

Limitations
-----------
``app.conversation.understanding.FakeNlu`` never sets a dispute category on an ``NluResult``. A
``NORMAL``-category case whose turns reach the controller's reason-clarification step, which needs
a category, cannot complete through B0, and equally not through a P run on the stub provider. This
is a limit of the shared keyword classifier that B0 inherits.
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
