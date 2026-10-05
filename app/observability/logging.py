"""
Structured Logging
===================

Overview
--------
One JSON log formatter installed once, at the composition root and at the top of every CLI
entrypoint, replacing the ad-hoc ``logging.basicConfig`` calls that used to configure each
process's own logging (and, for the FastAPI app itself, configured nothing at all). Every line
carries a stable event name, the request's trace id and, when one exists, the authenticated
session id — without any call site passing them explicitly — and has any card-shaped digit run
redacted before it is ever written.

Scope
-----
In: the JSON formatter, the context filter, wiring ``redact_pan`` into the log path.
Out: what a caller logs and at what level (every module's own call sites), the PAN detector itself
(``app.llm.masking``), the request/session contextvars themselves (``app.security.middleware`` —
this module only reads them).

Design Principles
-----------------
- One place installs logging for the whole process, CLI entrypoints and the FastAPI app alike, so
  "structured JSON, redacted, with context" is a property of the process, not something every
  entrypoint has to remember to configure right.
- The stable ``event`` name is the first whitespace-delimited token of the message every call site
  already writes (for example ``"session_issued session_id=%s"``); this formalizes the codebase's
  existing convention rather than requiring every call site to pass ``extra={"event": ...}``.
- ``trace_id`` is on every line; ``session_id`` is on every line inside an authenticated request
  and ``None`` outside one — both read from contextvars a caller never has to pass.
- Redaction runs on the fully rendered message, the same point ``app.llm.masking.redact_pan``
  already runs at for the LLM egress boundary, so a card-shaped digit run in raw customer free
  text can never reach a log line either.
- Level discipline: ``DEBUG`` for detail useful only while developing; ``INFO`` for a normal
  lifecycle event (a session issued, a migration applied); ``WARNING`` for a handled refusal or
  degradation (a rejected sign-in, a rate limit); ``ERROR`` for a failure the caller could not
  handle, with ``exc_info`` attached; ``CRITICAL`` for a failure the process cannot continue past
  (``app.main``'s ``config_invalid`` event on a start-up configuration failure is the one call site
  today).

Runtime Contract
-----------------
``configure_logging(level, *, service_version, environment) -> None`` installs the formatter and
filter on the root logger; safe to call more than once (a fresh call replaces the prior handler
rather than stacking a second one).
``configure_logging_from_settings(settings) -> None`` does the same from a ``Settings`` object, or
a safe fallback when ``settings`` is ``None`` (a CLI entrypoint that resolved its own DSN and
could not load settings for an unrelated reason).

Limitations
-----------
Redaction only catches card-shaped digit runs — the one risk class that can carry raw customer
free text into a log line. Email, phone and document-number values never reach ``app`` as raw
values in the first place (masked upstream at the store, or never read at all), so this filter
does not separately scan for them. A stack trace (``exc_info``) is not itself scanned for a
card-shaped run; no call site today logs one alongside raw customer free text, so this is a
known limitation, not an oversight.
"""

from __future__ import annotations

# Standard libraries
import json  # Line-per-record JSON output
import logging  # The formatter, filter and handler this module installs
from datetime import UTC, datetime  # Timestamp formatting
from typing import Any  # Untyped extra attributes logging.Filter attaches to a LogRecord

# Local modules
from app.config import Settings  # Fields configure_logging_from_settings reads
from app.llm.masking import redact_pan  # Card-shaped digit-run redaction, reused from LLM egress
from app.security.middleware import current_request_id, current_session_id  # Request context

SERVICE_NAME = "dispute-intake"

# What a CLI entrypoint logs with when it resolves its own DSN (--dsn) and settings could not be
# loaded for an unrelated reason: an explicit --dsn must not be blocked by, for example, an
# invalid signing key nothing on that code path even reads.
_FALLBACK_LOG_LEVEL = "INFO"
_FALLBACK_SERVICE_VERSION = "unknown"
_FALLBACK_ENVIRONMENT = "unknown"


class _ContextFilter(logging.Filter):
    """Attaches the request's trace id and, when authenticated, its session id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record_any: Any = record
        record_any.trace_id = current_request_id()
        record_any.session_id = current_session_id()
        return True


class _JsonFormatter(logging.Formatter):
    """Renders one log record as one JSON line, redacted, with the full base schema."""

    def __init__(self, *, service_version: str, environment: str) -> None:
        super().__init__()
        self._service_version = service_version
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        message = redact_pan(record.getMessage()).masked
        event = message.split(" ", 1)[0] if message else ""
        record_any: Any = record
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "event": event,
            "message": message,
            "service": SERVICE_NAME,
            "service_version": self._service_version,
            "environment": self._environment,
            "component": record.name,
            "trace_id": getattr(record_any, "trace_id", "-"),
            "session_id": getattr(record_any, "session_id", None),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str, *, service_version: str, environment: str) -> None:
    """Install the JSON formatter and context filter on the root logger.

    Replaces any handler this function previously installed rather than stacking a second one, so
    it is safe to call again (for example once per test).
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter())
    handler.setFormatter(_JsonFormatter(service_version=service_version, environment=environment))
    root.addHandler(handler)


def configure_logging_from_settings(settings: Settings | None) -> None:
    """Install logging from ``settings``, or a safe fallback when settings could not be loaded.

    For a CLI entrypoint that can resolve its DSN from ``--dsn`` instead of ``DATABASE_URL``: an
    unrelated, invalid setting (a malformed signing key, say) must not block that entrypoint from
    running with an explicit DSN, so ``settings=None`` here means "loading settings failed and the
    caller decided to proceed anyway," not "no settings exist."
    """
    if settings is not None:
        configure_logging(
            settings.log_level,
            service_version=settings.service_version,
            environment=settings.app_env.value,
        )
    else:
        configure_logging(
            _FALLBACK_LOG_LEVEL,
            service_version=_FALLBACK_SERVICE_VERSION,
            environment=_FALLBACK_ENVIRONMENT,
        )
