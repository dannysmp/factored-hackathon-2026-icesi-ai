"""Unit tests for the Metabase dashboard-theming script: the parity checklist's mismatch
detection, the existing-dashcard matching that makes re-running the script converge instead of
duplicating panels, and — hermetically, via a faked `urlopen` — `_request`'s failure paths (an
HTTP error, an unreachable host, the configured timeout actually reaching `urlopen`). `infra/
scripts/lib/` sits outside the normal package layout (it runs stdlib-only on a bare host), so the
module is loaded by file path."""

import importlib.util
import io
import runpy
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "infra"
    / "scripts"
    / "lib"
    / "theme_metabase_dashboard.py"
)
_spec = importlib.util.spec_from_file_location("theme_metabase_dashboard", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
theme = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = theme
_spec.loader.exec_module(theme)


def _cards_matching_declared_colors() -> dict[str, dict[str, Any]]:
    """A fresh-from-the-API card per panel, whose confirmed colors match what each panel declares
    — the baseline every mismatch test below perturbs one field of."""
    cards = {}
    for panel in theme.PANELS:
        settings = {
            column: {"color": hex_value} for column, (_token, hex_value) in panel["series"].items()
        }
        cards[panel["name"]] = {"visualization_settings": {"series_settings": settings}}
    return cards


def test_every_panel_name_is_unique() -> None:
    names = [panel["name"] for panel in theme.PANELS]
    assert len(names) == len(set(names))


def test_find_by_name_returns_the_matching_row() -> None:
    rows = [{"name": "a", "id": 1}, {"name": "b", "id": 2}]

    assert theme._find_by_name(rows, "b") == {"name": "b", "id": 2}


def test_find_by_name_returns_none_for_an_unknown_name() -> None:
    assert theme._find_by_name([{"name": "a"}], "z") is None


def test_series_settings_maps_each_column_to_its_hex_value() -> None:
    series = {
        "cases": theme.SeriesColor("--color-accent", "#1c5fd6"),
        "sla_breached_cases": theme.SeriesColor("--color-warning", "#8a5a00"),
    }

    assert theme._series_settings(series) == {
        "cases": {"color": "#1c5fd6"},
        "sla_breached_cases": {"color": "#8a5a00"},
    }


def test_checklist_reports_no_mismatch_when_every_confirmed_color_matches() -> None:
    checklist = theme._checklist(_cards_matching_declared_colors())

    assert "MISMATCH" not in checklist
    assert "MISSING" not in checklist


def test_checklist_flags_a_color_that_does_not_match_what_the_panel_declares() -> None:
    cards = _cards_matching_declared_colors()
    cards["Dispute volume by month"]["visualization_settings"]["series_settings"]["cases"][
        "color"
    ] = "#000000"

    checklist = theme._checklist(cards)

    assert "MISMATCH (#000000)" in checklist


def test_checklist_flags_a_series_the_card_never_confirmed() -> None:
    cards = _cards_matching_declared_colors()
    cards["Dispute volume by month"]["visualization_settings"]["series_settings"] = {}

    checklist = theme._checklist(cards)

    assert "MISSING" in checklist


def test_text_dashcard_reuses_an_existing_dashcard_at_the_same_slot() -> None:
    existing = [{"id": 7, "card_id": None, "row": 0, "col": 0}]

    dashcard = theme._text_dashcard(existing, row=0, text="## Question", placeholder_id=-1)

    assert dashcard["id"] == 7


def test_text_dashcard_uses_the_placeholder_when_no_dashcard_occupies_that_slot() -> None:
    dashcard = theme._text_dashcard([], row=0, text="## Question", placeholder_id=-3)

    assert dashcard["id"] == -3


def test_chart_dashcard_reuses_an_existing_dashcard_referencing_the_same_card() -> None:
    existing = [{"id": 9, "card_id": 40, "row": 2, "col": 0}]

    dashcard = theme._chart_dashcard(existing, row=2, card_id=40, placeholder_id=-1)

    assert dashcard["id"] == 9


def test_chart_dashcard_uses_the_placeholder_for_a_card_not_yet_on_the_dashboard() -> None:
    dashcard = theme._chart_dashcard([], row=2, card_id=99, placeholder_id=-4)

    assert dashcard["id"] == -4


def test_request_refuses_a_non_http_url() -> None:
    with pytest.raises(ValueError, match="non-HTTP"):
        theme._request("file:///etc/passwd", "", session_id=None)


def _fake_success(body: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def test_request_passes_the_configured_timeout_to_urlopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float | None = None) -> MagicMock:
        captured["timeout"] = timeout
        return _fake_success(b'{"ok": true}')

    monkeypatch.setattr(theme.urllib.request, "urlopen", fake_urlopen)

    result = theme._request("http://example.test", "/api/x", session_id=None)

    assert captured["timeout"] == theme._TIMEOUT_SECONDS
    assert result == {"ok": True}


def test_request_raises_a_clear_error_on_an_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float | None = None) -> MagicMock:
        raise urllib.error.HTTPError(
            "http://example.test/api/x",
            401,
            "Unauthorized",
            Message(),
            io.BytesIO(b"bad credentials"),
        )

    monkeypatch.setattr(theme.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="401"):
        theme._request("http://example.test", "/api/x", session_id=None)


def test_request_raises_a_clear_error_when_the_host_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Any, timeout: float | None = None) -> MagicMock:
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(theme.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="could not reach"):
        theme._request("http://example.test", "/api/x", session_id=None)


def test_sign_in_propagates_a_clear_error_when_credentials_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: Any, timeout: float | None = None) -> MagicMock:
        raise urllib.error.HTTPError(
            "http://example.test/api/session", 401, "Unauthorized", Message(), io.BytesIO(b"{}")
        )

    monkeypatch.setattr(theme.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="401"):
        theme._sign_in("http://example.test", "admin@example.com", "wrong-password")


def test_find_analytics_database_id_raises_when_no_analytics_database_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        theme, "_request", lambda *args, **kwargs: {"data": [{"name": "sample", "id": 1}]}
    )

    with pytest.raises(RuntimeError, match="analytics"):
        theme._find_analytics_database_id("http://example.test", "session-id")


def test_find_analytics_database_id_returns_the_matching_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        theme,
        "_request",
        lambda *args, **kwargs: {
            "data": [{"name": "sample", "id": 1}, {"name": "analytics", "id": 7}]
        },
    )

    assert theme._find_analytics_database_id("http://example.test", "session-id") == 7


def _fake_request_recording(
    responses: dict[tuple[str, str], Any], calls: list[tuple[str, str]]
) -> Any:
    def fake_request(
        base_url: str, path: str, *, session_id: str | None, method: str = "GET", body: Any = None
    ) -> Any:
        calls.append((method, path))
        try:
            return responses[(method, path)]
        except KeyError:
            raise AssertionError(f"unexpected call: {method} {path}") from None

    return fake_request


def test_upsert_card_creates_when_no_matching_card_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        theme,
        "_request",
        _fake_request_recording(
            {
                ("GET", "/api/card"): {"data": []},
                ("POST", "/api/card"): {"id": 99},
            },
            calls,
        ),
    )

    result = theme._upsert_card("http://example.test", "session-id", 1, theme.PANELS[0])

    assert result == {"id": 99}
    assert ("POST", "/api/card") in calls
    assert not any(method == "PUT" for method, _ in calls)


def test_upsert_card_updates_when_a_matching_card_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    panel = theme.PANELS[0]
    monkeypatch.setattr(
        theme,
        "_request",
        _fake_request_recording(
            {
                ("GET", "/api/card"): {"data": [{"name": panel["name"], "id": 42}]},
                ("PUT", "/api/card/42"): {"id": 42},
            },
            calls,
        ),
    )

    result = theme._upsert_card("http://example.test", "session-id", 1, panel)

    assert result == {"id": 42}
    assert ("PUT", "/api/card/42") in calls
    assert not any(method == "POST" for method, _ in calls)


def test_upsert_dashboard_returns_the_existing_id_when_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        theme,
        "_request",
        _fake_request_recording(
            {("GET", "/api/dashboard"): {"data": [{"name": theme.DASHBOARD_NAME, "id": 5}]}},
            calls,
        ),
    )

    assert theme._upsert_dashboard("http://example.test", "session-id") == 5
    assert not any(method == "POST" for method, _ in calls)


def test_upsert_dashboard_creates_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        theme,
        "_request",
        _fake_request_recording(
            {
                ("GET", "/api/dashboard"): {"data": []},
                ("POST", "/api/dashboard"): {"id": 8},
            },
            calls,
        ),
    )

    assert theme._upsert_dashboard("http://example.test", "session-id") == 8
    assert ("POST", "/api/dashboard") in calls


def test_sign_in_returns_the_session_id_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: float | None = None) -> MagicMock:
        return _fake_success(b'{"id": "abc-session"}')

    monkeypatch.setattr(theme.urllib.request, "urlopen", fake_urlopen)

    session_id = theme._sign_in("http://example.test", "admin@example.com", "pw")

    assert session_id == "abc-session"


def test_request_sends_the_session_header_when_given_one(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float | None = None) -> MagicMock:
        captured["session_header"] = request.get_header("X-metabase-session")
        return _fake_success(b"{}")

    monkeypatch.setattr(theme.urllib.request, "urlopen", fake_urlopen)

    theme._request("http://example.test", "/api/x", session_id="tok-123")

    assert captured["session_header"] == "tok-123"


def test_main_runs_the_full_sequence_and_writes_a_matching_checklist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Hermetic end-to-end proof, alongside (not instead of) the real run against a throwaway
    Metabase container: every helper `main` calls is faked here, in sequence, so a break in how
    they're wired together — not just in one helper alone — would fail this test too."""
    monkeypatch.setenv("MB_BASE_URL", "http://example.test")
    monkeypatch.setenv("MB_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("MB_ADMIN_PASSWORD", "pw")

    card_ids = {panel["name"]: index + 1 for index, panel in enumerate(theme.PANELS)}

    def refreshed_card(card_id: int) -> dict[str, Any]:
        name = next(n for n, i in card_ids.items() if i == card_id)
        panel = next(p for p in theme.PANELS if p["name"] == name)
        settings = {col: {"color": color.hex} for col, color in panel["series"].items()}
        return {"visualization_settings": {"series_settings": settings}}

    def created_card(body: dict[str, Any] | None) -> dict[str, Any]:
        assert body is not None
        return {"id": card_ids[body["name"]], **body}

    fixed_responses: dict[tuple[str, str], Any] = {
        ("POST", "/api/session"): {"id": "session-token"},
        ("GET", "/api/database"): {"data": [{"name": "analytics", "id": 1}]},
        ("GET", "/api/collection?archived=true"): [],
        ("GET", "/api/collection"): [],
        ("GET", "/api/card"): {"data": []},
        ("GET", "/api/dashboard"): {"data": []},
        ("POST", "/api/dashboard"): {"id": 100},
        ("GET", "/api/dashboard/100"): {"dashcards": []},
        ("PUT", "/api/dashboard/100"): {},
    }

    def fake_request(
        base_url: str, path: str, *, session_id: str | None, method: str = "GET", body: Any = None
    ) -> Any:
        if (method, path) == ("POST", "/api/card"):
            return created_card(body)
        if path.startswith("/api/card/") and method == "GET":
            return refreshed_card(int(path.rsplit("/", 1)[-1]))
        if path.startswith("/api/card/") and path.endswith("/query") and method == "POST":
            return {"data": {"rows": [[1, 2]]}}
        if (method, path) in fixed_responses:
            return fixed_responses[(method, path)]
        raise AssertionError(f"unexpected call: {method} {path}")

    monkeypatch.setattr(theme, "_request", fake_request)

    theme.main()

    output = capsys.readouterr().out
    assert "# Dashboard theme checklist" in output
    assert "MISMATCH" not in output
    assert "MISSING" not in output


def _run_main_with_card_rows(monkeypatch: pytest.MonkeyPatch, rows: list[list[Any]]) -> None:
    """Runs `main` against a faked Metabase whose every panel question returns `rows`."""
    monkeypatch.setenv("MB_BASE_URL", "http://example.test")
    monkeypatch.setenv("MB_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("MB_ADMIN_PASSWORD", "pw")
    card_ids = {panel["name"]: index + 1 for index, panel in enumerate(theme.PANELS)}

    fixed: dict[tuple[str, str], Any] = {
        ("POST", "/api/session"): {"id": "tok"},
        ("GET", "/api/database"): {"data": [{"name": "analytics", "id": 1}]},
        ("GET", "/api/card"): {"data": []},
        ("GET", "/api/dashboard"): {"data": []},
        ("GET", "/api/collection"): [],
        ("GET", "/api/collection?archived=true"): [],
        ("POST", "/api/dashboard"): {"id": 100},
        ("GET", "/api/dashboard/100"): {"dashcards": []},
        ("PUT", "/api/dashboard/100"): {},
    }

    def fake_request(
        base_url: str, path: str, *, session_id: str | None, method: str = "GET", body: Any = None
    ) -> Any:
        if (method, path) in fixed:
            return fixed[(method, path)]
        if (method, path) == ("POST", "/api/card"):
            assert body is not None
            return {"id": card_ids[body["name"]]}
        if path.endswith("/query"):
            return {"data": {"rows": rows}}
        if path.startswith("/api/card/") and method == "GET":
            panel = theme.PANELS[int(path.rsplit("/", 1)[-1]) - 1]
            settings = {col: {"color": c.hex} for col, c in panel["series"].items()}
            return {"visualization_settings": {"series_settings": settings}}
        raise AssertionError(f"unexpected call: {method} {path}")

    monkeypatch.setattr(theme, "_request", fake_request)
    theme.main()


def test_main_fails_naming_every_panel_whose_question_returns_no_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="no rows") as error:
        _run_main_with_card_rows(monkeypatch, [])

    for panel in theme.PANELS:
        assert panel["name"] in str(error.value)


def test_main_succeeds_when_every_panel_question_returns_rows(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _run_main_with_card_rows(monkeypatch, [[1, 2]])

    assert "# Dashboard theme checklist" in capsys.readouterr().out


def test_panel_row_count_raises_when_the_question_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        theme, "_request", lambda *a, **k: {"error": 'relation "analytics.x" does not exist'}
    )

    with pytest.raises(RuntimeError, match="failed to run"):
        theme._panel_row_count("http://example.test", "tok", 7)


def _sample_content_instance(
    databases: list[dict[str, Any]], collections: list[dict[str, Any]], calls: list[tuple[str, str]]
) -> Any:
    def fake_request(
        base_url: str, path: str, *, session_id: str | None, method: str = "GET", body: Any = None
    ) -> Any:
        calls.append((method, path))
        if (method, path) == ("GET", "/api/database"):
            return {"data": databases}
        if (method, path) == ("GET", "/api/collection?archived=true"):
            return [c for c in collections if c.get("archived")]
        if (method, path) == ("GET", "/api/collection"):
            return [c for c in collections if not c.get("archived")]
        return None

    return fake_request


def test_remove_sample_content_deletes_the_sample_database_and_both_collection_levels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    databases = [
        {"id": 1, "name": "Sample Database", "is_sample": True},
        {"id": 2, "name": "analytics", "is_sample": False},
    ]
    collections = [
        {"id": 2, "name": "Examples", "is_sample": True, "location": "/", "archived": False},
        {"id": 3, "name": "E-commerce", "is_sample": True, "location": "/2/", "archived": True},
        {"id": 9, "name": "Ours", "is_sample": False, "location": "/", "archived": False},
    ]
    monkeypatch.setattr(theme, "_request", _sample_content_instance(databases, collections, calls))

    removed = theme._remove_sample_content("http://example.test", "tok")

    assert removed == ["database Sample Database", "collection E-commerce", "collection Examples"]
    assert ("DELETE", "/api/database/1") in calls
    assert ("DELETE", "/api/database/2") not in calls
    assert ("DELETE", "/api/collection/3") in calls
    assert ("DELETE", "/api/collection/2") in calls
    assert ("PUT", "/api/collection/2") in calls
    assert ("DELETE", "/api/collection/9") not in calls
    assert calls.index(("DELETE", "/api/collection/3")) < calls.index(
        ("DELETE", "/api/collection/2")
    )


def test_remove_sample_content_does_nothing_on_an_instance_without_sample_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    databases = [{"id": 2, "name": "analytics", "is_sample": False}]
    collections = [{"id": 9, "name": "Ours", "is_sample": False, "location": "/"}]
    monkeypatch.setattr(theme, "_request", _sample_content_instance(databases, collections, calls))

    assert theme._remove_sample_content("http://example.test", "tok") == []
    assert all(method == "GET" for method, _ in calls)


def test_list_marts_mode_prints_each_panel_table_without_contacting_metabase(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["theme_metabase_dashboard.py", "--list-marts"])
    monkeypatch.delenv("MB_BASE_URL", raising=False)

    runpy.run_path(str(_MODULE_PATH), run_name="__main__")

    assert capsys.readouterr().out.split() == [panel["mart"] for panel in theme.PANELS]
