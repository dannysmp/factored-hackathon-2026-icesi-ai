"""
Prompt Loading Tests
=====================

Component: ``app.llm.prompts``. Hermetic and pure: no network; a temporary directory stands in
for ``prompts/`` where a test needs a file that is not the shipped ``nlu_v1``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.llm.prompts import PROMPTS_DIR, PromptError, PromptTemplate, load_prompt


def test_the_shipped_nlu_prompt_loads_and_validates() -> None:
    """``prompts/nlu_v1.yaml`` is a real, valid prompt file, not just a fixture."""
    prompt = load_prompt("nlu_v1")

    assert prompt.version == "5"
    assert prompt.system.strip()
    assert prompt.placeholders() == {"language_hint", "message"}


def test_the_shipped_prompt_lives_under_the_repository_prompts_directory() -> None:
    """``PROMPTS_DIR`` is the top-level ``prompts/`` folder, not somewhere under ``app/``."""
    assert PROMPTS_DIR.name == "prompts"
    assert (PROMPTS_DIR / "nlu_v1.yaml").is_file()


def test_render_task_fills_every_placeholder() -> None:
    prompt = PromptTemplate(version="1", system="s", task_template="Lang: {lang}\nText: {text}")

    rendered = prompt.render_task(lang="es", text="hola")

    assert rendered == "Lang: es\nText: hola"


def test_render_task_refuses_a_missing_placeholder_value() -> None:
    prompt = PromptTemplate(version="1", system="s", task_template="Lang: {lang}\nText: {text}")

    with pytest.raises(PromptError, match="missing placeholder"):
        prompt.render_task(lang="es")


def test_render_task_refuses_an_unused_value() -> None:
    prompt = PromptTemplate(version="1", system="s", task_template="Lang: {lang}")

    with pytest.raises(PromptError, match="unused placeholder"):
        prompt.render_task(lang="es", extra="not used")


def test_load_prompt_reports_a_missing_file() -> None:
    with pytest.raises(PromptError, match="cannot be read"):
        load_prompt("does_not_exist", directory=PROMPTS_DIR)


def test_load_prompt_reports_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("version: [1, 2\n", encoding="utf-8")

    with pytest.raises(PromptError, match="not valid YAML"):
        load_prompt("broken", directory=tmp_path)


def test_load_prompt_reports_a_document_that_is_not_a_mapping(tmp_path: Path) -> None:
    (tmp_path / "list.yaml").write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(PromptError, match="must contain a mapping"):
        load_prompt("list", directory=tmp_path)


def test_load_prompt_reports_a_missing_required_field(tmp_path: Path) -> None:
    (tmp_path / "incomplete.yaml").write_text('version: "1"\nsystem: "s"\n', encoding="utf-8")

    with pytest.raises(PromptError, match="task_template"):
        load_prompt("incomplete", directory=tmp_path)


@pytest.mark.parametrize(
    "phrase",
    [
        "choice intent",
        '"option 2"',
        '"la segunda"',
        '"opción 3"',
        '"a terceira"',
        "only when the message names a position from 1 to 5",
        "that field, not a choice",
        "Fill choice only for the choice intent",
    ],
)
def test_the_shipped_nlu_prompt_teaches_the_choice_intent_and_its_limits(phrase: str) -> None:
    """The extraction prompt names the choice intent, gives a pick example per language and says
    when a number is not a choice."""
    assert phrase in load_prompt("nlu_v1").system


@pytest.mark.parametrize(
    "phrase",
    [
        "making a transfer",
        "is a dispute of that transaction",
        "leave merchant null",
        'A bare "$" does not state a currency',
        "the sign € or",
        'The word "pesos" alone names no country',
    ],
)
def test_the_shipped_nlu_prompt_keeps_a_transfer_already_made_disputable_and_states_currencies(
    phrase: str,
) -> None:
    """The extraction prompt separates a transfer to make from one already made, keeps a kind of
    transaction out of the merchant, and says which marks state a currency."""
    assert " ".join(load_prompt("nlu_v1").system.split()).find(phrase) >= 0
