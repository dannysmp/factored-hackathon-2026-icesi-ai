"""
Text Matching Tests
===================

Overview
--------
The folding rule shared by the dialogue controller and the transaction store: its behaviour on
accents and case, and the consistency of the store's character map with it.
"""

from __future__ import annotations

import duckdb
import pytest

from app.domain.text_matching import SQL_BLANKS, SQL_FOLD_FROM, SQL_FOLD_TO, fold_text


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("Café", "cafe"),
        ("SÃO PAULO", "sao paulo"),
        ("Pão de Açúcar", "pao de acucar"),
        ("Nuñez", "nunez"),
        ("plain", "plain"),
        ("", ""),
    ],
)
def test_fold_removes_accents_and_case(text: str, folded: str) -> None:
    assert fold_text(text) == folded


def test_the_character_map_pairs_each_letter_with_its_single_character_fold() -> None:
    assert len(SQL_FOLD_FROM) == len(SQL_FOLD_TO) > 0
    for source, target in zip(SQL_FOLD_FROM, SQL_FOLD_TO, strict=True):
        assert len(target) == 1
        assert fold_text(source) == target


def test_the_character_map_has_no_repeated_source_letter() -> None:
    assert len(set(SQL_FOLD_FROM)) == len(SQL_FOLD_FROM)


@pytest.mark.parametrize("text", ["Café Sol", "PÃO DE AÇÚCAR", "Ñandú", "Hôtel Zürich", "ASCII 42"])
def test_the_character_map_with_lower_matches_fold_for_every_covered_letter(text: str) -> None:
    table = str.maketrans(SQL_FOLD_FROM, SQL_FOLD_TO)
    assert text.translate(table).lower() == fold_text(text)


def test_the_character_map_with_lower_matches_fold_for_every_merchant_in_the_seed() -> None:
    try:
        rows = duckdb.sql(
            "SELECT DISTINCT merchant_name FROM 'data/gold/ops_seed/transactions.parquet' "
            "WHERE merchant_name IS NOT NULL"
        ).fetchall()
    except duckdb.IOException:
        pytest.skip("the seed is not present")
    table = str.maketrans(SQL_FOLD_FROM, SQL_FOLD_TO)
    assert rows
    for (name,) in rows:
        assert name.translate(table).lower() == fold_text(name)


def test_the_blanks_the_store_trims_are_those_a_strip_removes_within_latin_1() -> None:
    assert SQL_BLANKS
    for char in SQL_BLANKS:
        assert f"{char}x{char}".strip() == "x"
    for code in range(0x100):
        char = chr(code)
        if char not in SQL_BLANKS:
            assert f"{char}x".strip() == f"{char}x"
