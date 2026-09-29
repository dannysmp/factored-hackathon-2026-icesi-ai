"""Unit tests for the Metabase dashboard-theming script's pure logic (no network calls): the
parity checklist's mismatch detection, and the existing-dashcard matching that makes re-running
the script converge instead of duplicating panels. `infra/scripts/lib/` sits outside the normal
package layout (it runs stdlib-only on a bare host), so the module is loaded by file path."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

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
