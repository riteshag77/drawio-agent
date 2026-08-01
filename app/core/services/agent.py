"""Public LLM orchestration API — XML + Mermaid diagram generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.outbound.diagram.compiler import compile_corridor_ir, compile_network_ir
from app.adapters.outbound.diagram.diagram_edits import apply_diagram_edits, looks_like_surgical_edit
from app.adapters.outbound.llm.prompt_builders import build_chat_prompt
from app.adapters.outbound.utils.chat import (
    build_applied_suggestions_block,
    build_suggestions_instruction_block,
)
from app.adapters.outbound.llm.prompts.ir import get_corridor_ir_system_prompt, get_network_ir_system_prompt
from app.adapters.outbound.llm.prompts.simple import MERMAID_SYSTEM_PROMPT
from app.adapters.outbound.llm.prompts.system import get_xml_gen_system_prompt_marked
from app.adapters.outbound.llm.providers import get_provider, normalize_provider_key
from app.adapters.outbound.llm.response_parse import extract_mermaid
from app.core.ports.llm import LlmPort, get_llm
from app.domain.entities import DiagramEditPlan, MermaidDiagram, XmlDiagram
from app.domain.ir import IR_TYPES_BY_VIEW, NetworkIR

if TYPE_CHECKING:
    from app.core.logging import StepLogger

_EDIT_PLAN_SYSTEM = """You plan surgical edits to an existing draw.io architecture diagram.
Given the user request and a summary of current cell ids/labels, output a DiagramEditPlan.
Set applicable=true ONLY if a small set of ops fully satisfies the request (add/remove/connect/move/relabel).
Set applicable=false for redesigns, layout overhauls, or when you are unsure — the system will regenerate.
Never invent ids that must already exist unless op is add_node (then provide a new id).
For add_node, include shape_type for leaf services and a reasonable parent id from the diagram.
When suggestions are requested, always fill `suggestions` with 2–3 concrete follow-up improvements.
"""

_IMAGE_REPLICATE_HINT = (
    "## Image / PDF replicate mode\n"
    "The user attached diagram screenshot(s) or architecture PDF pages. "
    "Preserve the visible topology, grouping, and flows as faithfully as possible. "
    "Map each recognizable cloud service to the correct shape_type; use grey placeholders "
    "for unknowns. Prefer fidelity to the attachment over inventing new services."
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
    """Generate draw.io XML directly (optional surgical edit when current_xml + small change)."""
    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]
    hint = user_request or (chat_messages[-1]["content"] if chat_messages else "")
    _llm = llm or get_llm()

    # --- Surgical path (small edits only; still returns XML, never JSON architecture) ---
    if (
        current_xml
        and len(current_xml) > 200
        and not retry_hint
        and looks_like_surgical_edit(hint, has_current_xml=True)
    ):
        try:
            plan = generate_edit_plan(
                hint, current_xml, provider=provider_key, platform=platform,
                logger=logger, messages=chat_messages,
                include_suggestions=include_suggestions, llm=_llm,
            )
            if plan.applicable and plan.ops:
                patched = apply_diagram_edits(current_xml, plan)
                if logger:
                    logger.log(
                        "surgical_edit",
                        f"Applied {len(plan.ops)} surgical ops",
                        level="success",
                        details={"ops": [o.op for o in plan.ops]},
                    )
                sugg = (plan.suggestions or []) if include_suggestions else []
                return XmlDiagram(
                    xml=patched,
                    assistant_message=plan.assistant_message or "Updated diagram.",
                    suggestions=sugg,
                )
            if logger:
                logger.log("surgical_edit", "Edit plan not applicable — falling back", level="info")
        except Exception as exc:
            if logger:
                logger.log("surgical_edit", f"Surgical edit failed ({exc}) — falling back", level="warn")

    # --- Direct XML path ---
    provider_spec = get_provider(provider_key)
    system_prompt = get_xml_gen_system_prompt_marked(
        model_id=provider_spec.default_model,
        provider=provider_key,
        platform=platform,
        custom_system_message=custom_system_message,
        three_d=three_d,
        user_hint=hint,
        primary_view=primary_view,
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
        logger.log("prompt", "Prepared XML generation request", details={"provider": provider_key, "platform": platform})

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


#: How many corrective passes to spend on an IR that parsed but broke a semantic
#: rule. A single-flow-reference error is usually fixed in one pass: the
#: feedback names the exact offending flow/field, so the model has something
#: actionable. "flows is empty" is a whole-view omission rather than a single
#: bad reference, though, and a live capture showed the model still returning
#: no flows after one correction attempt — the whole view then fell back to
#: the direct-XML path (visibly worse: no compiler-owned geometry) purely
#: because it only got one chance to add them back. Two passes costs one
#: extra LLM call only on the views that already broke a rule once.
IR_MAX_CORRECTIVE_PASSES = 2

#: view -> (page name, IR type, compiler) — the single dispatch table every
#: view-agnostic IR call site reads from. Adding a future view means adding one
#: row here, not a new if/elif chain at each call site.
_IR_PAGE_NAMES: dict[str, str] = {
    "network": "Network", "logical": "Logical",
    "security": "Security", "data_flow": "Data flow",
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
        parts.append(_IMAGE_REPLICATE_HINT)
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
        correction = (
            f"Your previous {ir_type.__name__} was structurally valid but broke these rules:\n"
            + "\n".join(f"- {e}" for e in errors)
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

    if logger:
        if residual:
            # Not fixed by correction or drop (e.g. "flows is empty" — there is
            # nothing to drop). The compiler will reject this IR and the caller
            # falls back to direct XML; logging success here would hide why.
            logger.log(
                "ir", f"{view_label} IR still breaks {len(residual)} rule(s) after correction — "
                "compiler will reject it and this view will fall back to direct XML",
                level="warning", details={"errors": residual},
            )
        else:
            logger.log("ir", f"{view_label} IR generated", level="success", details={
                "leaves": len(ir.leaf_ids()),
                "flows": len(ir.flows),
                "actors": len(ir.external_actors),
            })
    return ir


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
        llm=_llm, view_label="Network",
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
        llm=_llm, view_label=_IR_PAGE_NAMES[view_key],
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
    *,
    llm: LlmPort | None = None,
) -> XmlDiagram:
    """IR path for any view: generate its IR, then compile it to draw.io XML.

    Returns the same ``XmlDiagram`` the direct-XML path returns, so everything
    downstream in the pipeline (validation, MCP icon resolution, cost/WAF/
    compliance enrichment) is completely unaffected by which path — or which
    view — produced it.
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
        xml = compile_network_ir(ir)
    else:
        ir = generate_corridor_ir(view_key, user_request, **kwargs)
        xml = compile_corridor_ir(ir, page_name=_IR_PAGE_NAMES[view_key])

    if logger:
        logger.log("ir_compile", f"Compiled {_IR_PAGE_NAMES[view_key]} IR to draw.io XML", level="success", details={
            "xml_chars": len(xml), "xml": xml,
        })
    return XmlDiagram(
        xml=xml,
        assistant_message=f"Generated {ir.title or _IR_PAGE_NAMES[view_key]} diagram.",
        suggestions=[],
        step_markers=[],
        ir_title=ir.title or "",
    )


def generate_mermaid_diagram(
    user_request: str,
    provider: str = "claude",
    logger: StepLogger | None = None,
    messages: list[dict[str, str]] | None = None,
    applied_suggestions: list[str] | None = None,
    include_suggestions: bool = False,
) -> MermaidDiagram:
    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]

    if logger:
        logger.log(
            "prompt",
            "Prepared Mermaid request",
            details={"provider": provider_key, "turns": len(chat_messages)},
        )

    human_content = build_chat_prompt(
        chat_messages, "Generic (Mermaid)", None, None, None,
        applied_suggestions=applied_suggestions,
        include_suggestions=include_suggestions,
    )
    parsed = get_llm()(MERMAID_SYSTEM_PROMPT, human_content, provider_key, MermaidDiagram, logger, None)
    parsed.mermaid = extract_mermaid(parsed.mermaid)
    if not parsed.assistant_message:
        parsed.assistant_message = "Generated Mermaid diagram."
    if not include_suggestions:
        parsed.suggestions = []
    elif parsed.suggestions is None:
        parsed.suggestions = []

    if logger:
        logger.log(
            "mermaid",
            "Mermaid diagram generated",
            level="success",
            details={"chars": len(parsed.mermaid), "lines": parsed.mermaid.count(chr(10)) + 1},
        )
    return parsed
