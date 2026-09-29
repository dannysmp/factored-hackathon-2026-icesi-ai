"""
Reply Rendering Orchestrator
=============================

Overview
--------
Decides, for one already-built template-mode envelope, whether to render it through the model
path (written by the model, then verified) or the fixed-wording template path, and always falls
back to the template on any failure: a rejected candidate, a failed model call, or an envelope
whose template is not eligible for model rendering at all. This is the deterministic degrade path
the plan calls for — a renderer failure never reaches the customer as an error, only as the
template's own wording.

Scope
-----
In: ``render_reply(envelope, *, model_renderer=None) -> RenderedReply``, and the closed set of
templates eligible for model rendering.
Out: building the template-mode envelope in the first place (the dialogue controller), the model
call itself (``app.conversation.model_renderer``), building ``SlotValues``
(``app.conversation.slot_values``) and the verification algorithm (``app.conversation.verifier``).

Design Principles
-----------------
- The template-mode envelope is always built first and is the fallback target, never something
  reconstructed after the fact: a model-mode sibling is derived from it and re-validated through
  the contract's own constructor, never ``model_copy``, which would skip the validators the
  contract relies on to keep model mode and template mode from disagreeing.
- Eligibility is an explicit, exhaustive allow-list of ``TemplateId``s, not just "the intent has
  fields to cite": some handoff variants carry safety-relevant wording a generic model-rendered
  sentence would weaken, and ``RenderEnvelope`` carries nothing that would still tell them apart
  once ``template_id`` is stripped for model mode; a zero-case dispute-status reply is not just
  ineligible but contractually unbuildable in model mode at all. Every purely procedural template
  (a greeting, a clarification, a farewell, a cancellation) is excluded too, since it has no
  grounded content to gain from model wording.
- Never guesses on failure: a verifier rejection, a ``None`` from the model renderer, and an
  ineligible template all take the exact same path — render the already-built template envelope,
  unchanged.
- Every outcome is logged (ineligible, a failed model call, a rejected candidate with its reasons,
  or an accepted one), so a model-rendering degradation is visible without instrumenting every
  caller of ``render_reply`` separately.

Runtime Contract
----------------
``render_reply(template_envelope, *, model_renderer=None) -> RenderedReply``.
``MODEL_ELIGIBLE_TEMPLATES``: the closed set of ``TemplateId``s eligible for model rendering.
"""

from __future__ import annotations

# Standard libraries
import logging  # Structured events about the model path's outcome, never print

# Local modules
from app.conversation.model_renderer import LlmRenderer
from app.conversation.renderer import RenderedReply, reference_date_line, render
from app.conversation.slot_values import slot_values_for
from app.conversation.verifier import verify
from app.security.middleware import current_request_id  # Correlates a log line to its request
from contracts.service_v1.envelope import RenderEnvelope, TemplateId

logger = logging.getLogger(__name__)

# Every template eligible for model rendering. Deliberately excludes: NO_CASE_FOUND (the contract
# itself refuses to build a zero-case dispute_status envelope in model mode at all, per
# _intent_has_what_it_states); HANDOFF_CARD_LOSS, HANDOFF_REQUESTED, HANDOFF_NOT_REGISTERED and
# FILING_UNVERIFIED (their wording carries safety-relevant or procedural content a generic
# model-rendered sentence can't distinguish from HANDOFF_REVIEW's own, since RenderEnvelope carries
# nothing that would tell them apart once template_id is stripped for model mode); REFUSE_* (the
# contract's own _wording_matches_the_mode forbids a refusal from ever rendering in model mode);
# ABSTAIN_POLICY (Intent.ABSTAIN has no required grounded field at all — the verifier only rejects
# a digit, a malformed placeholder or a missing *required* field, so nothing would force a
# model-rendered reply to actually state the ADR-16-mandated abstention sentence rather than any
# other digit-free text); and every purely procedural template (a greeting, a clarification, a
# farewell, a cancellation) with no grounded content to gain from model wording.
MODEL_ELIGIBLE_TEMPLATES = frozenset(
    {
        TemplateId.PRESENT_ONE,
        TemplateId.PRESENT_LIST,
        TemplateId.PRESENT_NARROW,
        TemplateId.CONFIRM_FILING,
        TemplateId.FILING_RESULT,
        TemplateId.INELIGIBLE,
        TemplateId.DISPUTE_STATUS,
        TemplateId.POLICY_ANSWER,
        TemplateId.HANDOFF_REVIEW,
        TemplateId.HANDOFF_FRAUD,
    }
)


def _model_sibling(template_envelope: RenderEnvelope) -> RenderEnvelope:
    """The model-mode sibling of ``template_envelope``, re-validated through the contract."""
    fields = template_envelope.model_dump()
    fields["render_mode"] = "model"
    fields["template_id"] = None
    return RenderEnvelope(**fields)


def render_reply(
    template_envelope: RenderEnvelope, *, model_renderer: LlmRenderer | None = None
) -> RenderedReply:
    """Render ``template_envelope`` through the model path when eligible, else the template path.

    Parameters
    ----------
    template_envelope : RenderEnvelope
        An already-built, valid, template-mode envelope (``render_mode == "template"``): both the
        model path's own source of grounded facts and the fallback target on any failure.
    model_renderer : LlmRenderer | None
        The model-backed renderer to try first; ``None`` disables model rendering entirely (the
        composition root's decision, from ``Settings.model_renderer_enabled``).
    """
    session_id = template_envelope.session_id
    eligible = (
        model_renderer is not None and template_envelope.template_id in MODEL_ELIGIBLE_TEMPLATES
    )
    if not eligible:
        logger.debug(
            "render_reply_ineligible session_id=%s template_id=%s request_id=%s",
            session_id,
            template_envelope.template_id,
            current_request_id(),
        )
        return render(template_envelope)

    assert model_renderer is not None  # noqa: S101 - guaranteed by `eligible` above
    model_envelope = _model_sibling(template_envelope)
    candidate = model_renderer.render(model_envelope)
    if candidate is None:
        logger.info(
            "render_reply_fallback reason=model_unavailable session_id=%s template_id=%s "
            "request_id=%s",
            session_id,
            template_envelope.template_id,
            current_request_id(),
        )
        return render(template_envelope)

    result = verify(model_envelope, candidate, slot_values_for(model_envelope))
    if result.outcome == "accepted":
        assert result.rendered_text is not None  # noqa: S101 - guaranteed when accepted
        logger.info(
            "render_reply_accepted session_id=%s template_id=%s request_id=%s",
            session_id,
            template_envelope.template_id,
            current_request_id(),
        )
        return RenderedReply(
            reply=result.rendered_text,
            reference_date_line=reference_date_line(
                template_envelope.domain_date, template_envelope.lang
            ),
            render_mode="model",
        )

    logger.info(
        "render_reply_rejected session_id=%s template_id=%s reasons=%s request_id=%s",
        session_id,
        template_envelope.template_id,
        ",".join(reason.value for reason in result.reasons),
        current_request_id(),
    )
    return render(template_envelope)
