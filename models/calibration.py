"""
Risk Score Calibration, Threshold and Model Card
=================================================

Overview
--------
The last modelling step: fits the model that `models.boosted` selected (read from the experiment
log, never re-decided here), reports its calibration on validation and on test, chooses a
threshold on validation against a fixed precision floor and routed-share cap, scores the test
period once with it, and switches risk routing on only if the test result clears the same floor
with its bootstrap interval's lower bound above the test prevalence. Whatever the result, it is
written to a machine-readable model card (`models/model_card.json`) and appended to the experiment
log.

Scope
-----
In: reading the selected model's name from the experiment log, fitting it once on train, scoring
validation and test, the threshold search, the customer-resampled bootstrap of the precision at
the chosen threshold, calibration diagnostics (Brier score, expected calibration error, a binned
curve) and the model card. Out: model selection between logistic and boosted (`models.boosted`
already decided that); wiring a live score into a request (the score is computed in batch and
never carries a model into the running service).

Design Principles
-----------------
- **The model is never re-selected here.** `latest_selected_model` reads the last "bootstrap"
  entry `models.boosted` appended and takes its `selected` field; this module has no rule of its
  own for choosing between logistic and boosted.
- **The threshold rule is fixed in advance and applied unchanged.** The lowest validation score
  whose precision is at least `PRECISION_FLOOR` and whose routed share is at most
  `ROUTED_SHARE_CAP`; if none qualifies, there is no threshold and routing stays off (no test
  scoring follows from that outcome, since there is no threshold to apply). `precision_recall_curve`
  gives precision and recall at every distinct score; the routed share at each one follows from
  `recall * positives / (precision * total)`, so no second pass over the scores is needed.
- **The test period is scored exactly once.** That one score array feeds both the calibration
  diagnostics for the test period and, when a threshold was chosen, the routing decision; it is
  never rescored for a second purpose.
- **The bootstrap resamples customers, not rows, over the test period only,** with the resample
  count (`CALIBRATION_BOOTSTRAP_RESAMPLES`) and seed fixed in advance rather than tuned here.
- **Scores are not calibrated probabilities.** Both models are fitted with `class_weight="balanced"`
  (the same rule as `models.probe` and `models.boosted`), which reweights the fit for the severe
  imbalance and inflates predicted probabilities relative to the true prevalence. The precision,
  recall and threshold procedures stay valid regardless, because they only depend on the scores'
  ranking, not their absolute value; the calibration diagnostics are reported for transparency,
  not as a claim that the score is a calibrated probability.
- **A negative result is written, not hidden.** If no threshold clears the floor, or the test
  result does not, `routing_enabled` is `False` and `rationale` says which condition failed; the
  card is still complete and still machine-readable.

Runtime Contract
----------------
``latest_selected_model(log_path) -> str``
``choose_threshold(label, scores, *, floor, cap) -> ThresholdChoice | None``
``run_calibration(mart, manifest, silver_dir, *, split, model_log, seed, now, floor, cap,
resamples) -> CalibrationResult``
``write_model_card(result, card_path) -> None``

Limitations
-----------
The routed-share and recall guardrail gap between countries, segments and currencies is not
computed here: with no threshold accepted nothing
is ever routed, so there is no group gap to report; enabling routing would first require adding
that breakdown. The customer identifier used for resampling is read only from the cleaned
transactions table and is discarded once the bootstrap returns, the same rule `models.boosted`
follows.
"""

from __future__ import annotations

# Standard libraries
import argparse  # Command line
import json  # Manifest, experiment-log and model-card serialisation
import logging  # Progress events
from collections.abc import Sequence  # Argument type of main
from dataclasses import asdict, dataclass  # Immutable result objects, serialised by field
from datetime import UTC, datetime  # The run timestamp, supplied by the caller
from pathlib import Path  # Locations
from typing import TypeAlias  # Names the fitted-model union for the type checker

# Third-party libraries
import duckdb  # Reading the mart and the cleaned transactions table
import numpy as np  # Feature matrices and the bootstrap resampling
from sklearn.compose import ColumnTransformer  # Shared preprocessing, typed for the fitted models
from sklearn.ensemble import HistGradientBoostingClassifier  # The boosted model
from sklearn.linear_model import LogisticRegression  # The baseline model
from sklearn.metrics import brier_score_loss, precision_recall_curve  # Calibration and threshold

# Local modules
from models.boosted import BootstrapInterval, _percentile_interval
from models.probe import (
    DEFAULT_LOG,
    DEFAULT_MANIFEST,
    DEFAULT_MART,
    SEED,
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
DEFAULT_CARD = Path("models/model_card.json")

# The decision rule, fixed in advance: a precision floor of ten times the validation base rate,
# with at most one in twenty otherwise-eligible transactions routed.
PRECISION_FLOOR = 0.01
ROUTED_SHARE_CAP = 0.05
# Fixed in advance, not tuned by this module.
CALIBRATION_BOOTSTRAP_RESAMPLES = 2000
CALIBRATION_BINS = 10

# The test period's features and label with the customer identifier, in one query so the three
# line up row for row. The inner join drops any mart row without a cleaned transaction.
_TEST_WITH_CUSTOMER_QUERY = """
SELECT {columns}
FROM read_parquet(?) AS m
JOIN read_parquet(?) AS t ON m."transaction_id" = t."transaction_id"
WHERE m."split" = 'test'
"""


@dataclass(frozen=True, slots=True)
class CalibrationCurvePoint:
    """One bin of the calibration curve: how many scores fell in it and what was observed.

    `bin_lower` and `bin_upper` are the bin's score range, `count` the scores in it,
    `mean_predicted` their mean score and `observed_rate` the fraction that were fraud.
    """

    bin_lower: float
    bin_upper: float
    count: int
    mean_predicted: float
    observed_rate: float


@dataclass(frozen=True, slots=True)
class CalibrationDiagnostics:
    """Brier score, expected calibration error and the binned curve for one period.

    `period` is `validation` or `test`. Empty bins are omitted from `curve`.
    """

    period: str
    brier_score: float
    expected_calibration_error: float
    curve: tuple[CalibrationCurvePoint, ...]


@dataclass(frozen=True, slots=True)
class ThresholdChoice:
    """The lowest validation threshold meeting the floor and the routed-share cap.

    Also holds the validation precision, recall and routed share (fraction of all validation rows
    scored at or above the threshold) at that threshold.
    """

    threshold: float
    validation_precision: float
    validation_recall: float
    validation_routed_share: float


@dataclass(frozen=True, slots=True)
class TestScoring:
    """The one-time test-period result of applying the chosen threshold.

    Precision, recall and routed share at the threshold, the number of fraud-positive test rows,
    and the customer-bootstrap interval of the precision.
    """

    precision: float
    recall: float
    routed_share: float
    positives: int
    precision_interval: BootstrapInterval


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """The full outcome of the calibration run: the model card, in one object.

    Provenance (code and mart versions, split, seed), the selected model, the floor and cap used,
    the calibration diagnostics per period, the threshold and test scoring (both `None` when no
    validation threshold qualified), whether routing is enabled and why, and the card's fixed
    prose: data provenance, intended use, leakage review and limitations.
    """

    timestamp: str
    code_version: str
    mart_code_version: str
    mart_output_sha256: str
    split: SplitConfig
    seed: int
    selected_model: str
    precision_floor: float
    routed_share_cap: float
    calibration: tuple[CalibrationDiagnostics, ...]
    threshold: ThresholdChoice | None
    test_scoring: TestScoring | None
    routing_enabled: bool
    rationale: str
    data_provenance: str
    intended_use: str
    leakage_review: str
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """The result as JSON-serialisable data: both the experiment-log line and the model card.

        Split dates are rendered as ISO strings and the tuple-valued fields as lists; `threshold`
        and `test_scoring` are `None` when absent.
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
            "selected_model": self.selected_model,
            "precision_floor": self.precision_floor,
            "routed_share_cap": self.routed_share_cap,
            "calibration": [
                {
                    "period": c.period,
                    "brier_score": c.brier_score,
                    "expected_calibration_error": c.expected_calibration_error,
                    "curve": [asdict(point) for point in c.curve],
                }
                for c in self.calibration
            ],
            "threshold": asdict(self.threshold) if self.threshold is not None else None,
            "test_scoring": (
                {
                    "precision": self.test_scoring.precision,
                    "recall": self.test_scoring.recall,
                    "routed_share": self.test_scoring.routed_share,
                    "positives": self.test_scoring.positives,
                    "precision_interval": asdict(self.test_scoring.precision_interval),
                }
                if self.test_scoring is not None
                else None
            ),
            "routing_enabled": self.routing_enabled,
            "rationale": self.rationale,
            "data_provenance": self.data_provenance,
            "intended_use": self.intended_use,
            "leakage_review": self.leakage_review,
            "limitations": list(self.limitations),
        }


def latest_selected_model(log_path: Path) -> str:
    """The model `models.boosted`'s bootstrap last selected, from the experiment log.

    Scans the log from the newest line back for an entry with a `bootstrap` object holding a
    `selected` field and returns that field; entries of other run types are skipped.

    Raises
    ------
    ValueError
        When the log has no line recording a bootstrap selection.
    """
    if log_path.is_file():
        for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            entry = json.loads(line)
            bootstrap = entry.get("bootstrap")
            if isinstance(bootstrap, dict) and "selected" in bootstrap:
                return str(bootstrap["selected"])
    raise ValueError(f"{log_path} has no bootstrap selection; run `python -m models.boosted` first")


_FittedModel: TypeAlias = LogisticRegression | HistGradientBoostingClassifier


def _fit_selected(
    model_name: str, train: PeriodArrays, *, seed: int
) -> tuple[_FittedModel, ColumnTransformer]:
    """Fit `model_name` on `train`'s identical features; return it with its fitted preprocessor.

    `model_name` is `logistic` or `boosted`, built with the same parameters as in `models.boosted`
    (class-balanced, seeded with `seed`). The preprocessor is fitted on `train` only.

    Raises
    ------
    ValueError
        When `model_name` is neither `logistic` nor `boosted`.
    """
    preprocessor = _preprocessor(train.categorical.shape[1], train.numeric.shape[1])
    x_train = preprocessor.fit_transform(np.hstack([train.categorical, train.numeric]))
    model: _FittedModel
    if model_name == "logistic":
        model = LogisticRegression(max_iter=1000, class_weight="balanced", random_state=seed)
    elif model_name == "boosted":
        model = HistGradientBoostingClassifier(class_weight="balanced", random_state=seed)
    else:
        raise ValueError(f"unknown model {model_name!r}; expected 'logistic' or 'boosted'")
    model.fit(x_train, train.label)
    return model, preprocessor


def _score(
    model: _FittedModel, preprocessor: ColumnTransformer, period: PeriodArrays
) -> np.ndarray:
    """The fitted `model`'s positive-class score for every row of `period`.

    Applies the already-fitted `preprocessor` (transform only, no refitting) and returns the
    predicted probability of the fraud class; being class-balanced, it ranks rows but is not a
    calibrated probability.
    """
    x = preprocessor.transform(np.hstack([period.categorical, period.numeric]))
    return np.asarray(model.predict_proba(x)[:, 1])


def _calibration_diagnostics(
    period: str, label: np.ndarray, scores: np.ndarray, *, bins: int = CALIBRATION_BINS
) -> CalibrationDiagnostics:
    """Brier score, expected calibration error and the binned curve of `scores` against `label`.

    Scores are placed in `bins` equal-width bins over [0, 1] (the last bin includes 1.0). The
    expected calibration error is the sum over non-empty bins of the bin's share of rows times the
    absolute gap between its observed fraud rate and its mean score.
    """
    brier = float(brier_score_loss(label, scores))
    edges = np.linspace(0.0, 1.0, bins + 1)
    bin_index = np.clip(np.digitize(scores, edges[1:-1], right=False), 0, bins - 1)
    total = len(label)
    points: list[CalibrationCurvePoint] = []
    error = 0.0
    for b in range(bins):
        mask = bin_index == b
        count = int(mask.sum())
        if count == 0:
            continue
        mean_predicted = float(scores[mask].mean())
        observed_rate = float(label[mask].mean())
        points.append(
            CalibrationCurvePoint(
                float(edges[b]), float(edges[b + 1]), count, mean_predicted, observed_rate
            )
        )
        error += (count / total) * abs(observed_rate - mean_predicted)
    return CalibrationDiagnostics(period, brier, float(error), tuple(points))


def choose_threshold(
    label: np.ndarray, scores: np.ndarray, *, floor: float, cap: float
) -> ThresholdChoice | None:
    """The lowest validation threshold whose precision is at least `floor` and share at most `cap`.

    Considers every distinct score as a threshold (rows scored at or above it are routed) and
    returns the lowest one meeting both conditions. `None` when no threshold meets both, or when
    `label` is empty or has no positives: routing then stays off and no test period is scored.
    """
    total = len(label)
    positives = int(label.sum()) if total else 0
    if positives == 0 or total == 0:
        return None
    precision, recall, thresholds = precision_recall_curve(label, scores)
    candidates: list[tuple[float, float, float, float]] = []
    for t, p, r in zip(thresholds, precision[:-1], recall[:-1], strict=True):
        if p <= 0:
            continue
        routed_share = (r * positives) / (p * total)
        if p >= floor and routed_share <= cap:
            candidates.append((float(t), float(p), float(r), float(routed_share)))
    if not candidates:
        return None
    threshold, chosen_precision, chosen_recall, chosen_share = min(candidates, key=lambda c: c[0])
    return ThresholdChoice(threshold, chosen_precision, chosen_recall, chosen_share)


def _score_at_threshold(
    label: np.ndarray, scores: np.ndarray, threshold: float
) -> tuple[float, float, float, int]:
    """Precision, recall, routed share and the positive count of `scores` at `threshold`, once.

    A row is routed when its score is at least `threshold`. Precision, recall and routed share are
    `0.0` rather than an error when their denominator is zero.
    """
    predicted = scores >= threshold
    positives = int(label.sum())
    true_positive = int(np.sum(predicted & label))
    false_positive = int(np.sum(predicted & ~label))
    routed = true_positive + false_positive
    precision = true_positive / routed if routed else 0.0
    recall = true_positive / positives if positives else 0.0
    routed_share = routed / len(label) if len(label) else 0.0
    return precision, recall, routed_share, positives


def _bootstrap_precision_at_threshold(
    label: np.ndarray,
    scores: np.ndarray,
    customer_ids: np.ndarray,
    threshold: float,
    *,
    seed: int,
    resamples: int,
) -> BootstrapInterval:
    """The customer-resampled 95% interval of the precision at `threshold`, over the test period.

    Each of `resamples` draws picks as many customers as there are, with replacement; rows are
    weighted by how often their customer was drawn and the precision is recomputed from the fixed
    scores (a draw that routes nothing counts as `0.0`). The returned point estimate is the
    precision on the unresampled test period. Deterministic given `seed`.
    """
    predicted = scores >= threshold
    unique_customers, row_of = np.unique(customer_ids, return_inverse=True)
    n_customers = len(unique_customers)
    rng = np.random.default_rng(seed)
    draws = np.empty(resamples)
    for i in range(resamples):
        drawn = rng.integers(0, n_customers, size=n_customers)
        weights = np.bincount(drawn, minlength=n_customers)[row_of]
        true_positive = float(np.sum(weights[predicted & label]))
        false_positive = float(np.sum(weights[predicted & ~label]))
        routed = true_positive + false_positive
        draws[i] = true_positive / routed if routed else 0.0
    point, _, _, _ = _score_at_threshold(label, scores, threshold)
    lower, upper = _percentile_interval(draws)
    return BootstrapInterval(point, lower, upper)


def _decide_routing(
    test_precision: float,
    precision_interval: BootstrapInterval,
    test_prevalence: float,
    *,
    floor: float,
) -> tuple[bool, str]:
    """Whether the test-period result clears the two-condition routing rule, and why.

    Routing turns on only when the test-period precision meets `floor` and the bootstrap
    interval's lower bound is above `test_prevalence`; either failing keeps it off, each with its
    own rationale, so a rejection always names which of the two conditions did not hold.
    """
    if test_precision >= floor and precision_interval.lower > test_prevalence:
        return True, (
            f"the test-period precision at the chosen threshold ({test_precision:.4f}) meets the "
            f"floor and its interval's lower bound ({precision_interval.lower:.4f}) is above the "
            f"test prevalence ({test_prevalence:.4f})"
        )
    if test_precision < floor:
        return False, (
            f"the test-period precision at the chosen threshold ({test_precision:.4f}) falls "
            f"below the floor ({floor:.4f}); the validation result did not hold on test"
        )
    return False, (
        f"the interval's lower bound ({precision_interval.lower:.4f}) is not above the test "
        f"prevalence ({test_prevalence:.4f}); the precision at the threshold is not "
        "distinguishable from chance"
    )


def _load_test_with_customer(
    con: duckdb.DuckDBPyConnection, mart: str, silver_dir: Path, column_types: dict[str, str]
) -> tuple[PeriodArrays, np.ndarray]:
    """The test period's features, label and customer identifier, joined once, in one order.

    Reads the mart's test rows and joins the cleaned transactions table (under `silver_dir`) on
    the transaction identifier to obtain the customer identifier, returned separately from the
    feature arrays so it can only be used for grouping the bootstrap.

    Raises
    ------
    duckdb.Error
        When a required column is missing or the cleaned transactions table cannot be read.
    """
    names = [*FEATURES, "is_fraud"]
    query = _TEST_WITH_CUSTOMER_QUERY.format(
        columns=", ".join(f'm."{name}"' for name in names) + ', t."customer_id" AS customer_id'
    )
    transactions = str(silver_dir / "silver" / "transactions.parquet")
    found = con.execute(query, [mart, transactions]).fetchnumpy()
    categorical_names = [name for name in FEATURES if column_types[name] == "VARCHAR"]
    numeric_names = [name for name in FEATURES if column_types[name] != "VARCHAR"]
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
    customer_ids = _as_categorical(found["customer_id"])
    return PeriodArrays(categorical, numeric, label, rows, int(label.sum())), customer_ids


# Fixed prose of the model card: where the data came from, what the score may be used for, how
# leakage was ruled out and what the card does not cover.
_DATA_PROVENANCE = (
    "The transaction and fraud labels are synthetic, generated for this project; they are not "
    "drawn from any real bank, customer or regulator."
)
_INTENDED_USE = (
    "Routing only: the score can send an otherwise eligible dispute to a person for review. It "
    "never decides a dispute's outcome, and a missing or absent score is treated exactly like a "
    "routing rule that does not apply."
)
_LEAKAGE_REVIEW = (
    "Every feature uses only information available at or before the transaction; the source's own "
    "fraud score, authorisation code and status are excluded; the full review is in "
    "reports/risk-features.md."
)
_BASE_LIMITATIONS = (
    "Both models are fitted with class_weight='balanced' for the severe imbalance; their scores "
    "rank transactions but are not calibrated probabilities, so the Brier score and expected "
    "calibration error above describe the raw output, not a claim of calibration.",
    "The routed-share and recall gap between countries, segments and currencies is not computed "
    "in this card: with routing off nothing is routed, so there is no group gap to report.",
)


def run_calibration(
    mart: Path,
    manifest: Path,
    silver_dir: Path,
    *,
    split: SplitConfig,
    model_log: Path,
    seed: int,
    now: datetime,
    floor: float = PRECISION_FLOOR,
    cap: float = ROUTED_SHARE_CAP,
    resamples: int = CALIBRATION_BOOTSTRAP_RESAMPLES,
) -> CalibrationResult:
    """Run the calibration on `mart`; return the full model card as one result.

    Fits the model recorded in `model_log` on train, scores validation and test once, and
    searches validation for a threshold meeting `floor` and `cap`. With a threshold, the test
    period is scored at it and the routing decision is made from the test precision and its
    `resamples`-draw customer bootstrap; without one, routing is off and the rationale says so.
    `manifest` supplies the mart's code version and output digest, `silver_dir` the cleaned layer
    used to group the bootstrap by customer, and `now` the run timestamp. Writes nothing.

    Raises
    ------
    FileNotFoundError
        When `mart` or `manifest` does not exist.
    ValueError
        When `model_log` has no recorded model selection.
    duckdb.Error
        When a required column is missing, or the cleaned transactions table cannot be read.
    """
    if not mart.is_file():
        raise FileNotFoundError(f"risk feature mart not found at {mart}; run make features first")
    if not manifest.is_file():
        raise FileNotFoundError(f"mart manifest not found at {manifest}; run make features first")
    mart_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    selected_model = latest_selected_model(model_log)

    location = str(mart)
    con = duckdb.connect()
    try:
        column_types = _column_types(con, location)
        train = load_period(con, location, column_types, "train")
        validation = load_period(con, location, column_types, "validation")
        model, preprocessor = _fit_selected(selected_model, train, seed=seed)
        validation_scores = _score(model, preprocessor, validation)
        test, customer_ids = _load_test_with_customer(con, location, silver_dir, column_types)
        test_scores = _score(model, preprocessor, test)
    finally:
        con.close()

    calibration = (
        _calibration_diagnostics("validation", validation.label, validation_scores),
        _calibration_diagnostics("test", test.label, test_scores),
    )

    chosen = choose_threshold(validation.label, validation_scores, floor=floor, cap=cap)
    if chosen is None:
        return CalibrationResult(
            timestamp=now.isoformat(),
            code_version=git_version(),
            mart_code_version=mart_manifest["code_version"],
            mart_output_sha256=mart_manifest["output_sha256"],
            split=split,
            seed=seed,
            selected_model=selected_model,
            precision_floor=floor,
            routed_share_cap=cap,
            calibration=calibration,
            threshold=None,
            test_scoring=None,
            routing_enabled=False,
            rationale=(
                f"no validation threshold reaches a precision of {floor:.4f} while keeping the "
                f"routed share at or below {cap:.4f}; there is no usable signal at this floor"
            ),
            data_provenance=_DATA_PROVENANCE,
            intended_use=_INTENDED_USE,
            leakage_review=_LEAKAGE_REVIEW,
            limitations=_BASE_LIMITATIONS,
        )

    test_precision, test_recall, test_routed_share, test_positives = _score_at_threshold(
        test.label, test_scores, chosen.threshold
    )
    precision_interval = _bootstrap_precision_at_threshold(
        test.label, test_scores, customer_ids, chosen.threshold, seed=seed, resamples=resamples
    )
    test_scoring = TestScoring(
        test_precision, test_recall, test_routed_share, test_positives, precision_interval
    )

    test_prevalence = test.positives / test.rows if test.rows else 0.0
    routing_enabled, rationale = _decide_routing(
        test_precision, precision_interval, test_prevalence, floor=floor
    )

    return CalibrationResult(
        timestamp=now.isoformat(),
        code_version=git_version(),
        mart_code_version=mart_manifest["code_version"],
        mart_output_sha256=mart_manifest["output_sha256"],
        split=split,
        seed=seed,
        selected_model=selected_model,
        precision_floor=floor,
        routed_share_cap=cap,
        calibration=calibration,
        threshold=chosen,
        test_scoring=test_scoring,
        routing_enabled=routing_enabled,
        rationale=rationale,
        data_provenance=_DATA_PROVENANCE,
        intended_use=_INTENDED_USE,
        leakage_review=_LEAKAGE_REVIEW,
        limitations=_BASE_LIMITATIONS,
    )


def write_model_card(result: CalibrationResult, card_path: Path) -> None:
    """Write `result` as the current model card, replacing whatever `card_path` held before.

    Unlike the experiment log, the card holds only the current state. It is pretty-printed with
    sorted keys and a trailing newline so a rewrite with an unchanged result is byte-identical,
    and the parent directory is created when missing.
    """
    card_path.parent.mkdir(parents=True, exist_ok=True)
    card_path.write_text(
        json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# -----------------------------------------------------------------------------
# Command line
# -----------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the calibration; write the model card and append to the experiment log.

    Options select the mart, its manifest, the cleaned layer, the split file, the log (which is
    also where the selected model is read from), the card path, the seed, the precision floor, the
    routed-share cap and the resample count. Returns `0` on success and `1` when the split file is
    invalid, an input is missing, the log has no model selection or DuckDB cannot read an input;
    the failure is logged by exception type only.
    """
    parser = argparse.ArgumentParser(
        description="Choose the risk-score threshold, calibrate it and write the model card."
    )
    parser.add_argument("--mart", type=Path, default=DEFAULT_MART)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--silver", type=Path, default=DEFAULT_SILVER)
    parser.add_argument("--split", type=Path, default=DEFAULT_SPLIT)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--card", type=Path, default=DEFAULT_CARD)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--floor", type=float, default=PRECISION_FLOOR)
    parser.add_argument("--cap", type=float, default=ROUTED_SHARE_CAP)
    parser.add_argument("--resamples", type=int, default=CALIBRATION_BOOTSTRAP_RESAMPLES)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        split = load_split(args.split)
        result = run_calibration(
            args.mart,
            args.manifest,
            args.silver,
            split=split,
            model_log=args.log,
            seed=args.seed,
            now=datetime.now(UTC),
            floor=args.floor,
            cap=args.cap,
            resamples=args.resamples,
        )
    except (FileNotFoundError, ValueError, duckdb.Error) as error:
        logger.error("calibration_failed reason=%s", type(error).__name__)
        return 1
    append_experiment(result, args.log)
    write_model_card(result, args.card)
    logger.info(
        "calibration_written routing_enabled=%s threshold=%s card=%s",
        result.routing_enabled,
        result.threshold.threshold if result.threshold else None,
        args.card,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
