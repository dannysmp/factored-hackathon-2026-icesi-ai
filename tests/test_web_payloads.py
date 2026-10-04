"""The web tests' backend payloads are exactly what the service's response models produce."""

from __future__ import annotations

from tests.fixtures.web_payloads import PAYLOADS_PATH, render_payloads


def test_the_committed_web_payloads_match_the_response_models() -> None:
    assert PAYLOADS_PATH.read_text(encoding="utf-8") == render_payloads(), (
        "regenerate with: uv run python -m tests.fixtures.web_payloads"
    )
