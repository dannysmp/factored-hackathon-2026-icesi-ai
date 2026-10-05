"""
Dispute Policy Loader Tests
===========================

Component: ``app.domain.policy.loader`` and the validation of ``Policy``. Hermetic: policy files
are written to a temporary directory; the shipped policy file is loaded as it is.
"""

from __future__ import annotations

# Standard libraries
from decimal import Decimal  # Exact money in the shipped policy
from pathlib import Path  # Temporary policy files

# Third-party libraries
import pytest  # Test runner and parametrisation
import yaml  # Build variants of the shipped policy

# Local modules
from app.domain.policy import DEFAULT_POLICY_PATH, DisputeCategory, PolicyError, load_policy


def _shipped() -> dict[str, object]:
    """The shipped policy as plain data, ready to be broken in one place."""
    loaded = yaml.safe_load(DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write(tmp_path: Path, document: object) -> Path:
    """Write ``document`` as a YAML policy file under ``tmp_path`` and return its path."""
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return path


def test_the_shipped_policy_loads_and_has_the_documented_parameters() -> None:
    """The file that ships with the repository is valid and says what the documentation says."""
    policy = load_policy()

    assert policy.version == "2"
    assert {c: r.filing_window_days for c, r in policy.categories.items()} == {
        DisputeCategory.UNRECOGNIZED_CHARGE: 120,
        DisputeCategory.DUPLICATE_CHARGE: 60,
        DisputeCategory.WRONG_AMOUNT: 90,
        DisputeCategory.SERVICE_NOT_RECEIVED: 120,
        DisputeCategory.FRAUD_CLAIM: 180,
    }
    assert policy.routing.escalate_amount_usd == Decimal("5000.00")
    assert policy.routing.clarification_budget == 2
    assert policy.routing.risk_routing_enabled is False
    assert dict(policy.first_response_days) == {
        DisputeCategory.UNRECOGNIZED_CHARGE: 3,
        DisputeCategory.DUPLICATE_CHARGE: 3,
        DisputeCategory.WRONG_AMOUNT: 3,
        DisputeCategory.SERVICE_NOT_RECEIVED: 5,
        DisputeCategory.FRAUD_CLAIM: 1,
    }
    assert all(policy.evidence_required[c] for c in DisputeCategory)
    assert "synthetic" in policy.provenance.lower()


def test_the_default_policy_path_points_at_the_repository_file() -> None:
    """The default location resolves from the package, not from the working directory."""
    assert DEFAULT_POLICY_PATH.is_file()
    assert DEFAULT_POLICY_PATH.parent.name == "policy"


def test_a_missing_file_is_a_policy_error_naming_the_file(tmp_path: Path) -> None:
    """The service must not start on a policy it cannot read."""
    with pytest.raises(PolicyError, match=r"nothing\.yaml cannot be read"):
        load_policy(tmp_path / "nothing.yaml")


def test_invalid_yaml_is_a_policy_error(tmp_path: Path) -> None:
    """Syntax errors are reported without echoing the content."""
    path = tmp_path / "broken.yaml"
    path.write_text("version: [unclosed", encoding="utf-8")

    with pytest.raises(PolicyError, match=r"broken\.yaml is not valid YAML"):
        load_policy(path)


@pytest.mark.parametrize("content", ["- just\n- a list\n", "plain text\n", ""])
def test_a_file_that_is_not_a_mapping_is_a_policy_error(tmp_path: Path, content: str) -> None:
    """A list, a scalar or an empty file is not a policy."""
    path = tmp_path / "list.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PolicyError, match="must contain a mapping"):
        load_policy(path)


def _without(document: dict[str, object], *keys: str) -> dict[str, object]:
    """A copy of ``document`` with the given top-level keys removed."""
    return {k: v for k, v in document.items() if k not in keys}


def _with_category_window(window: object) -> dict[str, object]:
    """The shipped policy with the wrong-amount filing window set to ``window``."""
    document = _shipped()
    categories = dict(document["categories"])  # type: ignore[call-overload]
    categories["wrong_amount"] = {"filing_window_days": window, "requires_confirmation": True}
    return {**document, "categories": categories}


def _without_category(name: str) -> dict[str, object]:
    """The shipped policy with the filing rule of one category removed."""
    document = _shipped()
    categories = {k: v for k, v in document["categories"].items() if k != name}  # type: ignore[attr-defined]
    return {**document, "categories": categories}


def _without_first_response(name: str) -> dict[str, object]:
    """The shipped policy with the first-response count of one category removed."""
    document = _shipped()
    counts = {k: v for k, v in document["first_response_days"].items() if k != name}  # type: ignore[attr-defined]
    return {**document, "first_response_days": counts}


def _with_evidence(name: str, items: object) -> dict[str, object]:
    """The shipped policy with the evidence list of one category replaced by ``items``."""
    document = _shipped()
    evidence = dict(document["evidence_required"])  # type: ignore[call-overload]
    evidence[name] = items
    return {**document, "evidence_required": evidence}


@pytest.mark.parametrize(
    ("document", "field"),
    [
        ({**_shipped(), "unknown_key": 1}, "?"),
        (_without(_shipped(), "version"), "version"),
        ({**_shipped(), "version": ""}, "version"),
        ({**_shipped(), "in_scope_product_types": []}, "required"),
        (_with_category_window(0), "categories.wrong_amount.filing_window_days"),
        (_with_category_window("soon"), "categories.wrong_amount.filing_window_days"),
        (_without_category("fraud_claim"), "no rule for: fraud_claim"),
        (
            {**_shipped(), "routing": {**_shipped()["routing"], "nlu_confidence_floor": 1.5}},  # type: ignore[dict-item]
            "routing.nlu_confidence_floor",
        ),
        (
            {**_shipped(), "routing": {**_shipped()["routing"], "escalate_amount_usd": "0"}},  # type: ignore[dict-item]
            "routing.escalate_amount_usd",
        ),
        (
            _without_first_response("fraud_claim"),
            "no first_response_days for: fraud_claim",
        ),
        (_with_evidence("wrong_amount", []), "no evidence_required for: wrong_amount"),
        (_with_evidence("wrong_amount", "not-a-list"), "evidence_required.wrong_amount"),
        (
            {**_shipped(), "routing": {**_shipped()["routing"], "clarification_budget": 0}},  # type: ignore[dict-item]
            "routing.clarification_budget",
        ),
    ],
    ids=[
        "unknown-key",
        "missing-version",
        "blank-version",
        "empty-scope",
        "zero-window",
        "text-window",
        "missing-category",
        "confidence-above-one",
        "zero-threshold",
        "missing-first-response",
        "empty-evidence",
        "evidence-not-a-list",
        "zero-clarification-budget",
    ],
)
def test_an_invalid_policy_is_refused_and_the_fields_at_fault_are_named(
    tmp_path: Path, document: dict[str, object], field: str
) -> None:
    """Validation names where the file is wrong."""
    with pytest.raises(PolicyError) as raised:
        load_policy(_write(tmp_path, document))

    assert field in str(raised.value)


def test_an_error_never_echoes_a_value_from_the_file(tmp_path: Path) -> None:
    """A value in a rejected field, however sensitive it looks, stays out of the message."""
    document = {**_shipped(), "version": ["sk-ant-secret-value"]}

    with pytest.raises(PolicyError) as raised:
        load_policy(_write(tmp_path, document))

    assert "sk-ant-secret-value" not in str(raised.value)


def test_a_policy_file_can_be_loaded_from_any_location(tmp_path: Path) -> None:
    """The loader takes a path; nothing depends on the working directory."""
    path = _write(tmp_path, _shipped())

    assert load_policy(path) == load_policy()


def test_bytes_that_are_not_text_are_a_policy_error(tmp_path: Path) -> None:
    """A file that is not valid UTF-8 is unreadable as a policy, not a crash."""
    path = tmp_path / "binary.yaml"
    path.write_bytes(b"version: \xff")

    with pytest.raises(PolicyError, match=r"binary\.yaml cannot be read"):
        load_policy(path)


def test_a_repeated_key_is_refused(tmp_path: Path) -> None:
    """The last value must not silently override the first."""
    text = DEFAULT_POLICY_PATH.read_text(encoding="utf-8") + '\nversion: "2"\n'
    path = tmp_path / "duplicate.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(PolicyError, match=r"duplicate\.yaml is not valid YAML"):
        load_policy(path)


def test_unknown_keys_and_values_are_never_echoed(tmp_path: Path) -> None:
    """An unknown key, at any depth, is shown as a placeholder, like a value."""
    document = _shipped()
    categories = dict(document["categories"])  # type: ignore[call-overload]
    categories["SECRET-KEY-abc123"] = {"filing_window_days": 1, "requires_confirmation": True}
    document = {**document, "categories": categories, "hunter2_token": "hunter2"}

    with pytest.raises(PolicyError) as raised:
        load_policy(_write(tmp_path, document))

    message = str(raised.value)
    for leaked in ("SECRET-KEY-abc123", "hunter2_token", "hunter2"):
        assert leaked not in message
    assert "?" in message


def test_an_amount_written_as_a_float_is_refused(tmp_path: Path) -> None:
    """Money is text in the file: a float carries binary rounding error."""
    document = {**_shipped(), "routing": {**_shipped()["routing"], "escalate_amount_usd": 5000.0}}  # type: ignore[dict-item]

    with pytest.raises(PolicyError, match=r"routing\.escalate_amount_usd"):
        load_policy(_write(tmp_path, document))


def test_a_flag_written_as_text_is_refused(tmp_path: Path) -> None:
    """``"yes"`` is not a boolean; the policy does not guess."""
    document = {
        **_shipped(),
        "routing": {**_shipped()["routing"], "escalate_repeat_complainer": "yes"},  # type: ignore[dict-item]
    }

    with pytest.raises(PolicyError, match=r"routing\.escalate_repeat_complainer"):
        load_policy(_write(tmp_path, document))


def test_the_loaded_policy_is_equal_across_loads() -> None:
    """Loading twice gives equal policies (the read-only category mapping compares by value)."""
    assert load_policy() == load_policy()


@pytest.mark.parametrize(
    "key", ["? [a, b]\n: 1\n", "? {a: 1}\n: 1\n"], ids=["list-key", "mapping-key"]
)
def test_a_key_that_cannot_be_hashed_is_a_policy_error(tmp_path: Path, key: str) -> None:
    """A list or mapping used as a key is invalid YAML for a policy, not a crash."""
    path = tmp_path / "unhashable.yaml"
    path.write_text(key, encoding="utf-8")

    with pytest.raises(PolicyError, match=r"unhashable\.yaml is not valid YAML"):
        load_policy(path)
