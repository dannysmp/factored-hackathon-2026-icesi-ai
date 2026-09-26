"""
Data-Quality Report Renderer
============================

Overview
--------
Turns the outcomes of a cleaning run into a Markdown report: what entered, what was kept,
what was superseded by a newer version of the same key, what was quarantined and why, which
references were flagged, which columns were unknown and which tables could not be processed.

Scope
-----
In: formatting the manifests of a run.
Out: producing them (``pipelines.silver``).

Design Principles
-----------------
Pure function of the manifests: no clock and no filesystem, so the same run always renders the
same text. Only counts and reason codes appear; data values never do.

Runtime Contract
----------------
``render_quality_report(outcomes) -> str``
"""

from __future__ import annotations

# Standard libraries
from collections.abc import Sequence  # Type of the outcomes argument

# Local modules
from pipelines.outcomes import Status, TableOutcome  # Result of each table

# Number of hexadecimal digits of a digest shown in the report.
DIGEST_DIGITS = 12


def _count(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a Markdown table; an empty row list renders a single 'none' row."""
    body = rows or [["none", *[""] * (len(headers) - 1)]]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def render_quality_report(outcomes: Sequence[TableOutcome]) -> str:
    """Render the report for one cleaning run.

    Parameters
    ----------
    outcomes : Sequence[TableOutcome]
        Result of each table, as returned by ``run_silver``.

    Returns
    -------
    str
        The complete Markdown report, ending with a newline.
    """
    built = [o for o in outcomes if o.manifest is not None]
    summary, reasons, references, extras = [], [], [], []
    for outcome in built:
        manifest = outcome.manifest or {}
        counts = manifest["counts"]
        summary.append(
            [
                outcome.table,
                outcome.status.value,
                _count(counts["rows_in"]),
                _count(counts["rows_out"]),
                _count(counts["superseded"]),
                _count(counts["quarantined"]),
                manifest["outputs"]["silver_sha256"][:DIGEST_DIGITS],
            ]
        )
        reasons += [
            [outcome.table, reason, _count(count)]
            for reason, count in manifest["quarantine_reasons"].items()
        ]
        references += [
            [outcome.table, column, "flagged", _count(count)]
            for column, count in manifest["flagged_references"].items()
        ]
        references += [
            [outcome.table, column, "not checked (referenced table not cleaned)", ""]
            for column in manifest["unchecked_references"]
        ]
        if manifest["extra_columns"]:
            extras.append([outcome.table, ", ".join(manifest["extra_columns"])])
    skipped = [[o.table, o.reason or ""] for o in outcomes if o.status is Status.SKIPPED]
    version = built[0].manifest["contract_version"] if built and built[0].manifest else "n/a"
    sections = [
        "# Data Quality Report",
        f"Contract version {version}. Every figure is computed by `make pipeline`; a row is "
        "either kept, superseded by a newer version of the same key, or quarantined with a "
        "reason.",
        "## 1. Outcome per table\n\n"
        + _table(
            [
                "Table",
                "Status",
                "Rows in",
                "Rows out",
                "Superseded",
                "Quarantined",
                "Output digest",
            ],
            summary,
        ),
        "## 2. Quarantine reasons\n\nReason codes are `required` (missing value in a required "
        "column), `type` (does not parse as the declared type), `value` (outside the allowed "
        "values), `range` (outside the numeric range) and `reference` (points at no existing "
        "row).\n\n" + _table(["Table", "Reason", "Rows"], reasons),
        "## 3. References\n\n" + _table(["Table", "Column", "Handling", "Rows"], references),
        "## 4. Columns not in the contract\n\n" + _table(["Table", "Columns"], extras),
        "## 5. Tables not processed\n\n" + _table(["Table", "Reason"], skipped),
    ]
    return "\n\n".join(sections) + "\n"
