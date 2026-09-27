"""
Cleaned Layer ("Silver")
========================

Overview
--------
Turns the raw CSV tables into typed, de-duplicated Parquet tables that satisfy the data
contract, sends every row that does not to a quarantine table with the reason, and writes a
manifest per table that records exactly what went in, what came out and under which rules.

Scope
-----
In: typing, contract checks, canonical spellings, de-duplication, reference enforcement,
quarantine, unknown-column sidecar, manifests and idempotent re-runs.
Out: analytical marts, features and any write to the raw data.

Design Principles
-----------------
- Deterministic: the same raw files, contract version and code version give byte-identical
  outputs and manifests. Nothing in an output depends on the clock or on thread scheduling.
- Idempotent: a table whose inputs, contract version, code version and outputs are unchanged is
  not rebuilt.
- Nothing is silently lost: every raw row is either in the output, superseded by a newer
  version of the same key (counted), or in quarantine with a reason code.
- Late and re-delivered records are handled by construction: a table is always rebuilt from all
  of its partitions, and for a repeated key the latest version wins (by ``last_updated`` where
  the table has it, otherwise by partition day), with a content hash as final tie-break.
- Data values never reach logs or manifests; only counts and reason codes do.

Runtime Contract
----------------
``run_silver(raw_dir, out_dir, code_version=..., force=False) -> tuple[TableOutcome, ...]``
``python -m pipelines.silver --raw data/raw --out data/silver``

Limitations
-----------
A changed table is rebuilt from all of its partitions rather than only from the changed ones;
this is exact and takes minutes at the current data size. Partition-level incremental loading is
the designated step when the data outgrows that.

A version of a key that breaks the contract is quarantined and never supersedes an older valid
version: the older version stays in the cleaned table. Quarantined rows hold the text as
delivered, with canonical spellings applied; rows quarantined for an orphan reference have
already been typed and hold their values rendered as text (``false`` for ``False``, ``10.0000``
for ``10.00``). The code version
records the commit; when tracked files have uncommitted changes it is suffixed
``-dirty-<digest>``, the digest identifying those changes, and it is ``unknown`` outside a
repository. The digest is taken over the output of ``git diff``, so it also depends on the
machine's diff formatting configuration (``diff.noprefix``, ``diff.context``,
``diff.algorithm``).
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command-line interface
import hashlib  # Content digests of outputs
import json  # Manifest serialization
import logging  # Progress events on stderr
import os  # Atomic replacement of outputs
import subprocess  # Resolve the code version from git
import sys  # Exit codes and log stream
import tempfile  # Throwaway working database
import time  # Timing for progress events
from collections.abc import Sequence  # Type of the specs argument
from dataclasses import dataclass  # Immutable result objects
from pathlib import Path  # Input and output locations
from typing import Any  # Manifest values

# Third-party libraries
import duckdb  # Columnar SQL engine

# Local modules
from contracts.v1 import (  # Rules applied to every table
    CONTRACT_VERSION,
    ReferenceAction,
    TableContract,
    contract_for,
    load_order,
)
from pipelines.inventory import TableInventory, content_digest, scan_table  # Input facts
from pipelines.outcomes import Status, TableOutcome  # Result of each table
from pipelines.quality import render_quality_report  # Data-quality report
from pipelines.raw import (  # Strict loading shared with the profiler
    TableLoadError,
    load_table,
    quote_identifier,
    quote_literal,
    reject_invalid_headers,
    verify_headers,
)
from pipelines.sources import TABLES, Column, TableSpec, table  # Table registry

logger = logging.getLogger(__name__)

# Database errors that come from the content of the files; any other error (disk, memory, an
# internal failure) is not the data's fault and must stop the run instead of skipping a table.
FILE_ERRORS = (duckdb.InvalidInputException, duckdb.ConversionException)

# Integers up to 2^53 are exactly representable as DOUBLE.
EXACT_DOUBLE_LIMIT = 9007199254740992

# -----------------------------------------------------------------------------
# Types
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SilverPaths:
    """Locations of every artefact of the cleaned layer under one root."""

    root: Path

    def silver(self, name: str) -> Path:
        """Parquet file of the cleaned table."""
        return self.root / "silver" / f"{name}.parquet"

    def quarantine(self, name: str) -> Path:
        """Parquet file of the rows that did not satisfy the contract."""
        return self.root / "quarantine" / f"{name}.parquet"

    def extras(self, name: str) -> Path:
        """Parquet file holding the values of columns the contract does not declare."""
        return self.root / "extras" / f"{name}.parquet"

    def manifest(self, name: str) -> Path:
        """JSON manifest of the table."""
        return self.root / "manifests" / f"{name}.json"


# -----------------------------------------------------------------------------
# SQL builders
# -----------------------------------------------------------------------------


def _typed_expression(column: Column) -> str:
    """SQL that converts the raw text of ``column`` to its declared type, or NULL when it cannot."""
    raw = quote_identifier(column.name)
    dtype = column.dtype.upper()
    if dtype in {"DATE", "TIMESTAMP", "TIME"}:
        return f"try_cast({raw} AS {dtype})"
    if dtype == "INTEGER":
        # Exact for integer text (the database would round "701.5" to 702, so the text is matched
        # first); integers written with a decimal point ("701.0") go through
        # DOUBLE but only inside the range where every integer is exactly representable, so
        # NaN, infinities and huge values become NULL instead of failing the query.
        number = f"try_cast({raw} AS DOUBLE)"
        via_double = (
            f"CASE WHEN regexp_full_match({raw}, '[+-]?[0-9]+[.][0-9]+') "
            f"AND {number} = floor({number}) AND abs({number}) < {EXACT_DOUBLE_LIMIT} "
            f"THEN CAST({number} AS BIGINT) END"
        )
        plain = f"regexp_full_match({raw}, '[+-]?[0-9]+')"
        return f"CASE WHEN {plain} THEN try_cast({raw} AS BIGINT) ELSE {via_double} END"
    if dtype.startswith("DECIMAL"):
        # A value is kept only when it is stored exactly. The text must be a number without
        # padding or a bare point, with at most the declared digits after the point; an exponent
        # ("-1.19e-05") is allowed when the stored value equals the wider, exact reading of the
        # text, so "1.5e-5" in a two-decimal column is rejected instead of rounded to zero.
        scale = int(dtype.rstrip(")").split(",")[1])
        shape = f"regexp_full_match({raw}, '[+-]?[0-9]+([.][0-9]+)?([eE][+-]?[0-9]+)?')"
        excess = f"regexp_matches({raw}, '[.][0-9]{{{scale + 1},}}')"
        wide = f"try_cast({raw} AS DECIMAL(38, 18))"
        narrow = f"try_cast({raw} AS {dtype})"
        not_zeroed = f"({wide} <> 0 OR try_cast({raw} AS DOUBLE) = 0)"
        return (
            f"CASE WHEN {shape} AND NOT {excess} AND {wide} = {narrow} AND {not_zeroed} "
            f"THEN {narrow} END"
        )
    if dtype == "BOOLEAN":
        return f"CASE {raw} WHEN 'True' THEN true WHEN 'False' THEN false END"
    return raw


def _canonical_expression(name: str, contract: TableContract) -> str:
    """SQL for the raw text of ``name`` after canonical spellings are applied."""
    raw = quote_identifier(name)
    for rule in contract.canonical:
        if rule.column == name:
            cases = " ".join(
                f"WHEN {raw} = {quote_literal(old)} THEN {quote_literal(new)}"
                for old, new in rule.mapping
            )
            return f"CASE {cases} ELSE {raw} END"
    return raw


def _issue_expressions(spec: TableSpec, contract: TableContract) -> list[str]:
    """One SQL expression per possible contract violation; each is a reason code or NULL."""
    allowed = {rule.column: rule for rule in contract.allowed}
    ranges = {rule.column: rule for rule in contract.ranges}
    issues: list[str] = []
    for column in spec.columns:
        raw, typed = quote_identifier(column.name), quote_identifier(f"{column.name}__typed")
        if not column.nullable:
            issues.append(
                f"CASE WHEN {raw} IS NULL THEN {quote_literal('required:' + column.name)} END"
            )
        issues.append(
            f"CASE WHEN {raw} IS NOT NULL AND {typed} IS NULL "
            f"THEN {quote_literal('type:' + column.name)} END"
        )
        if column.name in allowed:
            values = ", ".join(quote_literal(v) for v in sorted(allowed[column.name].values))
            issues.append(
                f"CASE WHEN {typed} IS NOT NULL AND {typed} NOT IN ({values}) "
                f"THEN {quote_literal('value:' + column.name)} END"
            )
        if column.name in ranges:
            bounds = ranges[column.name]
            issues.append(
                f"CASE WHEN {typed} IS NOT NULL AND ({typed} < {bounds.minimum} "
                f"OR {typed} > {bounds.maximum}) THEN {quote_literal('range:' + column.name)} END"
            )
    return issues


def _order_for_latest(spec: TableSpec) -> str:
    """ORDER BY clause that ranks the versions of one key, latest first."""
    parts = []
    if "last_updated" in spec.column_names:
        parts.append(f"{quote_identifier('last_updated__typed')} DESC NULLS LAST")
    parts += ["_partition_date DESC NULLS LAST", "_row_hash DESC"]
    return ", ".join(parts)


# -----------------------------------------------------------------------------
# Files and manifests
# -----------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, document: dict[str, Any]) -> None:
    """Write ``document`` deterministically (sorted keys, trailing newline) and atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _copy_to_parquet(con: duckdb.DuckDBPyConnection, query: str, target: Path) -> None:
    """Write the result of ``query`` to ``target`` atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".parquet.tmp")
    con.execute(f"COPY ({query}) TO {quote_literal(str(temporary))} (FORMAT PARQUET)")
    os.replace(temporary, target)


def _read_manifest(path: Path) -> dict[str, Any] | None:
    """The stored manifest, or None when absent or unreadable."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _dependencies(contract: TableContract, available: dict[str, str]) -> dict[str, str | None]:
    """Output digest of every table this one references (``None`` when it is not available).

    Recorded in the manifest so a table is rebuilt whenever a table it references changes,
    because its references must be judged against the new content.
    """
    return {rule.ref_table: available.get(rule.ref_table) for rule in contract.references}


def _outputs_intact(paths: SilverPaths, name: str, outputs: dict[str, Any]) -> bool:
    """True when every artefact of the table exists and matches the digest in its manifest."""
    for kind, path in (
        ("silver_sha256", paths.silver(name)),
        ("quarantine_sha256", paths.quarantine(name)),
    ):
        if not path.is_file() or outputs.get(kind) != _sha256(path):
            return False
    extras = paths.extras(name)
    recorded = outputs.get("extras_sha256")
    if recorded is None:
        return not extras.exists()
    return extras.is_file() and recorded == _sha256(extras)


def _is_current(
    paths: SilverPaths,
    name: str,
    *,
    digest: str,
    code_version: str,
    dependencies: dict[str, str | None],
) -> dict[str, Any] | None:
    """The stored manifest when the table is already built for exactly these inputs.

    The inputs are the raw files, the contract and code versions, the database engine version
    and the outputs of the referenced tables; the outputs must also still match their digests.
    """
    manifest = _read_manifest(paths.manifest(name))
    if manifest is None:
        return None
    same_inputs = (
        manifest.get("contract_version") == CONTRACT_VERSION
        and manifest.get("code_version") == code_version
        and manifest.get("engine_version") == duckdb.__version__
        and manifest.get("input", {}).get("digest") == digest
        and manifest.get("dependencies") == dependencies
    )
    intact = _outputs_intact(paths, name, manifest.get("outputs", {}))
    return manifest if same_inputs and intact else None


def _clear_table(paths: SilverPaths, name: str) -> None:
    """Remove every artefact of a table, manifest first.

    Called before a rebuild and when a table is skipped, so no manifest or output of an earlier
    build can be mistaken for the result of the current run.
    """
    paths.manifest(name).unlink(missing_ok=True)
    for artefact in (paths.silver(name), paths.quarantine(name), paths.extras(name)):
        artefact.unlink(missing_ok=True)


# -----------------------------------------------------------------------------
# One table
# -----------------------------------------------------------------------------


def _checked_query(spec: TableSpec, contract: TableContract, present: set[str]) -> str:
    """SQL producing, per raw row, the canonical raw text, the typed columns, a content hash and
    the list of contract issues.

    The query is evaluated on demand from the raw files rather than stored, so a very large
    table never exists on disk in more than its cleaned form.
    """
    raw_columns = []
    for column in spec.columns:
        expression = (
            _canonical_expression(column.name, contract) if column.name in present else "NULL"
        )
        raw_columns.append(f"CAST({expression} AS VARCHAR) AS {quote_identifier(column.name)}")
    hashed = ", ".join(
        f"coalesce({quote_identifier(c.name)}, '<null>')"
        for c in spec.columns
        if c.name != "process_date"
    )
    typed_columns = ", ".join(
        f"{_typed_expression(c)} AS {quote_identifier(c.name + '__typed')}" for c in spec.columns
    )
    issues = ", ".join(_issue_expressions(spec, contract))
    staged = f"SELECT {', '.join(raw_columns)}, _partition_date FROM {quote_identifier(spec.name)}"
    typed = f"SELECT *, {typed_columns}, md5(concat_ws('|', {hashed})) AS _row_hash FROM ({staged})"
    return f"SELECT *, list_filter([{issues}], x -> x IS NOT NULL) AS _issues FROM ({typed})"


def _reference_sql(rule_column: str, ref_table: str, ref_column: str, paths: SilverPaths) -> str:
    """SQL condition that is true when a non-null reference has no row in the referenced table."""
    column = quote_identifier(f"{rule_column}__typed")
    source = quote_literal(str(paths.silver(ref_table)))
    return (
        f"({column} IS NOT NULL AND {column} NOT IN "
        f"(SELECT {quote_identifier(ref_column)} FROM read_parquet({source}) "
        f"WHERE {quote_identifier(ref_column)} IS NOT NULL))"
    )


@dataclass(frozen=True, slots=True)
class _References:
    """Outcome of the reference checks: flag counts and the columns that could not be checked."""

    flagged: dict[str, int]
    unchecked: list[str]


def _deduplicate(con: duckdb.DuckDBPyConnection, spec: TableSpec, checked: str) -> tuple[int, int]:
    """Build ``quarantined`` and ``kept``; return (rows read, versions superseded).

    Rows that break the contract go to ``quarantined`` with their raw text. Of the others, the
    latest version of each key is kept as typed columns only, so at most one typed copy of the
    data is stored.
    """
    key = ", ".join(quote_identifier(f"{k}__typed") for k in spec.primary_key)
    raw_names = ", ".join(quote_identifier(c.name) for c in spec.columns)
    typed_names = ", ".join(quote_identifier(f"{c.name}__typed") for c in spec.columns)
    con.execute(
        f"CREATE TABLE quarantined AS SELECT {raw_names}, _partition_date, _row_hash, "
        f"_issues[1] AS reason FROM ({checked}) WHERE len(_issues) > 0"
    )
    con.execute(
        f"CREATE TABLE kept AS SELECT {typed_names}, _partition_date, _row_hash FROM ("
        f"SELECT *, row_number() OVER (PARTITION BY {key} ORDER BY {_order_for_latest(spec)}) "
        f"AS _rank FROM ({checked}) WHERE len(_issues) = 0) WHERE _rank = 1"
    )
    (rows_in,) = con.execute(f"SELECT count(*) FROM {quote_identifier(spec.name)}").fetchone() or (
        0,
    )
    (rejected,) = con.execute("SELECT count(*) FROM quarantined").fetchone() or (0,)
    (kept,) = con.execute("SELECT count(*) FROM kept").fetchone() or (0,)
    return int(rows_in), int(rows_in) - int(rejected) - int(kept)


def _apply_references(
    con: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    contract: TableContract,
    paths: SilverPaths,
    available: dict[str, str],
) -> _References:
    """Enforce or flag references to the tables cleaned in this run.

    Rows with an orphan required reference are appended to ``quarantined``; the remaining rows
    are exposed as the view ``silver_rows``. A reference to a table that was not cleaned cannot
    be judged and is reported as unchecked.
    """
    unchecked: list[str] = []
    flag_conditions: list[tuple[str, str]] = []
    orphan_conditions: list[tuple[str, str]] = []
    for rule in contract.references:
        if rule.ref_table not in available:
            unchecked.append(rule.column)
            continue
        condition = _reference_sql(rule.column, rule.ref_table, rule.ref_column, paths)
        target = orphan_conditions if rule.action is ReferenceAction.QUARANTINE else flag_conditions
        target.append((rule.column, condition))
    if orphan_conditions:
        reason = (
            "CASE "
            + " ".join(
                f"WHEN {cond} THEN {quote_literal('reference:' + col)}"
                for col, cond in orphan_conditions
            )
            + " END"
        )
        as_text = ", ".join(
            f"CAST({quote_identifier(c.name + '__typed')} AS VARCHAR) AS {quote_identifier(c.name)}"
            for c in spec.columns
        )
        con.execute(f"CREATE VIEW orphans AS SELECT *, {reason} AS _reason FROM kept")
        con.execute(
            f"INSERT INTO quarantined SELECT {as_text}, _partition_date, _row_hash, "
            "_reason FROM orphans WHERE _reason IS NOT NULL"
        )
        con.execute(
            "CREATE VIEW silver_rows AS SELECT * EXCLUDE (_reason) FROM orphans "
            "WHERE _reason IS NULL"
        )
    else:
        con.execute("CREATE VIEW silver_rows AS SELECT * FROM kept")
    # Flag counts describe the rows that remain in the cleaned table
    flagged: dict[str, int] = {}
    for column, condition in flag_conditions:
        (count,) = con.execute(
            f"SELECT count(*) FROM silver_rows WHERE {condition}"
        ).fetchone() or (0,)
        flagged[column] = int(count)
    return _References(flagged, unchecked)


def _write_extras(
    con: duckdb.DuckDBPyConnection, spec: TableSpec, present: set[str], paths: SilverPaths
) -> tuple[list[str], str | None]:
    """Write the sidecar of undeclared columns; return their names and the sidecar digest.

    A sidecar left by an earlier run is removed when the data no longer has extra columns.
    """
    extra = sorted(present - set(spec.column_names) - {"_partition_date"})
    if not extra:
        paths.extras(spec.name).unlink(missing_ok=True)
        return [], None
    extra_names = ", ".join(quote_identifier(name) for name in extra)
    keys = ", ".join(quote_identifier(k) for k in spec.primary_key)
    _copy_to_parquet(
        con,
        f"SELECT {keys}, _partition_date, {extra_names} FROM {quote_identifier(spec.name)} "
        f"ORDER BY {keys}, _partition_date, {extra_names}",
        paths.extras(spec.name),
    )
    return extra, _sha256(paths.extras(spec.name))


def _write_tables(con: duckdb.DuckDBPyConnection, spec: TableSpec, paths: SilverPaths) -> None:
    """Write the cleaned table and the quarantine table in a deterministic order."""
    key = ", ".join(quote_identifier(f"{k}__typed") for k in spec.primary_key)
    typed_names = ", ".join(
        f"{quote_identifier(c.name + '__typed')} AS {quote_identifier(c.name)}"
        for c in spec.columns
    )
    _copy_to_parquet(
        con, f"SELECT {typed_names} FROM silver_rows ORDER BY {key}", paths.silver(spec.name)
    )
    order_quarantine = ", ".join(
        ["reason", *(quote_identifier(k) for k in spec.primary_key), "_partition_date", "_row_hash"]
    )
    _copy_to_parquet(
        con, f"SELECT * FROM quarantined ORDER BY {order_quarantine}", paths.quarantine(spec.name)
    )


def _process_table(
    con: duckdb.DuckDBPyConnection,
    spec: TableSpec,
    present: set[str],
    *,
    paths: SilverPaths,
    available: dict[str, str],
    inventory: TableInventory,
    digest: str,
    code_version: str,
) -> dict[str, Any]:
    """Clean one loaded table, write its artefacts and return its manifest."""
    contract = contract_for(spec.name)
    extra, extras_hash = _write_extras(con, spec, present, paths)
    rows_in, superseded = _deduplicate(con, spec, _checked_query(spec, contract, present))
    references = _apply_references(con, spec, contract, paths, available)
    _write_tables(con, spec, paths)

    reasons = {
        str(reason): int(count)
        for reason, count in con.execute(
            "SELECT reason, count(*) FROM quarantined GROUP BY reason ORDER BY reason"
        ).fetchall()
    }
    (rows_out,) = con.execute("SELECT count(*) FROM silver_rows").fetchone() or (0,)
    manifest: dict[str, Any] = {
        "table": spec.name,
        "contract_version": CONTRACT_VERSION,
        "code_version": code_version,
        "engine_version": duckdb.__version__,
        "dependencies": _dependencies(contract, available),
        "input": {
            "files": inventory.files,
            "bytes": inventory.total_bytes,
            "digest": digest,
            "first_partition": inventory.first_partition,
            "last_partition": inventory.last_partition,
        },
        "counts": {
            "rows_in": rows_in,
            "rows_out": int(rows_out),
            "quarantined": sum(reasons.values()),
            "superseded": superseded,
        },
        "quarantine_reasons": reasons,
        "flagged_references": dict(sorted(references.flagged.items())),
        "unchecked_references": sorted(references.unchecked),
        "extra_columns": extra,
        "outputs": {
            "silver_sha256": _sha256(paths.silver(spec.name)),
            "quarantine_sha256": _sha256(paths.quarantine(spec.name)),
            "extras_sha256": extras_hash,
        },
    }
    _write_json(paths.manifest(spec.name), manifest)
    return manifest


def _build_table(
    raw_dir: Path,
    spec: TableSpec,
    inventory: TableInventory,
    *,
    digest: str,
    paths: SilverPaths,
    available: dict[str, str],
    code_version: str,
) -> TableOutcome:
    """Load and clean one table in a private working database that is removed afterwards.

    Removing the database as soon as the table is done keeps the disk footprint of a run at
    one table at a time. Artefacts of an earlier build are removed first, so a table that fails
    leaves nothing that could be mistaken for a good build. Only errors caused by the files
    themselves skip the table; failures of the environment (disk, memory) stop the run.
    """
    started = time.monotonic()
    _clear_table(paths, spec.name)
    with tempfile.TemporaryDirectory() as workdir:
        con = duckdb.connect(str(Path(workdir) / "work.duckdb"))
        con.execute("SET preserve_insertion_order = false")
        try:
            reject_invalid_headers(inventory)
            present = load_table(con, raw_dir, spec, as_view=True)
            verify_headers(present, inventory)
            manifest = _process_table(
                con,
                spec,
                present,
                paths=paths,
                available=available,
                inventory=inventory,
                digest=digest,
                code_version=code_version,
            )
        except (TableLoadError, *FILE_ERRORS) as exc:
            # The database's own message can quote a line of the data: keep the class only
            reason = (
                str(exc)
                if isinstance(exc, TableLoadError)
                else f"{type(exc).__name__}: the files of table {spec.name} could not be parsed"
            )
            _clear_table(paths, spec.name)
            logger.warning("silver_table_skipped table=%s reason=%s", spec.name, reason)
            return TableOutcome(spec.name, Status.SKIPPED, reason, None)
        finally:
            con.close()
    logger.info(
        "silver_table_built table=%s rows_out=%d quarantined=%d seconds=%.1f",
        spec.name,
        manifest["counts"]["rows_out"],
        manifest["counts"]["quarantined"],
        time.monotonic() - started,
    )
    return TableOutcome(spec.name, Status.BUILT, None, manifest)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------


def run_silver(
    raw_dir: Path,
    out_dir: Path,
    *,
    code_version: str = "unknown",
    force: bool = False,
    specs: Sequence[TableSpec] = TABLES,
) -> tuple[TableOutcome, ...]:
    """Build the cleaned layer for every table that has raw files.

    Parameters
    ----------
    raw_dir : Path
        Root of the raw data.
    out_dir : Path
        Root of the cleaned layer (``silver/``, ``quarantine/``, ``extras/``, ``manifests/``).
    code_version : str
        Identifier of the code producing the outputs, recorded in every manifest.
    force : bool
        Rebuild tables even when their inputs and outputs are unchanged.
    specs : Sequence[TableSpec]
        Tables to consider; they are processed in dependency order.

    Returns
    -------
    tuple[TableOutcome, ...]
        One outcome per table that has raw files, in processing order.
    """
    paths = SilverPaths(out_dir)
    wanted = {spec.name for spec in specs}
    outcomes: list[TableOutcome] = []
    available: dict[str, str] = {}
    for name in (n for n in load_order() if n in wanted):
        spec = table(name)
        inventory = scan_table(raw_dir, spec)
        if inventory.files == 0:
            logger.warning("silver_table_missing table=%s", name)
            continue
        digest = content_digest(raw_dir, (spec,))
        dependencies = _dependencies(contract_for(name), available)
        current = (
            None
            if force
            else _is_current(
                paths, name, digest=digest, code_version=code_version, dependencies=dependencies
            )
        )
        if current is not None:
            outcome = TableOutcome(name, Status.UNCHANGED, None, current)
            logger.info("silver_table_unchanged table=%s", name)
        else:
            outcome = _build_table(
                raw_dir,
                spec,
                inventory,
                digest=digest,
                paths=paths,
                available=available,
                code_version=code_version,
            )
        if outcome.manifest is not None:
            available[name] = outcome.manifest["outputs"]["silver_sha256"]
        outcomes.append(outcome)
    return tuple(outcomes)


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def _git_bytes(*arguments: str) -> bytes | None:
    """Raw output of a git command, or None when git is unavailable or the command fails."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            ["git", *arguments],  # noqa: S607 - resolved through PATH by design
            capture_output=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def _git(*arguments: str) -> str | None:
    """Text output of a git command, or None when git is unavailable or the command fails."""
    output = _git_bytes(*arguments)
    return None if output is None else output.decode("utf-8", errors="replace").strip()


def git_version() -> str:
    """Short commit id of the working tree, suffixed with a digest of its changes when modified.

    A tree with uncommitted changes to tracked files is stamped ``<commit>-dirty-<digest>``,
    the digest covering staged and unstaged changes: the same modified tree always yields the
    same stamp and two different modified trees never share one. ``unknown`` outside a
    repository or when the changes cannot be read, so a stamp never claims more than is known.
    """
    commit = _git("rev-parse", "--short", "HEAD")
    if not commit:
        return "unknown"
    changes = _git_bytes("diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv", "--no-color")
    if changes is None:
        return "unknown"
    if not changes:
        return commit
    return f"{commit}-dirty-{hashlib.sha256(changes).hexdigest()[:8]}"


def main(argv: Sequence[str] | None = None) -> int:
    """Build the cleaned layer and the data-quality report.

    Returns
    -------
    int
        0 when every table with raw files was built or unchanged, 1 when any was skipped.
    """
    parser = argparse.ArgumentParser(description="Build the cleaned layer from the raw data.")
    parser.add_argument("--raw", type=Path, default=Path("data/raw"), help="raw data directory")
    parser.add_argument("--out", type=Path, default=Path("data/silver"), help="cleaned layer root")
    parser.add_argument(
        "--report", type=Path, default=Path("reports/data-quality.md"), help="Markdown report"
    )
    parser.add_argument("--code-version", default=None, help="recorded in manifests (default: git)")
    parser.add_argument("--force", action="store_true", help="rebuild unchanged tables")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr
    )
    outcomes = run_silver(
        args.raw, args.out, code_version=args.code_version or git_version(), force=args.force
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(render_quality_report(outcomes), encoding="utf-8")
    logger.info("silver_report_written report=%s", args.report)
    return 1 if any(o.status is Status.SKIPPED for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
