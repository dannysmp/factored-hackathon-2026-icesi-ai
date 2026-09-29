#!/usr/bin/env python3
"""Create or update the operations dashboard's panels, styled from the design-token palette.

Stdlib only (``urllib.request``), matching every other Metabase-provisioning step in this
directory: no package the deployed host doesn't already have. Run against Metabase's own HTTP
API, on whichever host can reach it (loopback on the deployed host; a throwaway local container
during development) — never against the database directly.

Each panel pairs a text card naming its business question with a chart card whose series colors
come from ``web/src/styles/tokens.css``'s light-mode hex values (Metabase renders one fixed
theme, so the light-mode value is the one that applies). Full "native" theming (logo, app name,
instance-wide brand colors) is a paid Metabase feature this deployment doesn't have a license
for — see ``docs/limitations.md`` — so this script scopes to what the open-source edition
actually exposes: per-panel chart colors, content and layout.

Every step checks for an existing row by name before creating one, and updates it in place if
found — the same convention ``08-deploy-metabase.sh`` already uses for the role, database and
datasource connection — so re-running this script (every redeploy) converges to exactly the
panels defined below, never duplicating them.

Prints a markdown checklist to stdout: one row per panel, naming its business question, the mart
it reads, its chart type, and the token/hex color actually confirmed on the card by reading it
back from the API — the same "prove what was actually written" discipline the mart-load parity
check already applies to row counts and checksums.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, NamedTuple, TypedDict

JSON = dict[str, Any]


class SeriesColor(NamedTuple):
    token: str
    hex: str


class Panel(TypedDict):
    name: str
    mart: str
    question: str
    query: str
    display: str
    series: dict[str, SeriesColor]


DASHBOARD_NAME = "Operations"

# Token name and its light-mode hex value, from web/src/styles/tokens.css: Metabase has one fixed
# (light) theme, so the light-mode side of each token's light-dark() pair is what applies here.
TOKEN_ACCENT = SeriesColor("--color-accent", "#1c5fd6")
TOKEN_WARNING = SeriesColor("--color-warning", "#8a5a00")
TOKEN_ERROR = SeriesColor("--color-error", "#b3251f")
TOKEN_SUCCESS = SeriesColor("--color-success", "#1a7f4b")

PANELS: list[Panel] = [
    {
        "name": "Dispute volume by month",
        "mart": "dispute_cases_monthly",
        "question": "How much dispute volume are we handling, and is it growing?",
        "query": "SELECT month, cases FROM analytics.dispute_cases_monthly ORDER BY month",
        "display": "bar",
        "series": {"cases": TOKEN_ACCENT},
    },
    {
        "name": "Median days to resolution and SLA breaches by status",
        "mart": "dispute_resolution",
        "question": ("How long does it take to resolve a dispute, and how often do we breach SLA?"),
        "query": (
            "SELECT status, median_days, sla_breached_cases "
            "FROM analytics.dispute_resolution ORDER BY status"
        ),
        "display": "bar",
        "series": {"median_days": TOKEN_ACCENT, "sla_breached_cases": TOKEN_WARNING},
    },
    {
        "name": "Mean claimed amount by currency",
        "mart": "dispute_claims_by_currency",
        "question": "How much money do claims put at risk, and does it vary by currency?",
        "query": (
            "SELECT currency, claimed_total / NULLIF(cases_with_amount, 0) AS mean_claimed "
            "FROM analytics.dispute_claims_by_currency ORDER BY currency"
        ),
        "display": "bar",
        "series": {"mean_claimed": TOKEN_ERROR},
    },
    {
        "name": "Mean satisfaction score by contact reason",
        "mart": "contact_satisfaction",
        "question": "How satisfied are customers after a dispute-related contact?",
        "query": (
            "SELECT reason_category, score_sum / NULLIF(surveys_with_score, 0) AS mean_score "
            "FROM analytics.contact_satisfaction ORDER BY reason_category"
        ),
        "display": "bar",
        "series": {"mean_score": TOKEN_SUCCESS},
    },
]

# Layout: one text card (business question) above each chart card, stacked vertically, full width.
_ROW_HEIGHT_TEXT = 2
_ROW_HEIGHT_CHART = 6
_WIDTH = 12


def _request(
    base_url: str,
    path: str,
    *,
    session_id: str | None,
    method: str = "GET",
    body: JSON | None = None,
) -> Any:
    url = f"{base_url}{path}"
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-HTTP(S) URL: {url}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)  # noqa: S310 — scheme checked above
    request.add_header("Content-Type", "application/json")
    if session_id is not None:
        request.add_header("X-Metabase-Session", session_id)
    try:
        with urllib.request.urlopen(request) as response:  # noqa: S310 — scheme checked above
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed ({error.code}): {detail}") from error
    return json.loads(raw) if raw else None


def _as_list(response: Any) -> list[JSON]:
    """Metabase answers some list endpoints as a bare array, others as ``{"data": [...]}``."""
    rows: list[JSON] = response if isinstance(response, list) else response.get("data", [])
    return rows


def _sign_in(base_url: str, email: str, password: str) -> str:
    response = _request(
        base_url,
        "/api/session",
        session_id=None,
        method="POST",
        body={"username": email, "password": password},
    )
    session_id: str = response["id"]
    return session_id


def _find_by_name(rows: list[JSON], name: str) -> JSON | None:
    for row in rows:
        if row.get("name") == name:
            return row
    return None


def _series_settings(series: dict[str, SeriesColor]) -> dict[str, dict[str, str]]:
    return {column: {"color": color.hex} for column, color in series.items()}


def _upsert_card(base_url: str, session_id: str, database_id: int, panel: Panel) -> JSON:
    """Creates or updates the panel's chart card; returns it as the API now has it."""
    existing_cards = _as_list(_request(base_url, "/api/card", session_id=session_id))
    existing = _find_by_name(existing_cards, panel["name"])

    body: JSON = {
        "name": panel["name"],
        "dataset_query": {
            "database": database_id,
            "type": "native",
            "native": {"query": panel["query"]},
        },
        "display": panel["display"],
        "visualization_settings": {"series_settings": _series_settings(panel["series"])},
    }
    if existing is None:
        card: JSON = _request(
            base_url, "/api/card", session_id=session_id, method="POST", body=body
        )
        return card
    updated: JSON = _request(
        base_url, f"/api/card/{existing['id']}", session_id=session_id, method="PUT", body=body
    )
    return updated


def _find_analytics_database_id(base_url: str, session_id: str) -> int:
    databases = _as_list(_request(base_url, "/api/database", session_id=session_id))
    for database in databases:
        if database.get("name") == "analytics":
            database_id: int = database["id"]
            return database_id
    raise RuntimeError("no 'analytics' database connected in Metabase yet")


def _upsert_dashboard(base_url: str, session_id: str) -> int:
    existing = _as_list(_request(base_url, "/api/dashboard", session_id=session_id))
    found = _find_by_name(existing, DASHBOARD_NAME)
    if found is not None:
        found_id: int = found["id"]
        return found_id
    created = _request(
        base_url,
        "/api/dashboard",
        session_id=session_id,
        method="POST",
        body={"name": DASHBOARD_NAME},
    )
    created_id: int = created["id"]
    return created_id


def _text_dashcard(
    existing_dashcards: list[JSON], row: int, text: str, placeholder_id: int
) -> JSON:
    dashcard_id: int = placeholder_id
    for dashcard in existing_dashcards:
        if dashcard["card_id"] is None and dashcard["row"] == row and dashcard["col"] == 0:
            dashcard_id = dashcard["id"]
            break
    return {
        "id": dashcard_id,
        "card_id": None,
        "row": row,
        "col": 0,
        "size_x": _WIDTH,
        "size_y": _ROW_HEIGHT_TEXT,
        "visualization_settings": {"virtual_card": {"display": "text"}, "text": text},
    }


def _chart_dashcard(
    existing_dashcards: list[JSON], row: int, card_id: int, placeholder_id: int
) -> JSON:
    dashcard_id: int = placeholder_id
    for dashcard in existing_dashcards:
        if dashcard["card_id"] == card_id:
            dashcard_id = dashcard["id"]
            break
    return {
        "id": dashcard_id,
        "card_id": card_id,
        "row": row,
        "col": 0,
        "size_x": _WIDTH,
        "size_y": _ROW_HEIGHT_CHART,
        "visualization_settings": {},
    }


def _checklist(cards_by_panel: dict[str, JSON]) -> str:
    lines = [
        "# Dashboard theme checklist",
        "",
        "Generated by `infra/scripts/lib/theme_metabase_dashboard.py`, reading each panel's",
        "series color back from the Metabase API after it was written — not hand-typed.",
        "Native application-wide theming (logo, app name, instance brand colors) is a paid",
        "Metabase feature this deployment doesn't have a license for; see `docs/limitations.md`.",
        "",
        "| Panel | Mart | Business question | Chart | Series | Token | Confirmed hex |",
        "|---|---|---|---|---|---|---|",
    ]
    for panel in PANELS:
        card = cards_by_panel[panel["name"]]
        confirmed_series = card.get("visualization_settings", {}).get("series_settings", {})
        for column, color in panel["series"].items():
            confirmed_hex = confirmed_series.get(column, {}).get("color", "MISSING")
            status = confirmed_hex if confirmed_hex == color.hex else f"MISMATCH ({confirmed_hex})"
            lines.append(
                f"| {panel['name']} | {panel['mart']} | {panel['question']} | "
                f"{panel['display']} | {column} | `{color.token}` | {status} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    base_url = os.environ["MB_BASE_URL"].rstrip("/")
    admin_email = os.environ["MB_ADMIN_EMAIL"]
    admin_password = os.environ["MB_ADMIN_PASSWORD"]

    session_id = _sign_in(base_url, admin_email, admin_password)
    database_id = _find_analytics_database_id(base_url, session_id)

    cards_by_panel: dict[str, JSON] = {}
    for panel in PANELS:
        cards_by_panel[panel["name"]] = _upsert_card(base_url, session_id, database_id, panel)

    dashboard_id = _upsert_dashboard(base_url, session_id)
    current = _request(base_url, f"/api/dashboard/{dashboard_id}", session_id=session_id)
    existing_dashcards: list[JSON] = current["dashcards"]

    dashcards: list[JSON] = []
    placeholder = -1
    for index, panel in enumerate(PANELS):
        text_row = index * (_ROW_HEIGHT_TEXT + _ROW_HEIGHT_CHART)
        chart_row = text_row + _ROW_HEIGHT_TEXT
        dashcards.append(
            _text_dashcard(existing_dashcards, text_row, f"## {panel['question']}", placeholder)
        )
        placeholder -= 1
        dashcards.append(
            _chart_dashcard(
                existing_dashcards, chart_row, cards_by_panel[panel["name"]]["id"], placeholder
            )
        )
        placeholder -= 1

    _request(
        base_url,
        f"/api/dashboard/{dashboard_id}",
        session_id=session_id,
        method="PUT",
        body={"dashcards": dashcards},
    )

    # Read back every card fresh (not the create/update response) so the checklist reflects what
    # the API now actually has, the same "prove what was shown" rule the audit trail follows.
    refreshed_cards: dict[str, JSON] = {}
    for panel in PANELS:
        card_id = cards_by_panel[panel["name"]]["id"]
        refreshed_cards[panel["name"]] = _request(
            base_url, f"/api/card/{card_id}", session_id=session_id
        )

    sys.stdout.write(_checklist(refreshed_cards))


if __name__ == "__main__":
    main()
