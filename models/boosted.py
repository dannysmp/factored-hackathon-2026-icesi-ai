"""
Boosted Risk Model and Ablation
================================

Overview
--------
The full comparison of an uncapped gradient-boosted classifier against the logistic baseline of
`models.probe`, and the with/without ablation of the two latest-snapshot features
(`customer_country`, `country_mismatch`) described in `models/README.md`. The comparison that
selects between the two models is a customer-resampled paired bootstrap on the test period, read
exactly once. The selection is appended to the experiment log, where `models.calibration` reads it.

Scope
-----
In: an uncapped boosted fit (unlike the signal probe's own, capped fit), the ablation report,
the test-period bootstrap and the resulting selection, appended to the experiment log.
Out: the precision floor, the threshold and the model card. Those belong to `models.calibration`:
this module only says which model, boosted or logistic, the threshold is chosen on.

Design Principles
-----------------
- **The boosting engine stays scikit-learn's own** (`HistGradientBoostingClassifier`, uncapped
  this time). The signal tabulation in `pipelines.risk_signal` found every feature's fraud
  prevalence flat, and the capped boosted fit of `models.probe` already landed within noise of the
  logistic baseline and the base rate, so there is no evidence that a different engine (for
  example one needing a native library) would change the qualitative result. Adding a native
  dependency for a benefit with no evidence behind it is not warranted; scikit-learn's own
  implementation stays in the existing `ml` dependency group.
- **The same preprocessing rule as the signal probe:** one `ColumnTransformer` per feature set,
  fitted on training rows only, shared by both models.
- **The ablation is diagnostic, not a gate.** Both feature sets are reported on validation; only
  the full feature set goes on to the test-period bootstrap and the selection.
- **The test period is read exactly once, for scoring only.** Every model is fitted on the training
  period; the test period's rows are scored a single time. Only the *resampling* is repeated: the
  bootstrap draws customers, not rows, with replacement, and recomputes each draw's metric from the
  one scoring pass, never refitting or rescoring a model.
- **The customer identifier is for grouping only.** It is obtained by joining the mart's
  transaction identifier to the cleaned transactions table; it is never a model feature and never
  leaves `bootstrap_test`, so it cannot appear in the experiment log.
- **Deterministic and runtime-capped.** A fixed seed and a fixed resample count
  (`BOOTSTRAP_RESAMPLES`) make the bootstrap reproducible and bound its running time regardless of
  the mart's size; it is a command, not a test, run on demand rather than on every change.

Runtime Contract
----------------
``run_ablation(con, mart, column_types, *, seed) -> tuple[AblationTable, ...]``
``bootstrap_test(con, mart, silver_dir, column_types, *, seed, resamples) -> BootstrapResult``
``run_boosted(mart, manifest, silver_dir, *, split, seed, now) -> BoostedResult``

Limitations
-----------
The bootstrap resamples customers, not the transactions within a customer, so a customer with many
transactions weighs the interval by its transaction count. The ablation compares validation PR-AUC
only; it does not itself decide whether the two snapshot features are kept, and it does not set
the threshold (`models.calibration` does).
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import json  # Manifest and experiment-log serialisation
import logging  # Progress events
from collections.abc import Sequence  # Argument type of main
from dataclasses import asdict, dataclass  # Immutable result objects, serialised by field
from datetime import UTC, datetime  # The run timestamp, supplied by the caller
from pathlib import Path  # Locations

# Third-party libraries
import duckdb  # Reading the mart and the cleaned transactions table
import numpy as np  # Feature matrices and the bootstrap resampling
from sklearn.ensemble import HistGradientBoostingClassifier  # The uncapped boosted model
from sklearn.linear_model import LogisticRegression  # The baseline model
from sklearn.metrics import average_precision_score  # Area under the precision-recall curve

# Local modules
from models.probe import (
    DEFAULT_LOG,
    DEFAULT_MANIFEST,
    DEFAULT_MART,
    SEED,
    ModelResult,
    PeriodArrays,
    _as_categorical,
    _as_numeric,
    _column_types,
    _preprocessor,
    append_experiment,
    load_period,
)
from pipelines.risk_features import DEFAULT_SPLIT, FEATURES, SplitConfig, load_split
from pipelines.silver import git_version  # Same code-version rule as the other pipelines

logger = logging.getLogger(__name__)

DEFAULT_SILVER = Path("data/silver")
# Features taken from the customer's latest recorded state rather than as of the transaction, so
# they are the ones the ablation drops to see whether they carry the result.
SNAPSHOT_FEATURES = ("customer_country", "country_mismatch")
REDUCED_FEATURES = tuple(name for name in FEATURES if name not in SNAPSHOT_FEATURES)

# Fixed for every run, so the interval is reproducible and bounded in running time regardless of
# the mart's size.
BOOTSTRAP_RESAMPLES = 300

# The test period's features and label with the customer identifier, in one query so the three
# line up row for row. The inner join drops any mart row without a cleaned transaction.
_TEST_WITH_CUSTOMER_QUERY = """
SELECT {columns}
FROM read_parquet(?) AS m
JOIN read_parquet(?) AS t ON m."transaction_id" = t."transaction_id"
WHERE m."split" = 'test'
"""


@dataclass(frozen=True, slots=True)
class AblationTable:
    """One feature set's validation PR-AUC for both models.

    `feature_set` names the set (`full` or `without_snapshot`), `features` lists its columns and
    `models` holds one `ModelResult` per model fitted on it.
    """

    feature_set: str
    features: tuple[str, ...]
    models: tuple[ModelResult, ...]


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    """A metric's point estimate with the 95% percentile interval from the customer bootstrap.

    `point` is computed on the full test period; `lower` and `upper` are the 2.5th and 97.5th
    percentiles of the resampled values.
    """

    point: float
    lower: float
    upper: float


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """The test-period comparison and the model it selects.

    Holds the PR-AUC interval of each model and of their difference (boosted minus logistic), the
    number of resamples, the seed and the number of distinct customers resampled. `selected` is
    `boosted` or `logistic` and `rationale` states the rule outcome in words.
    """

    resamples: int
    seed: int
    customers: int
    logistic: BootstrapInterval
    boosted: BootstrapInterval
    difference: BootstrapInterval
    selected: str
    rationale: str


@dataclass(frozen=True, slots=True)
class BoostedResult:
    """The full outcome of one boosted-model run: the ablation and the bootstrap selection.

    Carries the same provenance fields as `ProbeResult` (code version, the mart's version and
    output digest, split and seed) alongside the ablation tables and the bootstrap result.
    """

    timestamp: str
    code_version: str
    mart_code_version: str
    mart_output_sha256: str
    split: SplitConfig
    seed: int
    ablation: tuple[AblationTable, ...]
    bootstrap: BootstrapResult

    def as_dict(self) -> dict[str, object]:
        """The result as JSON-serialisable data, one line of the experiment log.

        The `bootstrap` entry is what `models.calibration.latest_selected_model` looks for when it
        reads the log back. Split dates are rendered as ISO strings.
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
            "ablation": [
                {
                    "feature_set": table.feature_set,
                    "features": list(table.features),
                    "models": [
                        {
                            "name": m.name,
                            "params": m.params,
                            "validation_pr_auc": m.validation_pr_auc,
                        }
                        for m in table.models
                    ],
                }
                for table in self.ablation
            ],
            "bootstrap": {
                "resamples": self.bootstrap.resamples,
                "seed": self.bootstrap.seed,
                "customers": self.bootstrap.customers,
                "logistic": asdict(self.bootstrap.logistic),
                "boosted": asdict(self.bootstrap.boosted),
                "difference": asdict(self.bootstrap.difference),
                "selected": self.bootstrap.selected,
                "rationale": self.bootstrap.rationale,
            },
        }


def _fit_and_score(
    train: PeriodArrays, validation: PeriodArrays, *, seed: int
) -> tuple[ModelResult, ...]:
    """Fit the logistic baseline and the uncapped boosted model on `train`, score on `validation`.

    Identical to `models.probe`'s own `_fit_models`, except the boosted model is not capped. The
    preprocessor is fitted on `train` only. Returns the logistic then the boosted `ModelResult`,
    each with its validation PR-AUC; nothing is written.
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

    boosted_params: dict[str, object] = {"class_weight": "balanced", "random_state": seed}
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


def run_ablation(
    con: duckdb.DuckDBPyConnection, mart: str, column_types: dict[str, str], *, seed: int
) -> tuple[AblationTable, ...]:
    """Score both models on validation, with and without the two snapshot features.

    Returns two `AblationTable`s: `full` (every feature in `FEATURES`) and `without_snapshot`
    (`REDUCED_FEATURES`). Each set is loaded, preprocessed and fitted independently on the
    training period and scored on validation; the test period is not read.

    Raises
    ------
    duckdb.Error
        When `mart` lacks a required column.
    """
    tables = []
    feature_sets = (("full", tuple(FEATURES)), ("without_snapshot", REDUCED_FEATURES))
    for feature_set, feature_names in feature_sets:
        train = load_period(con, mart, column_types, "train", feature_names)
        validation = load_period(con, mart, column_types, "validation", feature_names)
        models = _fit_and_score(train, validation, seed=seed)
        tables.append(AblationTable(feature_set, feature_names, models))
    return tuple(tables)


def _percentile_interval(values: np.ndarray) -> tuple[float, float]:
    """The 2.5th and 97.5th percentiles of `values`, the bounds of a 95% percentile interval."""
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))


def bootstrap_test(
    con: duckdb.DuckDBPyConnection,
    mart: str,
    silver_dir: Path,
    column_types: dict[str, str],
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> BootstrapResult:
    """Fit both models on train, score the test period once, and bootstrap the comparison.

    The customer identifier is read only to group the resampling; it is never a feature and is
    discarded once this function returns. Each of `resamples` draws picks as many customers as
    there are in the test period, with replacement, and recomputes both models' PR-AUC and their
    difference from the single scoring pass (rows are weighted by how often their customer was
    drawn). The selection rule: `boosted` when the lower bound of the difference interval is above
    zero, otherwise `logistic`, kept for parsimony. The point estimates use the whole test period.

    Raises
    ------
    duckdb.Error
        When the cleaned transactions table cannot be read.
    """
    train = load_period(con, mart, column_types, "train")

    # One query for the test period's features, label and customer identifier together, so their
    # row order can never drift apart the way two separate queries could.
    names = [*FEATURES, "is_fraud"]
    query = _TEST_WITH_CUSTOMER_QUERY.format(
        columns=", ".join(f'm."{name}"' for name in names) + ', t."customer_id" AS customer_id'
    )
    transactions = str(silver_dir / "silver" / "transactions.parquet")
    found = con.execute(query, [mart, transactions]).fetchnumpy()
    categorical_names = [name for name in FEATURES if column_types[name] == "VARCHAR"]
    numeric_names = [name for name in FEATURES if column_types[name] != "VARCHAR"]
    rows = len(found["is_fraud"])
    test_categorical = (
        np.stack([_as_categorical(found[name]) for name in categorical_names], axis=1)
        if categorical_names
        else np.empty((rows, 0), dtype=object)
    )
    test_numeric = (
        np.stack([_as_numeric(found[name]) for name in numeric_names], axis=1)
        if numeric_names
        else np.empty((rows, 0), dtype=np.float64)
    )
    test_label = _as_numeric(found["is_fraud"]).astype(bool)
    test = PeriodArrays(test_categorical, test_numeric, test_label, rows, int(test_label.sum()))
    customer_ids = _as_categorical(found["customer_id"])

    preprocessor = _preprocessor(train.categorical.shape[1], train.numeric.shape[1])
    x_train = preprocessor.fit_transform(np.hstack([train.categorical, train.numeric]))
    x_test = preprocessor.transform(np.hstack([test.categorical, test.numeric]))

    logistic = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=seed)
    logistic.fit(x_train, train.label)
    logistic_scores = logistic.predict_proba(x_test)[:, 1]

    boosted = HistGradientBoostingClassifier(class_weight="balanced", random_state=seed)
    boosted.fit(x_train, train.label)
    boosted_scores = boosted.predict_proba(x_test)[:, 1]

    label = test.label
    unique_customers, row_of = np.unique(customer_ids, return_inverse=True)
    n_customers = len(unique_customers)

    # A customer drawn twice must count its rows twice, and one drawn zero times must count for
    # nothing; that is exactly what a per-row sample weight expresses, so each resample becomes one
    # weight vector (how many times each row's customer was drawn) instead of a physically
    # duplicated, re-sorted row set. This keeps a resample at O(rows), not O(rows per draw).
    rng = np.random.default_rng(seed)
    logistic_draws = np.empty(resamples)
    boosted_draws = np.empty(resamples)
    difference_draws = np.empty(resamples)
    for i in range(resamples):
        drawn = rng.integers(0, n_customers, size=n_customers)
        weights = np.bincount(drawn, minlength=n_customers)[row_of]
        logistic_pr_auc = average_precision_score(label, logistic_scores, sample_weight=weights)
        boosted_pr_auc = average_precision_score(label, boosted_scores, sample_weight=weights)
        logistic_draws[i] = logistic_pr_auc
        boosted_draws[i] = boosted_pr_auc
        difference_draws[i] = boosted_pr_auc - logistic_pr_auc

    logistic_point = float(average_precision_score(label, logistic_scores))
    boosted_point = float(average_precision_score(label, boosted_scores))
    logistic_interval = BootstrapInterval(logistic_point, *_percentile_interval(logistic_draws))
    boosted_interval = BootstrapInterval(boosted_point, *_percentile_interval(boosted_draws))
    difference_lower, difference_upper = _percentile_interval(difference_draws)
    difference_interval = BootstrapInterval(
        boosted_point - logistic_point, difference_lower, difference_upper
    )

    if difference_lower > 0:
        selected, rationale = "boosted", "the interval of the PR-AUC difference excludes zero"
    else:
        selected = "logistic"
        rationale = (
            "the interval of the PR-AUC difference includes zero; "
            "the logistic model is kept for parsimony"
        )
    return BootstrapResult(
        resamples=resamples,
        seed=seed,
        customers=len(unique_customers),
        logistic=logistic_interval,
        boosted=boosted_interval,
        difference=difference_interval,
        selected=selected,
        rationale=rationale,
    )


def run_boosted(
    mart: Path,
    manifest: Path,
    silver_dir: Path,
    *,
    split: SplitConfig,
    seed: int,
    now: datetime,
) -> BoostedResult:
    """Run the ablation and the test-period bootstrap on `mart`; return the combined result.

    `manifest` is the mart's build manifest, read for its code version and output digest, which
    are stamped on the result. `silver_dir` is the root of the cleaned layer, used to resolve the
    customer identifier. `split` is recorded on the result and does not select rows (the mart is
    already labelled by period). `now` is the run timestamp, supplied by the caller. Writes
    nothing; the caller appends the result to the log.

    Raises
    ------
    FileNotFoundError
        When `mart` or `manifest` does not exist.
    duckdb.Error
        When a required column is missing, or the cleaned transactions table cannot be read.
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
        ablation = run_ablation(con, location, column_types, seed=seed)
        bootstrap = bootstrap_test(con, location, silver_dir, column_types, seed=seed)
    finally:
        con.close()

    return BoostedResult(
        timestamp=now.isoformat(),
        code_version=git_version(),
        mart_code_version=mart_manifest["code_version"],
        mart_output_sha256=mart_manifest["output_sha256"],
        split=split,
        seed=seed,
        ablation=ablation,
        bootstrap=bootstrap,
    )


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the boosted model, the ablation and the bootstrap; append to the experiment log.

    Options select the mart, its manifest, the cleaned layer, the split file, the log and the
    seed. Returns `0` on success and `1` when the split file is invalid, an input is missing or
    DuckDB cannot read it; the failure is logged by exception type only.
    """
    parser = argparse.ArgumentParser(description="Run the boosted model, ablation and bootstrap.")
    parser.add_argument("--mart", type=Path, default=DEFAULT_MART)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--silver", type=Path, default=DEFAULT_SILVER)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        split = load_split(args.split)
        result = run_boosted(
            args.mart,
            args.manifest,
            args.silver,
            split=split,
            seed=args.seed,
            now=datetime.now(UTC),
        )
    except (FileNotFoundError, ValueError, duckdb.Error) as error:
        logger.error("boosted_model_failed reason=%s", type(error).__name__)
        return 1
    append_experiment(result, args.log)
    logger.info(
        "boosted_model_written selected=%s difference=%s log=%s",
        result.bootstrap.selected,
        round(result.bootstrap.difference.point, 6),
        args.log,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
