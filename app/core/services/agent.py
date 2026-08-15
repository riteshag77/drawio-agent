"""Public LLM orchestration API — cloud, generic and simple diagram generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.outbound.diagram.compiler import (
    compile_archimate_ir,
    compile_corridor_ir,
    compile_generic_ir,
    compile_network_ir,
    compile_simple_ir,
)
from app.adapters.outbound.diagram.diagram_edits import apply_diagram_edits
from app.adapters.outbound.utils.chat import (
    build_applied_suggestions_block,
    build_suggestions_instruction_block,
)
from app.adapters.outbound.llm.prompts.ir import (
    get_archimate_ir_system_prompt,
    get_corridor_ir_system_prompt,
    get_generic_ir_system_prompt,
    get_network_ir_system_prompt,
    get_simple_ir_system_prompt,
)
from app.adapters.outbound.llm.prompts.system import get_xml_gen_system_prompt_marked
from app.adapters.outbound.llm.prompts.vlm_qa import VLM_QA_SYSTEM_PROMPT
from app.adapters.outbound.governance_store import list_rules as list_governance_rules
from app.adapters.outbound.llm.providers import get_provider, normalize_provider_key
from app.adapters.outbound.saas_catalog import find_saas_vendor_mismatches
from app.core.ports.llm import LlmPort, get_llm
from app.core.services.governance import evaluate_governance_rules, findings_to_warnings
from app.domain.entities import DiagramEditPlan, VisualQaResult, XmlDiagram
from app.domain.entities.xml_diagram import DiagramSuggestion
from app.domain.ir import (
    IR_TYPES_BY_VIEW,
    ArchimateIR,
    GenericDiagramIR,
    NetworkIR,
    SimpleDiagramIR,
)
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.core.logging import StepLogger


class _FollowupSuggestions(BaseModel):
    """Lightweight structured output for IR-path follow-up suggestions.

    IR schemas deliberately omit suggestions (nodes-then-edges generation order);
    when the user enables suggestions we ask for them in a second tiny call.
    """

    suggestions: list[DiagramSuggestion] = Field(
        default_factory=list,
        description="Exactly 2–3 concrete follow-up architecture improvements.",
    )


_SUGGESTIONS_SYSTEM = (
    "You are a principal cloud architect. Given a brief inventory of an architecture "
    "just generated, propose follow-up improvements. Return only the suggestions list."
)

#: Op kinds that can never remove existing content — a plan built only from
#: these is safe to apply even if the LLM itself flagged applicable=false.
_NON_DESTRUCTIVE_OPS = frozenset({"add_node", "connect", "relabel", "update_shape", "move"})


def _is_purely_additive(ops: list) -> bool:
    return bool(ops) and all((getattr(o, "op", None) or "") in _NON_DESTRUCTIVE_OPS for o in ops)

_EDIT_PLAN_SYSTEM = """You plan surgical edits to an existing draw.io architecture diagram.
Given the user request and a summary of current cell ids/labels, output a DiagramEditPlan.
Set applicable=true ONLY if a small set of ops fully satisfies the request (add/remove/connect/move/relabel).
Set applicable=false for redesigns, layout overhauls, or when you are unsure — the system will regenerate.
If the request is a numbered/bulleted list of separate findings (e.g. compliance or Well-Architected
gaps), it is one request with multiple parts — every part needs its own op(s), not just the first
or easiest few. Do not stop after satisfying some of the items: either cover all of them or set
applicable=false so the system regenerates the whole diagram instead of half-applying the list.
Adding missing services (the common compliance/WAF gap-fix shape — e.g. "add a KMS key", "add a WAF
in front of X") is ALWAYS a small edit, NO MATTER HOW MANY are listed: one add_node per missing
service (plus connect if the finding implies a relationship) fully satisfies each one. A batch of 8
independent "add missing service X" findings is exactly as small an edit as a batch of 1 — it's still
just 8 independent add_node ops, not a redesign. List length alone is never a reason for
applicable=false. Only use applicable=false when a request genuinely requires restructuring existing
topology (replacing a component, merging tiers, changing the AZ/subnet layout) — never for a long
list of pure additions. Regenerating via IR is NOT a reliable preserve-everything operation: it WILL
silently drop existing nodes (subnets, AZs, queues, replicas) that a text summary doesn't perfectly
capture. When in doubt between applicable=true with more ops vs. applicable=false, prefer
applicable=true — an incomplete but additive patch is always better than a lossy full rebuild.
Never invent ids that must already exist unless op is add_node (then provide a new id).
For add_node, ALWAYS include shape_type and a reasonable parent id from the diagram — a node with no
shape_type renders as an unstyled grey box, which is never the goal. Many requests name a
configuration/feature of an existing AWS service rather than a distinct one with its own icon (e.g.
"VPC Flow Logs" is a CloudWatch Logs feature, "S3 Object Lock"/"S3 Replica Bucket"/"S3 VPC Endpoint"
are all S3, "CloudTrail log file integrity validation" is a CloudTrail setting) — for these, use the
underlying service's own shape_type (aws_cloudwatch, aws_s3, aws_cloudtrail) with a label that keeps
the specific detail (e.g. label "S3 Object Lock", shape_type "aws_s3"), rather than leaving shape_type
blank. Only when a request is a genuinely distinct, named AWS service (e.g. "AWS Config") should its
own dedicated shape_type be used.
Omit x/y and the system will place the node for you, clear of every existing sibling.
When suggestions are requested, always fill `suggestions` with 2–3 concrete follow-up improvements.
"""

_IMAGE_REPLICATE_HINT = (
    "## Image / PDF replicate mode\n"
    "The user attached diagram screenshot(s) or architecture PDF pages. "
    "Preserve the visible topology, grouping, and flows as faithfully as possible. "
    "Map each recognizable cloud service to the correct shape_type; use grey placeholders "
    "for unknowns. Prefer fidelity to the attachment over inventing new services."
)

_IMAGE_REPLICATE_CORRIDOR_ADDENDUM = (
    "\n\nThis is the {view_label} view, which has its own inclusion/exclusion rules "
    "in the system prompt above — apply them even while replicating this image. "
    "The attachment may show infrastructure plumbing (VPC/subnets, security groups, "
    "load balancers, WAF, monitoring/observability, secrets/KMS, IAM) that this "
    "view's own rules deliberately exclude — do not draw it here just because it's "
    "visible in the image; that detail has its own home on Network or Security. "
    "Extract the image's *topology and relationships* (what connects to what, and in "
    "what order) faithfully, filtered through this view's lens — not a literal "
    "1:1 inventory of every box drawn."
)

_ANIMATED_HINT = (
    "## Animated connectors\n"
    "For edges that represent primary request/data flows, append `flowAnimation=1;` to the edge style."
)

_IAC_HINT = (
    "## IaC-derived architecture\n"
    "Document context includes Infrastructure-as-Code. Derive services and edges from declared "
    "resources only — do not invent undeclared cloud services. Add network parents (VPC/subnet) "
    "only when implied by the IaC or required for a valid hierarchy."
)


def _build_human_parts(
    *,
    user_request: str,
    chat_messages: list[dict[str, str]],
    platform: str,
    applied_suggestions: list[str] | None,
    context_text: str | None,
    current_xml: str | None,
    retry_hint: str | None,
    image_replicate: bool,
    animated_connectors: bool,
    include_suggestions: bool = False,
) -> str:
    human_parts: list[str] = []
    if retry_hint:
        human_parts.append(retry_hint)
    sugg_block = build_suggestions_instruction_block(include_suggestions)
    if sugg_block:
        human_parts.append(sugg_block)
    applied_block = build_applied_suggestions_block(applied_suggestions)
    if applied_block:
        human_parts.append(applied_block)
    if image_replicate:
        human_parts.append(_IMAGE_REPLICATE_HINT)
    if animated_connectors:
        human_parts.append(_ANIMATED_HINT)
    if context_text and _looks_like_iac(context_text):
        human_parts.append(_IAC_HINT)
    if current_xml:
        human_parts.append(
            "## Current diagram (edit this exact XML — this is Edit mode)\n"
            "```xml\n" + current_xml + "\n```\n"
            "Preserve every id/parent/coordinate not relevant to the requested change, "
            "per Create vs Edit Modes."
        )
    if len(chat_messages) > 1:
        human_parts.append("Conversation so far:")
        for msg in chat_messages[:-1]:
            human_parts.append(f"{msg['role'].upper()}: {msg['content']}")
    human_parts.append(f"Platform: {platform.upper()}")
    if context_text:
        human_parts.append(f"Document context:\n{context_text}")
    human_parts.append(f"Request:\n{chat_messages[-1]['content']}")
    return "\n".join(human_parts)


def _looks_like_iac(text: str) -> bool:
    t = (text or "").lower()
    markers = (
        "resource \"", "provider \"", "terraform {", "apiVersion:", "kind: deployment",
        "kind: service", "kubernetes", "helm", '"resources":', "planned_values",
        "resource_changes",
    )
    return any(m.lower() in t for m in markers)


def generate_edit_plan(
    user_request: str,
    current_xml: str,
    provider: str = "claude",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    *,
    include_suggestions: bool = False,
    llm: LlmPort | None = None,
) -> DiagramEditPlan:
    """Ask the LLM for surgical ops against current_xml (ids summarized)."""
    import xml.etree.ElementTree as ET

    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]
    summary_lines = []
    try:
        tree = ET.fromstring(current_xml)
        for cell in tree.findall(".//mxCell"):
            cid = cell.get("id")
            if not cid or cid in ("0", "1"):
                continue
            kind = "edge" if cell.get("edge") == "1" else "vertex"
            label = (cell.get("value") or "")[:80]
            st = cell.get("shape_type") or ""
            parent = cell.get("parent") or ""
            summary_lines.append(f"- {cid} [{kind}] parent={parent} shape_type={st} label={label!r}")
    except ET.ParseError:
        summary_lines.append("(could not parse current XML for summary)")
    human = (
        build_suggestions_instruction_block(include_suggestions) + "\n\n"
        f"Platform: {platform.upper()}\n"
        f"Current cells ({len(summary_lines)}):\n" + "\n".join(summary_lines[:200]) + "\n\n"
        f"Request:\n{chat_messages[-1]['content']}"
    )
    if logger:
        logger.log("prompt", "Prepared surgical edit plan", details={"cells": len(summary_lines)})
    _llm = llm or get_llm()
    return _llm(_EDIT_PLAN_SYSTEM, human, provider_key, DiagramEditPlan, logger, None)


def generate_xml_diagram(
    user_request: str,
    provider: str = "claude",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    custom_system_message: str = "",
    applied_suggestions: list[str] | None = None,
    three_d: bool = False,
    context_text: str | None = None,
    attachment_documents: list[dict] | None = None,
    current_xml: str | None = None,
    retry_hint: str | None = None,
    image_replicate: bool = False,
    animated_connectors: bool = False,
    include_suggestions: bool = False,
    primary_view: str = "network",
    *,
    llm: LlmPort | None = None,
) -> XmlDiagram:
    """Direct XML only for 3D or surgical edits of an existing diagram.

    Fresh cloud diagrams must use ``generate_xml_diagram_from_ir``. Non-surgical
    edits of ``current_xml`` raise ``NonSurgicalEditError`` so the pipeline can
    regenerate via IR instead of rewriting mxCells from ``core.txt``.
    """
    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]
    hint = user_request or (chat_messages[-1]["content"] if chat_messages else "")
    _llm = llm or get_llm()

    # --- Surgical path (prefer ops; LLM decides via applicable=false) ---
    if (
        current_xml
        and len(current_xml) > 200
        and not three_d
        and not retry_hint
    ):
        try:
            plan = generate_edit_plan(
                hint, current_xml, provider=provider_key, platform=platform,
                logger=logger, messages=chat_messages,
                include_suggestions=include_suggestions, llm=_llm,
            )
            effective_plan = plan
            override_reason = None
            if not plan.applicable and _is_purely_additive(plan.ops):
                # The LLM flagged this batch as too large for a surgical edit —
                # common on an "Apply All" of many independent gap-fix findings
                # (e.g. 8 separate "add missing service X" asks bundled into one
                # request) — but every op it DID generate is purely additive (no
                # delete_node/disconnect). Applying them is strictly safer than
                # NonSurgicalEditError's fallback: a full IR regenerate is NOT a
                # reliable preserve-everything operation — a live capture showed
                # it silently drop an entire AZ, a DB replica, a DLQ, and two NAT
                # gateways from an otherwise-untouched diagram. An incomplete but
                # additive patch beats a lossy full rebuild.
                effective_plan = plan.model_copy(update={"applicable": True})
                override_reason = "LLM marked not-applicable but every op was purely additive"

            if effective_plan.applicable and effective_plan.ops:
                patched = apply_diagram_edits(current_xml, effective_plan)
                if logger:
                    message = f"Applied {len(effective_plan.ops)} surgical ops"
                    if override_reason:
                        message += f" ({override_reason} — applying anyway to avoid a lossy full regenerate)"
                    logger.log(
                        "surgical_edit",
                        message,
                        level="success",
                        details={
                            "ops": [o.op for o in effective_plan.ops],
                            "llm_applicable": plan.applicable,
                        },
                    )
                sugg = (effective_plan.suggestions or []) if include_suggestions else []
                if include_suggestions and not sugg:
                    # The edit-plan LLM's own suggestions field came back empty —
                    # same "never leave suggestions empty" non-compliance the IR
                    # path's _generate_followup_suggestions already retries and
                    # flags degraded for. Route through that SAME shared,
                    # already-hardened function instead of silently returning
                    # nothing here too — "suggestions on" should behave
                    # identically regardless of which path produced the diagram.
                    sugg = _generate_followup_suggestions(
                        inventory=_xml_labels_inventory(patched),
                        view=primary_view or "network",
                        user_request=hint,
                        provider_key=provider_key,
                        llm=_llm,
                        logger=logger,
                        applied_suggestions=applied_suggestions,
                    )
                return XmlDiagram(
                    xml=patched,
                    assistant_message=effective_plan.assistant_message or "Updated diagram.",
                    suggestions=sugg,
                )
            if logger:
                logger.log("surgical_edit", "Edit plan not applicable — regenerate via IR", level="info")
        except Exception as exc:
            if logger:
                logger.log("surgical_edit", f"Surgical edit failed ({exc}) — regenerate via IR", level="warn")
        raise NonSurgicalEditError(
            "This change needs a full redesign. Regenerating via the IR path "
            "(or ask a smaller edit: rename, add one service, connect A→B)."
        )

    if not three_d:
        raise ValueError(
            "generate_xml_diagram is only for three_d=True or surgical edits. "
            "Fresh diagrams use generate_xml_diagram_from_ir."
        )

    # --- 3D isometric path ---
    provider_spec = get_provider(provider_key)
    system_prompt = get_xml_gen_system_prompt_marked(
        model_id=provider_spec.default_model,
        provider=provider_key,
        platform=platform,
        custom_system_message=custom_system_message,
        three_d=True,
        user_hint=hint,
    )
    human_content = _build_human_parts(
        user_request=user_request,
        chat_messages=chat_messages,
        platform=platform,
        applied_suggestions=applied_suggestions,
        context_text=context_text,
        current_xml=current_xml,
        retry_hint=retry_hint,
        image_replicate=image_replicate,
        animated_connectors=animated_connectors,
        include_suggestions=include_suggestions,
    )

    if logger:
        logger.log("prompt", "Prepared 3D XML generation request", details={"provider": provider_key, "platform": platform})

    try:
        result = _llm(
            system_prompt, human_content, provider_key, XmlDiagram, logger,
            attachment_documents,
        )
    except Exception as exc:
        if logger:
            logger.log("error", f"XML generation LLM call failed: {exc}", level="error")
        raise

    if not result.assistant_message:
        result.assistant_message = "Generated diagram."
    if not include_suggestions:
        result.suggestions = []
    elif result.suggestions is None:
        result.suggestions = []

    if logger:
        logger.log("xml_diagram", "Draw.io XML generated", level="success", details={
            "xml_chars": len(result.xml),
            "xml": result.xml,
        })
    return result


class NonSurgicalEditError(ValueError):
    """Raised when current_xml is present but the change is too large for surgical ops."""


#: How many corrective LLM passes to spend on an IR that parsed but broke a
#: semantic rule. One pass is enough for a named bad flow reference. Empty
#: ``flows`` used to burn a second identical pass (live captures: same
#: ``flows: []`` at temperature 0) then fall back to direct XML — that case is
#: now handled by ``synthesize_spine_flows`` after the residual check, so a
#: second LLM round-trip is pure waste.
IR_MAX_CORRECTIVE_PASSES = 1

#: view -> (page name, IR type, compiler) — the single dispatch table every
#: view-agnostic IR call site reads from. Adding a future view means adding one
#: row here, not a new if/elif chain at each call site.
_IR_PAGE_NAMES: dict[str, str] = {
    "network": "Network", "logical": "Logical",
    "security": "Security", "data_flow": "Data flow",
    "archimate": "ArchiMate",
}


def _prepare_ir_call(
    *,
    user_request: str,
    provider: str,
    messages: list[dict[str, str]] | None,
    llm: LlmPort | None,
):
    """Setup boilerplate shared by ``generate_network_ir`` and ``generate_corridor_ir``.

    Both stay separate public functions (each is patched independently in
    tests, and their signatures genuinely differ — one takes a ``view``, one
    doesn't), but the five lines resolving provider/messages/hint/llm/spec
    were identical in both, so that part lives here once.
    """
    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]
    hint = user_request or (chat_messages[-1]["content"] if chat_messages else "")
    _llm = llm or get_llm()
    provider_spec = get_provider(provider_key)
    return provider_key, chat_messages, hint, _llm, provider_spec


def _build_ir_human_content(
    *,
    view: str,
    platform: str,
    chat_messages: list[dict[str, str]],
    applied_suggestions: list[str] | None,
    context_text: str | None,
    image_replicate: bool,
) -> str:
    """Human message shared by every IR generator — view-agnostic by design."""
    parts: list[str] = []
    applied_block = build_applied_suggestions_block(applied_suggestions)
    if applied_block:
        parts.append(applied_block)
    if image_replicate:
        hint = _IMAGE_REPLICATE_HINT
        if view != "network":
            hint += _IMAGE_REPLICATE_CORRIDOR_ADDENDUM.format(
                view_label=_IR_PAGE_NAMES.get(view, view.title())
            )
        parts.append(hint)
    if context_text and _looks_like_iac(context_text):
        parts.append(_IAC_HINT)
    if len(chat_messages) > 1:
        parts.append("Conversation so far:")
        for msg in chat_messages[:-1]:
            parts.append(f"{msg['role'].upper()}: {msg['content']}")
    parts.append(f"Platform: {platform.upper()}")
    if context_text:
        parts.append(f"Document context:\n{context_text}")
    parts.append(f"Request:\n{chat_messages[-1]['content']}")
    return "\n".join(parts)


def _generate_ir_with_corrections(
    *,
    ir_type: type,
    system_prompt: str,
    human_content: str,
    provider_key: str,
    logger: "StepLogger | None",
    attachment_documents: list[dict] | None,
    llm: LlmPort,
    view_label: str,
    view_key: str = "",
):
    """Ask the LLM for an IR and correct it until it satisfies its own rules.

    Shared by Network and every corridor view — the two validation layers this
    loop bridges are identical regardless of shape:

    * **Pydantic** rejects structurally impossible output (bad id, empty
      corridor, a style string in ``shape_type``, an unrecognized corridor
      vocabulary). ``call_llm`` already retries these internally with the JSON
      schema attached.
    * **``semantic_errors()``** catches cross-field rules Pydantic cannot
      express — chiefly a flow referencing an unknown id or (for Network) one
      touching a cross-cutting group. Those are corrected here, because the
      error message names the exact offending flow and can be handed straight
      back to the model.
    """
    ir = llm(system_prompt, human_content, provider_key, ir_type, logger, attachment_documents)

    for _attempt in range(IR_MAX_CORRECTIVE_PASSES):
        errors = ir.semantic_errors()
        if not errors:
            break
        if logger:
            logger.log(
                "ir", f"{view_label} IR broke {len(errors)} rule(s) — asking for a correction",
                level="warning", details={"errors": errors},
            )
        # The previous IR must be included verbatim: without it, "keep
        # everything else identical" refers to output the model cannot see,
        # and at temperature 0 it just regenerates the same broken answer —
        # live captures showed two correction passes failing identically
        # (same empty `flows`) for exactly this reason.
        correction = (
            f"Your previous {ir_type.__name__} was structurally valid but broke these rules:\n"
            + "\n".join(f"- {e}" for e in errors)
            + f"\n\nYour previous {ir_type.__name__} was:\n{ir.model_dump_json()}"
            + f"\n\nReturn a corrected {ir_type.__name__}. Keep everything else identical — "
            "change only what is needed to fix the listed problems."
        )
        ir = llm(
            system_prompt, human_content + "\n\n" + correction,
            provider_key, ir_type, logger, attachment_documents,
        )

    residual = ir.semantic_errors()
    if residual:
        _drop_invalid_flows(ir, residual, logger, view_label)
        residual = ir.semantic_errors()

    # Empty flows survive drop (nothing to drop) and burn LLM corrections without
    # changing — synthesize a minimal spine so the IR+compiler path still wins.
    if residual and _is_empty_flows_error(residual):
        from app.domain.ir.flow_spine import synthesize_spine_flows

        if synthesize_spine_flows(ir):
            residual = ir.semantic_errors()
            if logger and not residual:
                logger.log(
                    "ir",
                    f"{view_label} IR had empty flows — synthesized a request-path spine "
                    f"({len(ir.flows)} edge(s)) to keep the compiler path",
                    level="warning",
                    details={"flows": len(ir.flows)},
                )

    if not residual:
        governance_warnings: list[str] = []
        if view_key:
            try:
                rules = list_governance_rules(view_key, enabled_only=True)
                if rules:
                    governance_warnings = findings_to_warnings(evaluate_governance_rules(ir, rules))
            except Exception:
                pass  # governance is best-effort — never a reason a diagram fails to generate
        try:
            governance_warnings += find_saas_vendor_mismatches(ir)
        except Exception:
            pass  # same best-effort contract — a bad catalog file never breaks generation
        ir = _apply_semantic_warning_pass(
            ir, ir_type=ir_type, system_prompt=system_prompt, human_content=human_content,
            provider_key=provider_key, logger=logger, attachment_documents=attachment_documents,
            llm=llm, view_label=view_label, extra_warnings=governance_warnings,
        )

    if logger:
        if residual:
            # Not fixed by correction, drop, or spine synthesis — compiler will
            # reject and the pipeline fails the request (no direct-XML fallback).
            logger.log(
                "ir", f"{view_label} IR still breaks {len(residual)} rule(s) after correction — "
                "compiler will reject this view (IR-only; no direct-XML fallback)",
                level="warning", details={"errors": residual},
            )
        else:
            logger.log("ir", f"{view_label} IR generated", level="success", details={
                "leaves": len(ir.leaf_ids()),
                "flows": len(ir.flows),
                "actors": len(ir.external_actors),
            })
    return ir


def _is_empty_flows_error(errors: list[str]) -> bool:
    """True when the only residual problem is a missing ``flows`` array."""
    if not errors:
        return False
    return all("flows is empty" in e for e in errors)


def _apply_semantic_warning_pass(
    ir,
    *,
    ir_type: type,
    system_prompt: str,
    human_content: str,
    provider_key: str,
    logger: "StepLogger | None",
    attachment_documents: list[dict] | None,
    llm: LlmPort,
    view_label: str,
    extra_warnings: list[str] | None = None,
):
    """One best-effort, non-blocking correction for ``ir.semantic_warnings()``
    plus any org-governance findings the caller already evaluated
    (``extra_warnings`` — see ``_generate_ir_with_corrections``).

    Unlike the ``semantic_errors()`` loop above, this never gates anything: a
    warning is a judgment call (a required-companion service that's probably
    missing, a boundary flow with no stated protection, an org house-style
    rule), not a structural defect, so there is no retry loop and no
    rejection — one corrective ask, and whatever comes back is used only if
    it still passes ``semantic_errors()``. A missing DLQ (or a house-style
    rule) must never be the reason a view falls back to direct XML.
    """
    warnings = list(ir.semantic_warnings()) + list(extra_warnings or [])
    if not warnings:
        return ir
    if logger:
        logger.log(
            "ir", f"{view_label} IR has {len(warnings)} judgment-call warning(s) — "
            "asking for one non-blocking correction",
            level="warning", details={"warnings": warnings},
        )
    correction = (
        f"Your {ir_type.__name__} passes all hard validation rules, but a reviewer flagged "
        "these judgment calls:\n"
        + "\n".join(f"- {w}" for w in warnings)
        + f"\n\nReturn an improved {ir_type.__name__} addressing whichever of these genuinely "
        "apply to this design. Keep everything else identical. If a flagged item doesn't "
        "actually fit this request, you may leave it as is."
    )
    try:
        corrected = llm(
            system_prompt, human_content + "\n\n" + correction,
            provider_key, ir_type, logger, attachment_documents,
        )
    except Exception as exc:
        if logger:
            logger.log("ir", f"{view_label} warning-correction pass failed, keeping original: {exc}", level="warning")
        return ir
    if corrected.semantic_errors():
        # The "fix" broke a hard rule — worse than the warning it was meant to
        # address. Keep the original rather than trade a soft issue for a hard one.
        return ir
    return corrected


def _drop_invalid_flows(ir, residual_errors: list[str], logger: "StepLogger | None", view_label: str) -> None:
    """Last-resort residual cleanup: drop flows that never satisfied validation.

    Applies to every IR type, Network included — dangling references and
    self-loops are universal error classes every ``semantic_errors()``
    implementation can produce. Network additionally forbids flows touching a
    cross-cutting group (``no_edge_ids``); corridor views have no such
    concept, so that check is a no-op for them via ``getattr``'s default.
    The diagram is far more useful missing a couple of edges than not
    rendering at all, and deletion is the one residual-error class that is
    always safe to resolve this way.
    """
    known = ir.all_ids()
    forbidden = ir.no_edge_ids() if hasattr(ir, "no_edge_ids") else set()
    kept = [
        f for f in ir.flows
        if f.source in known and f.target in known
        and f.source != f.target
        and f.source not in forbidden and f.target not in forbidden
    ]
    dropped = len(ir.flows) - len(kept)
    ir.flows = kept
    if dropped and logger:
        logger.log(
            "ir", f"Dropped {dropped} invalid flow(s) from {view_label} after correction passes",
            level="warning", details={"errors": residual_errors},
        )


def _build_plain_ir_human_content(
    *,
    chat_messages: list[dict[str, str]],
    applied_suggestions: list[str] | None,
) -> str:
    """Human message for the two non-cloud IR generators — generic (as-is image
    replication) and simple (fresh flowcharts/charts).

    No platform or image_replicate hint, unlike the cloud IR generators: each of
    these paths does exactly one thing, and its own pack (``generic.txt`` /
    ``simple.txt``) already carries the whole instruction, so there is no
    cloud-vs-as-is branch left to disambiguate here.
    """
    parts: list[str] = []
    applied_block = build_applied_suggestions_block(applied_suggestions)
    if applied_block:
        parts.append(applied_block)
    if len(chat_messages) > 1:
        parts.append("Conversation so far:")
        for msg in chat_messages[:-1]:
            parts.append(f"{msg['role'].upper()}: {msg['content']}")
    parts.append(f"Request:\n{chat_messages[-1]['content']}")
    return "\n".join(parts)


def _drop_invalid_generic_edges(
    ir: GenericDiagramIR, residual_errors: list[str], logger: "StepLogger | None",
) -> None:
    """Last-resort residual cleanup, mirroring ``_drop_invalid_flows``: a
    diagram missing an edge is far more useful than not rendering at all."""
    known = ir.all_ids()
    kept = [e for e in ir.edges if e.source in known and e.target in known and e.source != e.target]
    dropped = len(ir.edges) - len(kept)
    ir.edges = kept
    if dropped and logger:
        logger.log(
            "ir", f"Dropped {dropped} invalid edge(s) from Generic diagram after correction passes",
            level="warning", details={"errors": residual_errors},
        )


def _generate_generic_ir_with_corrections(
    *,
    system_prompt: str,
    human_content: str,
    provider_key: str,
    logger: "StepLogger | None",
    attachment_documents: list[dict] | None,
    llm: LlmPort,
) -> GenericDiagramIR:
    """Slim counterpart to ``_generate_ir_with_corrections`` for ``GenericDiagramIR``.

    Generic diagrams have no flow-spine/semantic-warning machinery (those are
    cloud-story concepts that don't apply to an as-is image trace) — just one
    corrective retry against ``semantic_errors()``, then a last-resort edge
    drop if errors persist.
    """
    ir = llm(system_prompt, human_content, provider_key, GenericDiagramIR, logger, attachment_documents)

    for _attempt in range(IR_MAX_CORRECTIVE_PASSES):
        errors = ir.semantic_errors()
        if not errors:
            break
        if logger:
            logger.log(
                "ir", f"Generic IR broke {len(errors)} rule(s) — asking for a correction",
                level="warning", details={"errors": errors},
            )
        correction = (
            "Your previous GenericDiagramIR was structurally valid but broke these rules:\n"
            + "\n".join(f"- {e}" for e in errors)
            + f"\n\nYour previous GenericDiagramIR was:\n{ir.model_dump_json()}"
            + "\n\nReturn a corrected GenericDiagramIR. Keep everything else identical — "
            "change only what is needed to fix the listed problems."
        )
        ir = llm(
            system_prompt, human_content + "\n\n" + correction,
            provider_key, GenericDiagramIR, logger, attachment_documents,
        )

    residual = ir.semantic_errors()
    if residual:
        _drop_invalid_generic_edges(ir, residual, logger)
        residual = ir.semantic_errors()

    if logger:
        if residual:
            logger.log(
                "ir", f"Generic IR still breaks {len(residual)} rule(s) after correction — "
                "compiler will reject this diagram (IR-only; no direct-XML fallback)",
                level="warning", details={"errors": residual},
            )
        else:
            logger.log("ir", "Generic IR generated", level="success", details={
                "nodes": len(ir.nodes), "edges": len(ir.edges), "groups": len(ir.groups),
            })
    return ir


def generate_generic_diagram_from_ir(
    user_request: str,
    provider: str = "claude",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    custom_system_message: str = "",
    applied_suggestions: list[str] | None = None,
    attachment_documents: list[dict] | None = None,
    *,
    llm: LlmPort | None = None,
) -> XmlDiagram:
    """As-is image replication: ask the LLM for a ``GenericDiagramIR``, then
    compile it to draw.io XML.

    Sibling to ``generate_xml_diagram_from_ir`` — same return type, so the
    caller (``xml_pipeline._run_generic_image_pipeline``) can reuse the same
    structural-hygiene post-processing — but this path skips the cloud-specific
    enrichment steps (cost, Well-Architected, compliance, multi-view fan-out),
    since a generic diagram carries no corridor/platform concepts.

    It does NOT skip MCP icon resolution, contrary to what this docstring used
    to claim: ``GenericNode.icon_hint`` becomes a transient ``shape_type`` that
    ``_run_generic_image_pipeline`` resolves with ``platform="any"`` (narrowed
    by ``compiler/generic._resolvable_icon_hint`` so it can't overwrite a
    colour traced from the source image).
    """
    provider_key, chat_messages, hint, _llm, provider_spec = _prepare_ir_call(
        user_request=user_request, provider=provider, messages=messages, llm=llm,
    )
    system_prompt = get_generic_ir_system_prompt(
        model_id=provider_spec.default_model, provider=provider_key,
        custom_system_message=custom_system_message, user_hint=hint,
    )
    human_content = _build_plain_ir_human_content(
        chat_messages=chat_messages, applied_suggestions=applied_suggestions,
    )
    if logger:
        logger.log("prompt", "Prepared Generic IR request", details={"provider": provider_key})
    ir = _generate_generic_ir_with_corrections(
        system_prompt=system_prompt, human_content=human_content,
        provider_key=provider_key, logger=logger, attachment_documents=attachment_documents,
        llm=_llm,
    )
    xml = compile_generic_ir(ir)
    if logger:
        logger.log("ir_compile", "Compiled Generic IR to draw.io XML", level="success", details={
            "xml_chars": len(xml), "xml": xml,
        })
    return XmlDiagram(
        xml=xml,
        assistant_message=f"Reproduced {ir.title or 'diagram'} as-is.",
        suggestions=[],
        step_markers=[],
        ir_title=ir.title or "",
    )


def generate_network_ir(
    user_request: str,
    provider: str = "claude",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    custom_system_message: str = "",
    applied_suggestions: list[str] | None = None,
    context_text: str | None = None,
    attachment_documents: list[dict] | None = None,
    image_replicate: bool = False,
    *,
    llm: LlmPort | None = None,
) -> NetworkIR:
    """Ask the LLM for a NetworkIR and correct it until it satisfies its own rules."""
    provider_key, chat_messages, hint, _llm, provider_spec = _prepare_ir_call(
        user_request=user_request, provider=provider, messages=messages, llm=llm,
    )

    system_prompt = get_network_ir_system_prompt(
        model_id=provider_spec.default_model,
        provider=provider_key,
        platform=platform,
        custom_system_message=custom_system_message,
        user_hint=hint,
    )
    human_content = _build_ir_human_content(
        view="network", platform=platform, chat_messages=chat_messages,
        applied_suggestions=applied_suggestions, context_text=context_text,
        image_replicate=image_replicate,
    )
    if logger:
        logger.log("prompt", "Prepared Network IR request", details={
            "provider": provider_key, "platform": platform,
        })
    return _generate_ir_with_corrections(
        ir_type=NetworkIR, system_prompt=system_prompt, human_content=human_content,
        provider_key=provider_key, logger=logger, attachment_documents=attachment_documents,
        llm=_llm, view_label="Network", view_key="network",
    )


def generate_archimate_ir(
    user_request: str,
    provider: str = "claude",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    custom_system_message: str = "",
    applied_suggestions: list[str] | None = None,
    context_text: str | None = None,
    attachment_documents: list[dict] | None = None,
    image_replicate: bool = False,
    *,
    llm: LlmPort | None = None,
) -> ArchimateIR:
    """Ask the LLM for an ArchimateIR and correct it until it satisfies its own rules.

    A third sibling of ``generate_network_ir``/``generate_corridor_ir`` rather
    than a branch inside either: ArchiMate shares the corrective loop but
    neither the corridor prompt assembly nor Network's cloud packs.
    """
    provider_key, chat_messages, hint, _llm, provider_spec = _prepare_ir_call(
        user_request=user_request, provider=provider, messages=messages, llm=llm,
    )
    system_prompt = get_archimate_ir_system_prompt(
        model_id=provider_spec.default_model,
        provider=provider_key,
        platform=platform,
        custom_system_message=custom_system_message,
        user_hint=hint,
    )
    human_content = _build_ir_human_content(
        view="archimate", platform=platform, chat_messages=chat_messages,
        applied_suggestions=applied_suggestions, context_text=context_text,
        image_replicate=image_replicate,
    )
    if logger:
        logger.log("prompt", "Prepared ArchiMate IR request", details={
            "provider": provider_key, "platform": platform,
        })
    return _generate_ir_with_corrections(
        ir_type=ArchimateIR, system_prompt=system_prompt, human_content=human_content,
        provider_key=provider_key, logger=logger, attachment_documents=attachment_documents,
        llm=_llm, view_label="ArchiMate", view_key="archimate",
    )


def generate_corridor_ir(
    view: str,
    user_request: str,
    provider: str = "claude",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    custom_system_message: str = "",
    applied_suggestions: list[str] | None = None,
    context_text: str | None = None,
    attachment_documents: list[dict] | None = None,
    image_replicate: bool = False,
    *,
    llm: LlmPort | None = None,
):
    """Ask the LLM for a CorridorIR (Logical / Security / Data-flow).

    The Network-shaped twin of this function is ``generate_network_ir`` —
    both are thin wrappers around ``_generate_ir_with_corrections``; only the
    IR type and prompt assembly differ.
    """
    view_key = (view or "").strip().lower()
    ir_type = IR_TYPES_BY_VIEW.get(view_key)
    if ir_type is None or view_key == "network":
        raise ValueError(f"generate_corridor_ir: not a corridor view: {view!r}")

    provider_key, chat_messages, hint, _llm, provider_spec = _prepare_ir_call(
        user_request=user_request, provider=provider, messages=messages, llm=llm,
    )

    system_prompt = get_corridor_ir_system_prompt(
        view_key,
        model_id=provider_spec.default_model,
        provider=provider_key,
        platform=platform,
        custom_system_message=custom_system_message,
        user_hint=hint,
    )
    human_content = _build_ir_human_content(
        view=view_key, platform=platform, chat_messages=chat_messages,
        applied_suggestions=applied_suggestions, context_text=context_text,
        image_replicate=image_replicate,
    )
    if logger:
        logger.log("prompt", f"Prepared {_IR_PAGE_NAMES[view_key]} IR request", details={
            "provider": provider_key, "platform": platform,
        })
    return _generate_ir_with_corrections(
        ir_type=ir_type, system_prompt=system_prompt, human_content=human_content,
        provider_key=provider_key, logger=logger, attachment_documents=attachment_documents,
        llm=_llm, view_label=_IR_PAGE_NAMES[view_key], view_key=view_key,
    )


def _ir_inventory_lines(ir: object, *, limit: int = 40) -> list[str]:
    """Short label inventory from any view's IR, for suggestion prompts.

    Written entirely with ``getattr`` defaults so a new IR shape contributes
    whatever it has and skips the rest, rather than needing a branch per view.
    """
    lines: list[str] = []
    title = getattr(ir, "title", "") or ""
    if title:
        lines.append(f"Title: {title}")
    # ArchiMate elements — named `name`, not `label`, since they are model
    # elements rather than icons.
    for element in getattr(ir, "elements", None) or []:
        lab = getattr(element, "name", "") or getattr(element, "id", "")
        if lab:
            lines.append(lab)
    # Corridor leaves
    for corridor in getattr(ir, "corridors", None) or []:
        for leaf in getattr(corridor, "leaves", None) or []:
            lab = getattr(leaf, "label", "") or getattr(leaf, "id", "")
            if lab:
                lines.append(lab)
    for shelf in getattr(ir, "anchored", None) or []:
        for leaf in getattr(shelf, "leaves", None) or []:
            lab = getattr(leaf, "label", "") or getattr(leaf, "id", "")
            if lab:
                lines.append(lab)
    # Flat node graphs (Simple/Generic) — a plain list, not a hierarchy.
    for node in getattr(ir, "nodes", None) or []:
        lab = getattr(node, "label", "") or getattr(node, "id", "")
        if lab:
            lines.append(lab)
    # Chart kinds have no nodes at all; their content is the axis vocabulary,
    # which is what a follow-up suggestion would actually talk about ("split
    # Q4 by region", "add a 2027 series").
    for category in getattr(ir, "categories", None) or []:
        if category:
            lines.append(f"Category: {category}")
    for series in getattr(ir, "series", None) or []:
        lab = getattr(series, "label", "")
        if lab:
            lines.append(f"Series: {lab}")
    for task in getattr(ir, "tasks", None) or []:
        lab = getattr(task, "label", "")
        if lab:
            lines.append(f"Task: {lab}")
    # Network hierarchy — prefer the typed iterator when present.
    iter_leaves = getattr(ir, "iter_leaves", None)
    if callable(iter_leaves):
        for leaf in iter_leaves():
            lab = getattr(leaf, "label", "") or getattr(leaf, "id", "")
            if lab:
                lines.append(lab)
    else:
        cloud = getattr(ir, "cloud", None)
        if cloud is not None:
            for region in getattr(cloud, "regions", None) or []:
                for vpc in getattr(region, "vpcs", None) or []:
                    for zone in getattr(vpc, "zones", None) or []:
                        for subnet in getattr(zone, "subnets", None) or []:
                            for leaf in getattr(subnet, "leaves", None) or []:
                                lab = getattr(leaf, "label", "") or getattr(leaf, "id", "")
                                if lab:
                                    lines.append(lab)
                    for group in getattr(vpc, "groups", None) or []:
                        for leaf in getattr(group, "leaves", None) or []:
                            lab = getattr(leaf, "label", "") or getattr(leaf, "id", "")
                            if lab:
                                lines.append(lab)
            edge = getattr(cloud, "edge_layer", None)
            if edge is not None:
                for leaf in getattr(edge, "leaves", None) or []:
                    lab = getattr(leaf, "label", "") or getattr(leaf, "id", "")
                    if lab:
                        lines.append(lab)
    for actor in getattr(ir, "external_actors", None) or []:
        lab = getattr(actor, "label", "") or getattr(actor, "id", "")
        if lab:
            lines.append(f"Actor: {lab}")
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _xml_labels_inventory(xml: str, *, limit: int = 40) -> list[str]:
    """Short label inventory from raw draw.io XML — the surgical-edit path's
    equivalent of ``_ir_inventory_lines`` (which needs a typed IR object the
    edit path never builds one of)."""
    import xml.etree.ElementTree as ET

    from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document

    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", "Architecture"))
    except ET.ParseError:
        return []
    lines: list[str] = []
    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        if cid in ("overview_panel", "legend_box") or cid.startswith("step_badge_"):
            continue
        style = cell.get("style") or ""
        if "container=1" in style or "swimlane" in style:
            continue
        label = (cell.get("value") or "").strip()
        if label:
            lines.append(label)
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _generate_followup_suggestions(
    *,
    inventory: list[str],
    view: str,
    user_request: str,
    provider_key: str,
    llm: LlmPort,
    logger: "StepLogger | None",
    applied_suggestions: list[str] | None,
) -> list[DiagramSuggestion]:
    """Ask for follow-up suggestions from a plain label inventory — shared by
    every generation path (IR, surgical edit) so "suggestions on" behaves the
    same regardless of which path produced the diagram, instead of each path
    reimplementing its own version with its own reliability. Callers build
    ``inventory`` however fits their own IR/XML shape (see
    ``_ir_inventory_lines`` for the IR-typed case).
    """
    applied_block = build_applied_suggestions_block(applied_suggestions)
    human = "\n".join([
        build_suggestions_instruction_block(True),
        "",
        f"Primary view: {view}",
        f"User request: {(user_request or '')[:800]}",
        "Architecture inventory (labels):",
        *[f"- {line}" for line in inventory],
        applied_block,
    ]).strip()
    # Two attempts total — retries BOTH a raised exception AND a technically
    # successful call that ignores "never leave suggestions empty" and returns
    # zero items. The user-visible symptom (nothing in the panel) is identical
    # either way, so both are worth one retry before giving up.
    sugg: list[DiagramSuggestion] = []
    last_exc: Exception | None = None
    for _attempt in range(2):
        try:
            parsed = llm(
                _SUGGESTIONS_SYSTEM,
                human,
                provider_key,
                _FollowupSuggestions,
                logger,
                None,
            )
            last_exc = None
        except Exception as exc:
            last_exc = exc
            parsed = None
        sugg = list(parsed.suggestions or []) if parsed is not None else []
        if sugg:
            break

    if not sugg:
        if logger:
            # details={"degraded": ...} is what makes this surface as a UI toast
            # via xml_pipeline._collect_degraded_features — without it, either
            # failure mode (a raised exception, or a "successful" empty
            # response) is completely invisible: status stays "success",
            # suggestions is just an empty list, indistinguishable from the
            # LLM legitimately declining to suggest anything.
            reason = str(last_exc) if last_exc is not None else "LLM returned an empty suggestions list"
            logger.log(
                "suggestions",
                f"Follow-up suggestions empty after retry: {reason}",
                level="warning",
                details={"degraded": True, "feature": "suggestions", "reason": reason},
            )
        return []

    if logger:
        logger.log("suggestions", f"Generated {len(sugg)} follow-up suggestion(s)", level="success")
    return sugg


def generate_followup_suggestions_for_xml(
    xml: str,
    *,
    view: str = "network",
    user_request: str = "",
    provider: str = "claude",
    applied_suggestions: list[str] | None = None,
    logger: "StepLogger | None" = None,
) -> list[DiagramSuggestion]:
    """Public entry point for re-running suggestions against an
    already-generated diagram — the ``/analyze`` route's suggestions
    counterpart to ``assess_well_architected``/``assess_compliance``. Turning
    the Suggestions toggle on after a diagram already exists should show
    results immediately instead of only taking effect on the next chat turn,
    same reasoning as the WAF/Compliance re-check.

    Builds the inventory straight from raw XML (``_xml_labels_inventory`` —
    the surgical-edit path's own approach, since there is no typed IR object
    here either) and routes through the same hardened
    ``_generate_followup_suggestions`` every other path already shares.
    """
    provider_key = normalize_provider_key(provider)
    return _generate_followup_suggestions(
        inventory=_xml_labels_inventory(xml),
        view=view or "network",
        user_request=user_request,
        provider_key=provider_key,
        llm=get_llm(),
        logger=logger,
        applied_suggestions=applied_suggestions,
    )


def generate_xml_diagram_from_ir(
    view: str,
    user_request: str,
    provider: str = "claude",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    custom_system_message: str = "",
    applied_suggestions: list[str] | None = None,
    context_text: str | None = None,
    attachment_documents: list[dict] | None = None,
    image_replicate: bool = False,
    include_suggestions: bool = False,
    animated_connectors: bool = False,
    include_governance_report: bool = False,
    *,
    llm: LlmPort | None = None,
) -> XmlDiagram:
    """IR path for any view: generate its IR, then compile it to draw.io XML.

    Returns the same ``XmlDiagram`` the direct-XML path returns, so everything
    downstream in the pipeline (validation, MCP icon resolution, cost/WAF/
    compliance enrichment) is completely unaffected by which path — or which
    view — produced it. ``animated_connectors`` only reaches the compiler
    here — the IR-generation LLM call never sees it, since this path's
    styling is entirely compiler-owned (see ``theme.flow_style``), unlike the
    direct-XML path where it's a prompt instruction.

    ``include_governance_report`` gates only the *final findings* attached to
    the returned diagram for display — matching cost_estimate/well_architected/
    compliance's opt-in shape. It does not touch the earlier, always-on
    governance pass inside ``_generate_ir_with_corrections`` (house-style
    warnings feeding a corrective re-ask before the IR is finalized): that is
    a generation-quality mechanism, not a report, and turning the report off
    should not turn off a free quality improvement nobody asked to lose.
    """
    view_key = (view or "network").strip().lower()
    kwargs = dict(
        provider=provider, platform=platform, logger=logger, messages=messages,
        custom_system_message=custom_system_message, applied_suggestions=applied_suggestions,
        context_text=context_text, attachment_documents=attachment_documents,
        image_replicate=image_replicate, llm=llm,
    )
    if view_key == "network":
        ir = generate_network_ir(user_request, **kwargs)
        xml = compile_network_ir(ir, animated_connectors=animated_connectors)
    elif view_key == "archimate":
        ir = generate_archimate_ir(user_request, **kwargs)
        xml = compile_archimate_ir(ir, page_name=_IR_PAGE_NAMES[view_key])
    else:
        ir = generate_corridor_ir(view_key, user_request, **kwargs)
        xml = compile_corridor_ir(ir, page_name=_IR_PAGE_NAMES[view_key], animated_connectors=animated_connectors)

    sugg: list[DiagramSuggestion] = []
    if include_suggestions:
        provider_key = normalize_provider_key(provider)
        sugg = _generate_followup_suggestions(
            inventory=_ir_inventory_lines(ir),
            view=view_key,
            user_request=user_request,
            provider_key=provider_key,
            llm=llm or get_llm(),
            logger=logger,
            applied_suggestions=applied_suggestions,
        )

    if logger:
        logger.log("ir_compile", f"Compiled {_IR_PAGE_NAMES[view_key]} IR to draw.io XML", level="success", details={
            "xml_chars": len(xml), "xml": xml,
        })

    # Final conformance report — evaluated fresh against the finished IR
    # (after any semantic-warning corrective pass), not reused from the
    # pre-correction check that fed the corrective prompt, so the report the
    # user sees always reflects what actually got compiled. Skipped entirely
    # when the caller didn't ask for it, same as cost/WAF/compliance — the
    # rule check itself is cheap, but the point is request-shaped symmetry:
    # a client that turned the report off shouldn't receive one anyway.
    governance_findings = []
    if include_governance_report:
        try:
            rules = list_governance_rules(view_key, enabled_only=True)
            if rules:
                governance_findings = evaluate_governance_rules(ir, rules)
        except Exception:
            governance_findings = []  # governance is best-effort — never a reason a diagram fails
    if logger and governance_findings:
        failed = [f for f in governance_findings if not f.passed]
        logger.log(
            "governance",
            f"{len(governance_findings)} house-style rule(s) checked, {len(failed)} failed"
            if failed else f"{len(governance_findings)} house-style rule(s) checked, all passed",
            level="warning" if failed else "success",
            details={"failed": [f.model_dump() for f in failed]},
        )

    return XmlDiagram(
        xml=xml,
        assistant_message=f"Generated {ir.title or _IR_PAGE_NAMES[view_key]} diagram.",
        suggestions=sugg,
        step_markers=[],
        ir_title=ir.title or "",
        design_tradeoffs=list(ir.design_tradeoffs),
        governance_findings=governance_findings,
    )


#: Mirrors the upper end of ``config/xml_gen/ir/simple.txt``'s depth table —
#: update both together. Raised from 8 when that table replaced a flat cap:
#: the old figure was suppressing detail on exactly the requests ("end to end",
#: "detailed") that want it, and warning about the intended result is noise.
#: A logged warning rather than a semantic error, since a genuinely large
#: process is a legitimate answer, not a failure.
_SIMPLE_SOFT_NODE_CAP = 20


def _drop_invalid_simple_edges(
    ir: SimpleDiagramIR, residual_errors: list[str], logger: "StepLogger | None",
) -> None:
    """Last-resort residual cleanup, mirroring ``_drop_invalid_generic_edges``:
    a diagram missing one arrow beats a diagram that doesn't render."""
    known = ir.all_ids()
    kept = [e for e in ir.edges if e.source in known and e.target in known]
    dropped = len(ir.edges) - len(kept)
    ir.edges = kept
    if dropped and logger:
        logger.log(
            "ir", f"Dropped {dropped} invalid edge(s) from Simple diagram after correction passes",
            level="warning", details={"errors": residual_errors},
        )


def _generate_simple_ir_with_corrections(
    *,
    system_prompt: str,
    human_content: str,
    provider_key: str,
    logger: "StepLogger | None",
    llm: LlmPort,
) -> SimpleDiagramIR:
    """One corrective retry against ``semantic_errors()``, then a last-resort
    edge drop — the same slim loop ``_generate_generic_ir_with_corrections``
    runs, and for the same reason: this path has no flow-spine or
    semantic-warning machinery, since those encode cloud-architecture story
    rules that mean nothing to a flowchart or a bar chart.

    The corrective pass earns its place here more than on most views: the
    schema is a discriminated union, so the one mistake worth catching is a
    model that picks ``kind="bar"`` and then fills in ``nodes``, which
    ``semantic_errors`` reports in exactly the words needed to fix it.
    """
    ir = llm(system_prompt, human_content, provider_key, SimpleDiagramIR, logger, None)

    for _attempt in range(IR_MAX_CORRECTIVE_PASSES):
        errors = ir.semantic_errors()
        if not errors:
            break
        if logger:
            logger.log(
                "ir", f"Simple IR broke {len(errors)} rule(s) — asking for a correction",
                level="warning", details={"errors": errors},
            )
        correction = (
            "Your previous SimpleDiagramIR was structurally valid but broke these rules:\n"
            + "\n".join(f"- {e}" for e in errors)
            + f"\n\nYour previous SimpleDiagramIR was:\n{ir.model_dump_json()}"
            + "\n\nReturn a corrected SimpleDiagramIR. Keep everything else identical — "
            "change only what is needed to fix the listed problems."
        )
        ir = llm(system_prompt, human_content + "\n\n" + correction, provider_key, SimpleDiagramIR, logger, None)

    residual = ir.semantic_errors()
    if residual and ir.kind == "flow":
        # Only flows have a salvageable failure mode. A chart whose series
        # don't line up with its categories has no equivalent "drop the bad
        # part" move — silently truncating data is worse than not drawing it.
        _drop_invalid_simple_edges(ir, residual, logger)
        residual = ir.semantic_errors()

    if logger:
        if residual:
            logger.log(
                "ir", f"Simple IR still breaks {len(residual)} rule(s) after correction — "
                "compiler will reject this diagram (IR-only; no direct-XML fallback)",
                level="warning", details={"errors": residual},
            )
        else:
            logger.log("ir", "Simple IR generated", level="success", details={
                "kind": ir.kind, "nodes": len(ir.nodes), "edges": len(ir.edges),
                "categories": len(ir.categories), "series": len(ir.series), "tasks": len(ir.tasks),
            })
        for warning in ir.semantic_warnings():
            logger.log("ir", warning, level="warning")
        if ir.kind == "flow" and len(ir.nodes) > _SIMPLE_SOFT_NODE_CAP:
            logger.log(
                "ir",
                f"{len(ir.nodes)} nodes exceeds the pack's soft cap of {_SIMPLE_SOFT_NODE_CAP} — "
                "fine if the request asked for more, otherwise the diagram may be denser "
                "than a glanceable one should be.",
                level="warning",
                details={"node_count": len(ir.nodes), "soft_cap": _SIMPLE_SOFT_NODE_CAP},
            )
    return ir


def _simple_revision_block(current_xml: str) -> str:
    """Context for revising a diagram that is already on screen.

    The non-cloud path has no surgical-edit mode and should not get one: on a
    chart, every mark's geometry is *derived* from the data (a bar's height is
    its value), so patching cells individually can only produce a drawing that
    disagrees with its own labels — which is exactly what the cloud edit path
    did to a bar chart before this existed. Regenerating the whole IR is both
    correct and cheap at these sizes.

    What regeneration needs is the current content, since the conversation
    alone is lossy: a diagram built over several turns is not reconstructable
    from the user's first message. The label inventory carries it — including,
    for charts, the plotted numbers, because the compiler writes a direct value
    label on every bar and slice.
    """
    labels = _xml_labels_inventory(current_xml)
    if not labels:
        return ""
    return (
        "You are revising a diagram that already exists. Its current contents are:\n"
        + "\n".join(f"- {label}" for label in labels)
        + "\n\nApply only the change the user asked for and carry everything else "
        "over unchanged, including the diagram kind, the labels you were not asked "
        "to touch, and any numbers you were not asked to touch."
    )


def generate_simple_diagram_from_ir(
    user_request: str,
    provider: str = "claude",
    logger: StepLogger | None = None,
    messages: list[dict[str, str]] | None = None,
    applied_suggestions: list[str] | None = None,
    include_suggestions: bool = False,
    custom_system_message: str = "",
    current_xml: str = "",
    *,
    llm: LlmPort | None = None,
) -> XmlDiagram:
    """Non-cloud generation: ask for a ``SimpleDiagramIR``, compile it to draw.io XML.

    Replaces the old Mermaid fast path, which could never work: it handed the
    draw.io embed ``{action: "import", format: "mermaid"}``, and the embed has
    no ``import`` action — it answered ``{error: "unknownMessage"}`` and drew
    nothing, for every simple diagram this app ever generated. The embed
    accepts mxGraph XML and nothing else, so the non-cloud path now produces
    XML the same way every other path does: LLM emits IR, compiler renders it.

    Returns the same ``XmlDiagram`` as the cloud and generic paths so callers
    need no special case, but the *pipeline* around it stays deliberately thin
    (see ``xml_pipeline._run_simple_pipeline``) — cost, Well-Architected,
    compliance, multi-view fan-out and MCP icon resolution are all cloud
    concepts with nothing to say about a flowchart or a pie chart.
    """
    provider_key, chat_messages, hint, _llm, provider_spec = _prepare_ir_call(
        user_request=user_request, provider=provider, messages=messages, llm=llm,
    )
    system_prompt = get_simple_ir_system_prompt(
        model_id=provider_spec.default_model, provider=provider_key,
        custom_system_message=custom_system_message, user_hint=hint,
    )
    human_content = _build_plain_ir_human_content(
        chat_messages=chat_messages, applied_suggestions=applied_suggestions,
    )
    revision = _simple_revision_block(current_xml)
    if revision:
        human_content = f"{revision}\n\n{human_content}"
    if logger:
        logger.log(
            "prompt", "Prepared Simple IR request",
            details={
                "provider": provider_key,
                "turns": len(chat_messages),
                "revising": bool(revision),
            },
        )
    ir = _generate_simple_ir_with_corrections(
        system_prompt=system_prompt, human_content=human_content,
        provider_key=provider_key, logger=logger, llm=_llm,
    )
    xml = compile_simple_ir(ir)

    sugg: list[DiagramSuggestion] = []
    if include_suggestions:
        sugg = _generate_followup_suggestions(
            inventory=_ir_inventory_lines(ir),
            # "logical" is the closest of the suggestion prompt's view
            # vocabularies to a plain diagram — none of them describes a chart,
            # and inventing a sixth view id here would mean teaching the
            # suggestion pack a view that no other caller ever passes.
            view="logical",
            user_request=hint,
            provider_key=provider_key,
            llm=_llm,
            logger=logger,
            applied_suggestions=applied_suggestions,
        )

    if logger:
        logger.log("ir_compile", "Compiled Simple IR to draw.io XML", level="success", details={
            "kind": ir.kind, "xml_chars": len(xml), "xml": xml,
        })
    return XmlDiagram(
        xml=xml,
        assistant_message=ir.assistant_message or f"Generated {ir.title or 'diagram'}.",
        suggestions=sugg,
        step_markers=[],
        ir_title=ir.title or "",
    )


def run_visual_qa_llm(
    image_b64: str,
    xml: str,
    provider: str,
    logger: "StepLogger | None" = None,
) -> VisualQaResult:
    """Screenshot the rendered diagram, ask a vision-capable model to critique it.

    Distinct from ``diagram_vlm_qa.py``'s pure-XML geometry linter — this makes
    a real multimodal LLM call against the rendered PNG, reusing the same
    image-attachment vision path (``_build_human_message``) that file-upload
    vision already goes through, via the same fallback-wrapped ``get_llm()``
    port every other generation call in this module uses.
    """
    provider_key = normalize_provider_key(provider)
    human_content = f"Diagram XML (for reference only — inspect the image):\n{xml[:20000]}"
    attachment = [{"filename": "diagram.png", "mime": "image/png", "data": image_b64}]
    if logger:
        logger.log("prompt", "Prepared visual QA request", details={"provider": provider_key})
    result = get_llm()(VLM_QA_SYSTEM_PROMPT, human_content, provider_key, VisualQaResult, logger, attachment)
    if logger:
        logger.log(
            "vlm_qa",
            "Visual QA complete",
            level="success" if result.passed else "warning",
            details={"issues": len(result.issues), "passed": result.passed},
        )
    return result
