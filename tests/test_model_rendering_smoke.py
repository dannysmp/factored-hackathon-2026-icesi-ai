"""
Model-Rendering Smoke Test
===========================

Component: ``app.conversation.reply.render_reply`` driven across every step of the six committed
scripted flows (``tests/fixtures/scripted_flows.py``), in Spanish, Portuguese and English. Hermetic
and fast: ``FakeLlm``, no network — the CI-safe net run before the expensive, real-model evaluation.
"""

from __future__ import annotations

# Third-party libraries
import pytest

# Local modules
from app.conversation.model_renderer import LlmRenderer
from app.conversation.renderer import render
from app.conversation.reply import MODEL_ELIGIBLE_TEMPLATES, render_reply
from app.conversation.slot_values import slot_values_for
from app.llm.client import FakeLlm, LlmUnavailable
from contracts.service_v1.envelope import Lang, RenderEnvelope
from tests.fixtures.scripted_flows import all_envelopes

_STEPS: list[tuple[Lang, str, RenderEnvelope]] = [
    (lang, name, envelope.render_view()) for lang, name, envelope in all_envelopes()
]
_ELIGIBLE = [step for step in _STEPS if step[2].template_id in MODEL_ELIGIBLE_TEMPLATES]
_INELIGIBLE = [step for step in _STEPS if step[2].template_id not in MODEL_ELIGIBLE_TEMPLATES]


def _ids(steps: list[tuple[Lang, str, RenderEnvelope]]) -> list[str]:
    return [f"{lang}-{name}-{envelope.template_id}" for lang, name, envelope in steps]


def _valid_candidate_text(envelope: RenderEnvelope) -> str:
    """A candidate that cites every grounded entry once, in order: always fully resolvable."""
    entries = slot_values_for(envelope).entries
    if not entries:
        return "Listo."
    return " ".join(f"{{{{{entry.field.value}}}}}" for entry in entries)


def test_every_committed_flow_step_is_covered() -> None:
    """A sanity floor: the six flows in three languages produce more than a token few steps."""
    assert len(_STEPS) > 18
    assert _ELIGIBLE
    assert _INELIGIBLE


@pytest.mark.parametrize(("lang", "name", "envelope"), _ELIGIBLE, ids=_ids(_ELIGIBLE))
def test_an_eligible_step_renders_through_the_model_when_the_candidate_is_valid(
    lang: Lang, name: str, envelope: RenderEnvelope
) -> None:
    candidate_text = _valid_candidate_text(envelope)
    llm = FakeLlm(responses=[{"text": candidate_text}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    result = render_reply(envelope, model_renderer=renderer)

    assert len(llm.requests) == 1
    assert result.reply
    assert "{{" not in result.reply
    assert "}}" not in result.reply


@pytest.mark.parametrize(("lang", "name", "envelope"), _ELIGIBLE, ids=_ids(_ELIGIBLE))
def test_an_eligible_step_falls_back_to_the_template_on_a_rejected_candidate(
    lang: Lang, name: str, envelope: RenderEnvelope
) -> None:
    """A candidate that writes a bare digit is rejected; the fallback is byte-identical to the
    template path rendered directly — not merely "a reasonable reply", the same text."""
    llm = FakeLlm(responses=[{"text": "El monto es 100."}])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    result = render_reply(envelope, model_renderer=renderer)
    expected = render(envelope)

    assert result.reply == expected.reply
    assert result.reference_date_line == expected.reference_date_line


@pytest.mark.parametrize(("lang", "name", "envelope"), _ELIGIBLE, ids=_ids(_ELIGIBLE))
def test_an_eligible_step_falls_back_to_the_template_when_the_model_call_fails(
    lang: Lang, name: str, envelope: RenderEnvelope
) -> None:
    llm = FakeLlm(responses=[LlmUnavailable("boom")])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    result = render_reply(envelope, model_renderer=renderer)
    expected = render(envelope)

    assert result.reply == expected.reply


@pytest.mark.parametrize(("lang", "name", "envelope"), _INELIGIBLE, ids=_ids(_INELIGIBLE))
def test_an_ineligible_step_never_reaches_the_model_at_all(
    lang: Lang, name: str, envelope: RenderEnvelope
) -> None:
    """Excluded structurally, not by omission: the model renderer is never even called."""
    llm = FakeLlm(responses=[])
    renderer = LlmRenderer(llm, model="claude-sonnet-5")

    result = render_reply(envelope, model_renderer=renderer)
    expected = render(envelope)

    assert not llm.requests
    assert result.reply == expected.reply


def test_rendering_is_disabled_by_default_with_no_model_renderer() -> None:
    """``model_renderer=None`` (the composition root's default) never attempts model rendering."""
    _, _, envelope = _ELIGIBLE[0]

    result = render_reply(envelope)
    expected = render(envelope)

    assert result.reply == expected.reply
