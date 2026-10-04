"""
Risk Signal Probe
=================

Overview
--------
The first modelling step on the risk feature mart: a logistic regression baseline and a small,
capped gradient-boosted classifier, both fitted on the training period on identical features and
then scored on the validation period with the area under the precision-recall curve (PR-AUC),
set against the period's base rate (its fraud prevalence). The question it answers is whether the
features carry any signal at all. Every run is appended to the experiment log. The shared
loading, preprocessing and logging helpers defined here are reused by `models.boosted` and
`models.calibration`.

Scope
-----
In: loading the train and validation periods, one shared preprocessing step fitted on train only,
fitting both models, the validation metric, the experiment-log entry and the command line
``python -m models.probe``.
Out: the precision floor, the threshold, the bootstrap interval, the routing decision and the
model card. Those belong to `models.boosted` and `models.calibration`; this probe only reports
whether the features separate fraud from non-fraud better than chance.

Design Principles
-----------------
- **The test period is never read.** Only rows whose ``split`` is ``train`` or ``validation`` are
  queried; a test-period row cannot move a metric this probe reports.
- **Identical features for both models:** one ``ColumnTransformer`` (one-hot encoding for text
  columns, median imputation for numeric ones) is fitted on the training rows only and used to
  transform both periods; both models are fitted on its output.
- **A capped fit, not a wall-clock timeout.** The boosted model's complexity (`BOOST_MAX_ITER`,
  `BOOST_MAX_DEPTH`) is fixed low so the fit finishes quickly on any machine, deterministically,
  rather than racing a clock: the probe must run every time, on every machine, in bounded time.
- **Deterministic.** A fixed seed (`SEED`) is used for both models; the same mart and split give
  the same metrics.
- **The experiment log is append-only** (`models/experiments.jsonl`): each run adds one line and
  no line is ever edited, so the record of every run that has been done stays intact.
- **No business-logic clock.** The run timestamp is a parameter (`now`), supplied by the caller;
  the module never reads the wall clock itself.

Runtime Contract
----------------
``load_period(con, mart, column_types, period, feature_names) -> PeriodArrays``
``run_probe(mart, manifest, *, split, seed, now) -> ProbeResult``
``append_experiment(result, log_path) -> None``

Limitations
-----------
No feature ablation runs here (the with/without comparison for ``customer_country`` and
``country_mismatch`` described in ``models/README.md``); `models.boosted` runs it. A validation
PR-AUC clearly above the base rate is a necessary, not sufficient, condition for a usable score:
the threshold is chosen later, on the validation period, and its precision is measured once on
the test period.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import json  # Manifest and experiment-log serialisation
import logging  # Progress events
from collections.abc import Sequence  # Argument type of main
from dataclasses import dataclass  # Immutable result objects
from datetime import UTC, datetime  # The run timestamp, supplied by the caller
from pathlib import Path  # Locations
from typing import Protocol  # The experiment-log entry contract shared by every run type

# Third-party libraries
import duckdb  # Reading the mart
import numpy as np  # Feature matrices
from sklearn.compose import ColumnTransformer  # Shared preprocessing for both models
from sklearn.ensemble import HistGradientBoostingClassifier  # The capped boosted model
from sklearn.impute import SimpleImputer  # Median-fills the numeric columns
from sklearn.linear_model import LogisticRegression  # The baseline model
from sklearn.metrics import average_precision_score  # Area under the precision-recall curve
from sklearn.preprocessing import OneHotEncoder  # Encodes the text columns

# Local modules
from pipelines.risk_features import DEFAULT_SPLIT, FEATURES, SplitConfig, load_split
from pipelines.silver import git_version  # Same code-version rule as the other pipelines

logger = logging.getLogger(__name__)

DEFAULT_MART = Path("data/gold/risk_features/risk_features.parquet")
DEFAULT_MANIFEST = Path("data/gold/risk_features/manifest.json")
DEFAULT_LOG = Path("models/experiments.jsonl")
PERIODS = ("train", "validation")

# Fixed for every run of this probe, so results are comparable across commits. The boosted
# model's cap keeps the fit bounded in time (see the module docstring).
SEED = 20260926
BOOST_MAX_ITER = 50
BOOST_MAX_DEPTH = 3

_TYPE_QUERY = "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))"
_PERIOD_QUERY = "SELECT {columns} FROM read_parquet(?) WHERE split = ?"


@dataclass(frozen=True, slots=True)
class PeriodArrays:
    """One period's feature matrix and label, split by column type.

    Text features go in `categorical` and numeric ones in `numeric`; each keeps the order its
    names have in `FEATURES`, which is the column order `_preprocessor` addresses by position.
    `rows` is the period's row count and `positives` its number of fraud-labelled rows.
    """

    categorical: np.ndarray  # object, shape (rows, n_categorical)
    numeric: np.ndarray  # float64, shape (rows, n_numeric)
    label: np.ndarray  # bool, shape (rows,)
    rows: int
    positives: int


@dataclass(frozen=True, slots=True)
class ModelResult:
    """One model's name, the parameters it was fitted with and its validation PR-AUC."""

    name: str
    params: dict[str, object]
    validation_pr_auc: float


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """The full outcome of one probe run, ready to be appended to the experiment log.

    Records the code version, the mart's own version and output digest, the split boundaries and
    seed, the row and positive counts per period, the validation base rate (positives over rows,
    `0.0` for an empty period) and each model's result.
    """

    timestamp: str
    code_version: str
    mart_code_version: str
    mart_output_sha256: str
    split: SplitConfig
    seed: int
    rows: dict[str, int]
    positives: dict[str, int]
    validation_base_rate: float
    models: tuple[ModelResult, ...]

    def as_dict(self) -> dict[str, object]:
        """The result as JSON-serialisable data, one line of the experiment log.

        Split dates are rendered as ISO strings; the keys match the dataclass fields.
        """
        return {
            "timestamp": self.timestamp,
            "code_version": self.code_version,
            "mart_code_version": self.mart_code_version,
            "mart_output_sha256": self.mart_output_sha256,
            "split": {
                "train_end": self.split.train_end.isoformat(),
                "validation_end": self.split.validation_end.isoformat(),
            },
            "seed": self.seed,
            "rows": self.rows,
            "positives": self.positives,
            "validation_base_rate": self.validation_base_rate,
            "models": [
                {"name": m.name, "params": m.params, "validation_pr_auc": m.validation_pr_auc}
                for m in self.models
            ],
        }


def _column_types(con: duckdb.DuckDBPyConnection, mart: str) -> dict[str, str]:
    """The DuckDB type name of every column of the parquet file `mart`, keyed by column name.

    `load_period` uses it to treat `VARCHAR` columns as text features and every other type as
    numeric.
    """
    return dict(con.execute(_TYPE_QUERY, [mart]).fetchall())


def _as_categorical(column: np.ndarray) -> np.ndarray:
    """`column` as an object array of strings, `"missing"` where the value is empty.

    `MaskedArray.filled(None)` is not "fill with `None`": a `None` argument means "use the
    default fill value", which for an object array is not `None` either. The mask is read
    directly instead, so an empty value always becomes the literal string `"missing"`.
    """
    if isinstance(column, np.ma.MaskedArray):
        mask = np.ma.getmaskarray(column)
        return np.array(
            ["missing" if empty else str(v) for empty, v in zip(mask, column.data, strict=True)],
            dtype=object,
        )
    return np.array([str(v) for v in column], dtype=object)


def _as_numeric(column: np.ndarray) -> np.ndarray:
    """`column` as a float64 array, `nan` where the value is empty (masked).

    Booleans become 0.0 or 1.0, so the label column can be converted with this and then cast back
    to `bool`. The `nan` values are filled later by the preprocessor's median imputation.
    """
    return np.asarray(np.ma.filled(column.astype(np.float64), np.nan))


def load_period(
    con: duckdb.DuckDBPyConnection,
    mart: str,
    column_types: dict[str, str],
    period: str,
    feature_names: Sequence[str] = tuple(FEATURES),
) -> PeriodArrays:
    """Load one period's rows of `mart`; only `period` is read, never another one.

    The query filters on the mart's own ``split`` column, so which rows belong to a period is
    decided where the mart is built, not here. `column_types` (from `_column_types`) decides which
    features are text and which numeric. `feature_names` selects a subset of `FEATURES` (in
    `FEATURES`' own order), for an ablation that trains without some of them; it defaults to every
    feature. Returns the period's arrays with its row and fraud-positive counts.

    Raises
    ------
    duckdb.Error
        When `mart` does not have every column of `feature_names`, the label or `split`.
    """
    names = [*feature_names, "is_fraud"]
    query = _PERIOD_QUERY.format(columns=", ".join(f'"{name}"' for name in names))
    found = con.execute(query, [mart, period]).fetchnumpy()
    categorical_names = [name for name in feature_names if column_types[name] == "VARCHAR"]
    numeric_names = [name for name in feature_names if column_types[name] != "VARCHAR"]
    rows = len(found["is_fraud"])
    categorical = (
        np.stack([_as_categorical(found[name]) for name in categorical_names], axis=1)
        if categorical_names
        else np.empty((rows, 0), dtype=object)
    )
    numeric = (
        np.stack([_as_numeric(found[name]) for name in numeric_names], axis=1)
        if numeric_names
        else np.empty((rows, 0), dtype=np.float64)
    )
    label = _as_numeric(found["is_fraud"]).astype(bool)
    return PeriodArrays(categorical, numeric, label, rows, int(label.sum()))


def _preprocessor(n_categorical: int, n_numeric: int) -> ColumnTransformer:
    """The shared preprocessing step: one-hot text columns, median-impute numeric ones.

    Columns are addressed by position: the first `n_categorical` are text, the next `n_numeric`
    numeric, matching the `np.hstack([categorical, numeric])` layout every caller builds. Text
    categories unseen at fit time encode as all zeros instead of raising. The returned transformer
    is unfitted; callers fit it on training rows only.
    """
    categorical_columns = list(range(n_categorical))
    numeric_columns = list(range(n_categorical, n_categorical + n_numeric))
    return ColumnTransformer(
        [
            ("categorical", OneHotEncoder(handle_unknown="ignore"), categorical_columns),
            ("numeric", SimpleImputer(strategy="median"), numeric_columns),
        ]
    )


def _fit_models(
    train: PeriodArrays, validation: PeriodArrays, *, seed: int
) -> tuple[ModelResult, ...]:
    """Fit both models on `train`'s identical features and score them on `validation`.

    The logistic baseline and the capped boosted model are both class-balanced and seeded with
    `seed`; the preprocessor is fitted on `train` alone. Returns one `ModelResult` per model
    (logistic, then boosted) holding the validation PR-AUC. Nothing is written.
    """
    preprocessor = _preprocessor(train.categorical.shape[1], train.numeric.shape[1])
    x_train = preprocessor.fit_transform(np.hstack([train.categorical, train.numeric]))
    x_validation = preprocessor.transform(np.hstack([validation.categorical, validation.numeric]))

    logistic_params: dict[str, object] = {
        "max_iter": 1000,
        "class_weight": "balanced",
        "random_state": seed,
    }
    logistic = LogisticRegression(**logistic_params)
    logistic.fit(x_train, train.label)
    logistic_scores = logistic.predict_proba(x_validation)[:, 1]

    boosted_params: dict[str, object] = {
        "max_iter": BOOST_MAX_ITER,
        "max_depth": BOOST_MAX_DEPTH,
        "class_weight": "balanced",
        "random_state": seed,
    }
    boosted = HistGradientBoostingClassifier(**boosted_params)
    boosted.fit(x_train, train.label)
    boosted_scores = boosted.predict_proba(x_validation)[:, 1]

    return (
        ModelResult(
            "logistic",
            logistic_params,
            float(average_precision_score(validation.label, logistic_scores)),
        ),
        ModelResult(
            "boosted",
            boosted_params,
            float(average_precision_score(validation.label, boosted_scores)),
        ),
    )


def run_probe(
    mart: Path,
    manifest: Path,
    *,
    split: SplitConfig,
    seed: int,
    now: datetime,
) -> ProbeResult:
    """Run the probe on `mart` and return its result.

    Opens a short-lived DuckDB connection to load the train and validation periods, fits both
    models and scores them on validation. `manifest` is the mart's build manifest, read only for
    its code version and output digest, which are stamped on the result. `split` is recorded on
    the result and not used to select rows (the mart is already labelled by period). `now` is
    the run timestamp, supplied by the caller. Writes nothing; the caller appends the result to
    the log.

    Raises
    ------
    FileNotFoundError
        When `mart` or `manifest` does not exist.
    duckdb.Error
        When `mart` is missing a required column.
    """
    if not mart.is_file():
        raise FileNotFoundError(f"risk feature mart not found at {mart}; run make features first")
    if not manifest.is_file():
        raise FileNotFoundError(f"mart manifest not found at {manifest}; run make features first")
    mart_manifest = json.loads(manifest.read_text(encoding="utf-8"))

    location = str(mart)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, location)
        train = load_period(con, location, column_types, "train")
        validation = load_period(con, location, column_types, "validation")
    finally:
        con.close()

    models = _fit_models(train, validation, seed=seed)
    return ProbeResult(
        timestamp=now.isoformat(),
        code_version=git_version(),
        mart_code_version=mart_manifest["code_version"],
        mart_output_sha256=mart_manifest["output_sha256"],
        split=split,
        seed=seed,
        rows={"train": train.rows, "validation": validation.rows},
        positives={"train": train.positives, "validation": validation.positives},
        validation_base_rate=validation.positives / validation.rows if validation.rows else 0.0,
        models=models,
    )


class ExperimentResult(Protocol):
    """What any run's result needs in order to be appended to an experiment log.

    A single method, `as_dict`, returning JSON-serialisable data for one log line.
    """

    def as_dict(self) -> dict[str, object]: ...


def append_experiment(result: ExperimentResult, log_path: Path) -> None:
    """Append `result` as one JSON line of `log_path`; earlier lines are never touched.

    Creates the log's parent directory when missing. Keys are sorted so a line is stable for a
    given result.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the probe and append its result to the experiment log; return the exit code.

    Options select the mart, its manifest, the split file, the log and the seed. Returns `0` on
    success and `1` when the split file is invalid, the mart or manifest is missing, or DuckDB
    cannot read the mart; the failure is logged by exception type only.
    """
    parser = argparse.ArgumentParser(description="Run the risk signal probe.")
    parser.add_argument("--mart", type=Path, default=DEFAULT_MART)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        split = load_split(args.split)
        result = run_probe(
            args.mart, args.manifest, split=split, seed=args.seed, now=datetime.now(UTC)
        )
    except (FileNotFoundError, ValueError, duckdb.Error) as error:
        logger.error("risk_probe_failed reason=%s", type(error).__name__)
        return 1
    append_experiment(result, args.log)
    logger.info(
        "risk_probe_written validation_pr_auc=%s log=%s",
        {m.name: round(m.validation_pr_auc, 5) for m in result.models},
        args.log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
