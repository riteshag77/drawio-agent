"""MCP-accelerated XML generation pipeline — LLM produces draw.io XML directly."""

from __future__ import annotations

import asyncio
import inspect
import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.drawio_mcp_client import DrawioMcpError, create_diagram, resolve_shape_styles
from app.adapters.outbound.diagram.mode import is_xml_pipeline_mode
from app.adapters.outbound.diagram.xml_diagnostics import apply_step_markers, diagnose_xml, repair_xml
from app.adapters.outbound.diagram.xml_tools import (
    apply_mcp_styles,
    choose_fixed_xml,
    ensure_diagram_document,
    extract_shape_types,
    fit_containers_to_children,
    guarantee_loadable_xml,
    normalize_platform_styles,
    validate_and_fix_xml,
)
from app.adapters.outbound.llm.concurrency import run_llm_sync
from app.adapters.outbound.llm.pricing import display_currency
from app.adapters.outbound.llm.prompt_builders import PLATFORM_LABELS
from app.adapters.outbound.compliance import assess_compliance
from app.adapters.outbound.cost_estimator import estimate_monthly_cost
from app.adapters.outbound.utils.chat import filter_applied_suggestions
from app.adapters.outbound.well_architected import assess_well_architected
from app.adapters.outbound.diagram.architecture_docs import (
    AdrRecord,
    ArchitectureDocPack,
    RaidItem,
    SingleViewPage,
    VIEW_PAGE_NAMES,
    _gap_candidates,
    achievable_density_target,
    build_adr_page_xml,
    build_architecture_inventory,
    build_interview_prompt_block,
    build_raid_page_xml,
    build_single_view_human,
    build_single_view_system,
    build_views_prompt_block,
    ensure_minimal_overview_panel,
    load_docs_prompt_pack,
    merge_mxfile_pages,
    normalize_diagram_views,
    normalize_single_page_xml,
    page_has_overview_panel,
    pick_primary_view,
    inject_shape_types_from_primary,
)
from app.adapters.outbound.diagram.view_conformance import (
    format_violations_for_repair,
    score_view,
)
from app.adapters.outbound.diagram.view_fixers import apply_view_fixers, ensure_gap_nodes
from app.core.ports.llm import get_llm
from app.core.services.agent import generate_xml_diagram

_SERVICE_INVENTORY_LIMIT = 36
_REQUEST_TRUNCATE_DOCS = 600


def _was_successful_surgical_edit(logger: Any) -> bool:
    entries = getattr(logger, "entries", None) if logger else None
    if not entries:
        return False
    for entry in entries:
        if getattr(entry, "step", None) != "surgical_edit":
            continue
        level = (getattr(entry, "level", None) or "").lower()
        msg = (getattr(entry, "message", None) or "").lower()
        if level == "success" or msg.startswith("applied "):
            return True
    return False


def _docs_human(*, human_prompt: str, platform_key: str, xml_output: str, need: str) -> str:
    labels_block = build_architecture_inventory(
        xml_output, service_limit=_SERVICE_INVENTORY_LIMIT, request_text=human_prompt or "",
    )
    return (
        f"Platform: {platform_key}\n"
        f"User request: {human_prompt[:_REQUEST_TRUNCATE_DOCS]}\n"
        f"Architecture inventory:\n{labels_block}\n"
        f"Need: {need} only. Leave the other list empty.\n"
        "Include every material item this design implies. Business/layman wording for all fields."
    )


def _fallback_adrs() -> list[AdrRecord]:
    return [
        AdrRecord(
            title="Prefer multi-AZ for stateful tiers",
            status="Accepted",
            context=(
                "Business-critical data must stay available if one data center "
                "zone fails."
            ),
            decision=(
                "Deploy primary databases and stateful stores across two or more "
                "availability zones."
            ),
            consequences=(
                "Customers keep working through a zone outage; monthly cost is "
                "slightly higher."
            ),
            alternatives="Single-AZ with backups only (faster recovery is not guaranteed).",
        ),
    ]


def _fallback_raid() -> list[RaidItem]:
    return [
        RaidItem(
            type="Risk",
            description="Putting everything in one region means a regional outage stops the product.",
            impact="High — full service outage",
            mitigation="Document a DR/failover plan and rehearse it.",
        ),
        RaidItem(
            type="Assumption",
            description="Cloud service quotas are high enough for launch traffic.",
            impact="Medium — launch could be blocked",
            mitigation="Validate quotas with the provider before go-live.",
        ),
        RaidItem(
            type="Issue",
            description="Network security rules still need a formal review.",
            impact="Medium — possible exposure or blocked traffic",
            mitigation="Peer-review security groups before production.",
        ),
        RaidItem(
            type="Dependency",
            description="Corporate identity and DNS must be ready before cutover.",
            impact="High — blocks launch",
            mitigation="Track need-by dates and define a fallback.",
        ),
    ]


def _generate_adr_list(
    *,
    human_prompt: str,
    platform_key: str,
    xml_output: str,
    provider_key: str,
    logger: Any,
) -> list[AdrRecord]:
    doc_system = load_docs_prompt_pack()
    doc_human = _docs_human(
        human_prompt=human_prompt,
        platform_key=platform_key,
        xml_output=xml_output,
        need="ADRs",
    )
    try:
        pack = get_llm()(doc_system, doc_human, provider_key, ArchitectureDocPack, logger, None)
        if isinstance(pack, ArchitectureDocPack) and pack.adrs:
            return list(pack.adrs)
    except Exception as exc:
        if logger:
            logger.log("docs", f"ADR LLM failed: {exc}", level="warning")
    return _fallback_adrs()


def _generate_raid_list(
    *,
    human_prompt: str,
    platform_key: str,
    xml_output: str,
    provider_key: str,
    logger: Any,
) -> list[RaidItem]:
    doc_system = load_docs_prompt_pack()
    doc_human = _docs_human(
        human_prompt=human_prompt,
        platform_key=platform_key,
        xml_output=xml_output,
        need="RAID",
    )
    try:
        pack = get_llm()(doc_system, doc_human, provider_key, ArchitectureDocPack, logger, None)
        if isinstance(pack, ArchitectureDocPack) and pack.raid:
            return list(pack.raid)
    except Exception as exc:
        if logger:
            logger.log("docs", f"RAID LLM failed: {exc}", level="warning")
    return _fallback_raid()


def _generate_doc_pack(
    *,
    human_prompt: str,
    platform_key: str,
    xml_output: str,
    provider_key: str,
    adr_notes: bool,
    raid_register: bool,
    logger: Any,
) -> ArchitectureDocPack:
    """Combined ADR+RAID pack (tests / single-call compatibility)."""
    return ArchitectureDocPack(
        adrs=_generate_adr_list(
            human_prompt=human_prompt,
            platform_key=platform_key,
            xml_output=xml_output,
            provider_key=provider_key,
            logger=logger,
        ) if adr_notes else [],
        raid=_generate_raid_list(
            human_prompt=human_prompt,
            platform_key=platform_key,
            xml_output=xml_output,
            provider_key=provider_key,
            logger=logger,
        ) if raid_register else [],
    )


def _append_doc_pages(
    xml_output: str,
    *,
    primary_page_name: str,
    pack: ArchitectureDocPack,
    adr_notes: bool,
    raid_register: bool,
) -> str:
    # Preserve existing multipage view set; append ADR/RAID only.
    if xml_output.count("<diagram") > 1:
        tree = ET.fromstring(ensure_diagram_document(xml_output, primary_page_name))
        if adr_notes:
            frag = ensure_diagram_document(build_adr_page_xml(pack.adrs), "ADR Notes")
            ftree = ET.fromstring(frag)
            diag = ftree.find("diagram") if ftree.tag == "mxfile" else None
            if diag is not None and tree.tag == "mxfile":
                tree.append(diag)
        if raid_register:
            frag = ensure_diagram_document(build_raid_page_xml(pack.raid), "RAID")
            ftree = ET.fromstring(frag)
            diag = ftree.find("diagram") if ftree.tag == "mxfile" else None
            if diag is not None and tree.tag == "mxfile":
                tree.append(diag)
        return ET.tostring(tree, encoding="unicode")

    built: list[tuple[str, str]] = [(primary_page_name, xml_output)]
    if adr_notes:
        built.append(("ADR Notes", build_adr_page_xml(pack.adrs)))
    if raid_register:
        built.append(("RAID", build_raid_page_xml(pack.raid)))
    if len(built) == 1:
        return xml_output
    return merge_mxfile_pages(built)


def _count_service_leaves(xml: str) -> int:
    """Count non-annotation vertex leaves (excludes overview/legend/badges/containers)."""
    try:
        tree = ET.fromstring(ensure_diagram_document(xml or "", "Page"))
    except ET.ParseError:
        return 0
    n = 0
    for cell in tree.iter("mxCell"):
        if cell.get("vertex") != "1" or cell.get("edge") == "1":
            continue
        cid = cell.get("id") or ""
        if cid in {"0", "1", "overview_panel", "legend_box"} or cid.startswith(
            ("step_badge_", "note_", "ctrl_", "sec_", "gap_")
        ):
            continue
        style = cell.get("style") or ""
        if "container=1" in style or "swimlane" in style:
            continue
        n += 1
    return n


def _load_view_repair_pack() -> str:
    from app.adapters.outbound.config.env_config import EnvConfigAdapter

    path = EnvConfigAdapter.base_dir() / "config" / "xml_gen" / "views" / "repair.txt"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return (
        "Repair this focused architecture view page. Fix only the listed violations. "
        "Return complete draw.io XML in xml + step_markers."
    )


def _degraded_view_placeholder(view: str, reason: str) -> str:
    """Visible stub page when the view LLM soft-fails (never silently omit the tab)."""
    title = VIEW_PAGE_NAMES.get(view, view)
    safe_reason = (reason or "generation failed")[:180].replace("<", "").replace(">", "").replace('"', "")
    overview = (
        f"&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;{title} — unavailable&lt;/div&gt;"
        f"&lt;div style=&quot;font-size:15px;&quot;&gt;1. This page could not be generated ({safe_reason}).&lt;/div&gt;"
        f"&lt;div style=&quot;font-size:15px;&quot;&gt;2. Retry the request or check provider capacity.&lt;/div&gt;"
        f"&lt;div style=&quot;font-size:15px;&quot;&gt;3. Network / primary page is still available.&lt;/div&gt;"
    )
    return (
        f'<mxfile host="app.diagrams.net"><diagram id="diagram-{view}" name="{title}">'
        '<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" page="1" pageWidth="1600" pageHeight="900">'
        "<root><mxCell id=\"0\"/><mxCell id=\"1\" parent=\"0\"/>"
        '<mxCell id="2" value="" style="fillColor=#F5F5F5;strokeColor=none;" vertex="1" parent="1">'
        '<mxGeometry x="0" y="0" width="1600" height="900" as="geometry"/></mxCell>'
        f'<mxCell id="degraded_note" value="{title} view unavailable" '
        'style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF5F5;strokeColor=#B85450;'
        'fontSize=16;align=center;verticalAlign=middle;" vertex="1" parent="1">'
        '<mxGeometry x="200" y="200" width="600" height="120" as="geometry"/></mxCell>'
        f'<mxCell id="overview_panel" value="{overview}" '
        'style="rounded=1;arcSize=2;whiteSpace=wrap;html=1;fillColor=#FFF8E1;strokeColor=#F5A623;'
        'strokeWidth=2;align=left;verticalAlign=top;spacingLeft=16;spacingTop=14;fontSize=15;" '
        'vertex="1" parent="1">'
        '<mxGeometry x="900" y="80" width="480" height="220" as="geometry"/></mxCell>'
        "</root></mxGraphModel></diagram></mxfile>"
    )


def _finalize_view_page_xml(
    xml: str,
    title: str,
    step_markers: list[Any] | None,
    logger: Any,
    *,
    primary_xml: str = "",
    view: str = "",
    human_prompt: str = "",
) -> str | None:
    xml = normalize_single_page_xml(xml, title)
    check = validate_and_fix_xml(xml)
    xml = choose_fixed_xml(xml, check)
    xml = normalize_single_page_xml(xml, title)
    if primary_xml and view != "logical":
        # Logical uses logical_* MCP vocabulary (not cloud shape_type inject).
        xml = inject_shape_types_from_primary(xml, primary_xml)
    if not page_has_overview_panel(xml):
        if logger:
            logger.log(
                "views",
                f"{title} page missing overview_panel — adding minimal stub",
                level="warning",
            )
        xml = ensure_minimal_overview_panel(xml, title)
    if view and primary_xml:
        # Gap recommendations → Overview Panel (security / data_flow); then strip cards
        try:
            inv_labels: list[str] = []
            tree = ET.fromstring(ensure_diagram_document(primary_xml, "Architecture"))
            for cell in tree.iter("mxCell"):
                if cell.get("vertex") == "1" and cell.get("edge") != "1":
                    from app.adapters.outbound.diagram.architecture_docs import _plain_cell_label
                    lab = _plain_cell_label(cell.get("value") or "")
                    if lab:
                        inv_labels.append(lab)
            gaps = _gap_candidates(human_prompt or "", inv_labels, view=view)
            xml = ensure_gap_nodes(xml, view=view, gap_labels=gaps)
        except Exception:
            pass
        xml = apply_view_fixers(xml, view=view)
    elif view:
        xml = apply_view_fixers(xml, view=view)
    if view == "logical":
        from app.adapters.outbound.diagram.architecture_docs import assign_logical_shape_types
        xml = assign_logical_shape_types(xml)
    if step_markers:
        xml = apply_step_markers(xml, step_markers, logger)
    if _count_service_leaves(xml) < 2 and "degraded_note" not in (xml or ""):
        return None
    return xml


def _generate_single_view_page(
    *,
    human_prompt: str,
    platform_key: str,
    primary_xml: str,
    provider_key: str,
    view: str,
    logger: Any,
) -> str | None:
    """One LLM call for a focused view page, with one retry on empty/sparse results."""
    inventory = build_architecture_inventory(primary_xml, request_text=human_prompt or "")
    system = build_single_view_system(view)
    human = build_single_view_human(
        human_prompt=human_prompt,
        platform=platform_key,
        primary_xml=primary_xml,
        inventory_text=inventory,
        view=view,
    )
    title = VIEW_PAGE_NAMES.get(view, view)

    def _once() -> str | None:
        page = get_llm()(system, human, provider_key, SingleViewPage, logger, None)
        if not isinstance(page, SingleViewPage) or not (page.xml or "").strip():
            return None
        return _finalize_view_page_xml(
            page.xml,
            title,
            page.step_markers,
            logger,
            primary_xml=primary_xml,
            view=view,
            human_prompt=human_prompt or "",
        )

    for attempt in range(2):
        try:
            result = _once()
            if result:
                return result
            if logger and attempt == 0:
                logger.log(
                    "views",
                    f"{title} view empty/sparse — retrying once",
                    level="warning",
                )
        except Exception as exc:
            if logger:
                logger.log(
                    "views",
                    f"{title} view LLM failed (attempt {attempt + 1}): {exc}",
                    level="warning",
                )
    return None


def _repair_single_view_page(
    *,
    human_prompt: str,
    platform_key: str,
    primary_xml: str,
    current_xml: str,
    provider_key: str,
    view: str,
    violations_text: str,
    logger: Any,
) -> str | None:
    """One gated LLM repair for a focused view (semantic viewpoint only)."""
    inventory = build_architecture_inventory(primary_xml, request_text=human_prompt or "")
    system = _load_view_repair_pack() + "\n\n" + build_single_view_system(view)
    human = (
        f"Platform: {platform_key}\n"
        f"View: {VIEW_PAGE_NAMES.get(view, view)}\n"
        f"Original request: {(human_prompt or '')[:800]}\n"
        f"{violations_text}\n\n"
        f"Architecture inventory:\n{inventory}\n\n"
        "CURRENT PAGE XML (repair in place):\n"
        f"{current_xml[:12000]}\n"
    )
    title = VIEW_PAGE_NAMES.get(view, view)
    try:
        page = get_llm()(system, human, provider_key, SingleViewPage, logger, None)
    except Exception as exc:
        if logger:
            logger.log("views", f"{title} repair LLM failed: {exc}", level="warning")
        return None
    if not isinstance(page, SingleViewPage) or not (page.xml or "").strip():
        return None
    return _finalize_view_page_xml(
        page.xml,
        title,
        page.step_markers,
        logger,
        primary_xml=primary_xml,
        view=view,
        human_prompt=human_prompt or "",
    )


def _score_and_maybe_keep(
    page_xml: str,
    *,
    view: str,
    primary_xml: str,
    human_prompt: str,
    min_score: int,
) -> tuple[str, Any]:
    leaf_n = _count_service_leaves(primary_xml)
    density = achievable_density_target(leaf_n)
    scored = score_view(
        page_xml,
        view=view,
        primary_xml=primary_xml,
        request_text=human_prompt or "",
        density_target=density,
        min_score=min_score,
    )
    return page_xml, scored


def _merge_ordered_view_pages(
    *,
    views: list[str],
    primary_view: str,
    primary_xml: str,
    extra_by_view: dict[str, str],
) -> str:
    """Build mxfile pages in Logical → Network → Security → Data flow order."""
    by_view: dict[str, str] = {primary_view: primary_xml}
    for key, xml in extra_by_view.items():
        if key in VIEW_PAGE_NAMES and (xml or "").strip():
            by_view[key] = xml
    pages: list[tuple[str, str]] = []
    for v in views:
        if v not in by_view:
            continue
        name = VIEW_PAGE_NAMES[v]
        xml = ensure_diagram_document(by_view[v], name)
        # Rename diagram page to canonical view name
        try:
            tree = ET.fromstring(xml)
            if tree.tag == "mxfile":
                diag = tree.find("diagram")
                if diag is not None:
                    diag.set("name", name)
                xml = ET.tostring(tree, encoding="unicode")
        except ET.ParseError:
            pass
        pages.append((name, xml))
    if not pages:
        return primary_xml
    if len(pages) == 1:
        return ensure_diagram_document(pages[0][1], pages[0][0])
    return merge_mxfile_pages(pages)


def _summarize_llm_usage(logger: Any) -> dict[str, Any] | None:
    """Sum token usage/cost logged by call_llm across every LLM call this request made.

    Distinct from ``cost_estimate`` (that's the depicted cloud architecture's
    estimated monthly AWS/Azure/GCP spend) — this is what it cost in LLM API
    tokens to generate the diagram itself.
    """
    entries = getattr(logger, "entries", None) if logger else None
    if not entries:
        return None
    calls = [e for e in entries if e.step == "usage"]
    if not calls:
        return None

    total_input = total_output = total_tokens = 0
    total_cost = 0.0
    has_cost = False
    currency = display_currency()
    breakdown: list[dict[str, Any]] = []
    for entry in calls:
        d = entry.details or {}
        total_input += d.get("input_tokens", 0)
        total_output += d.get("output_tokens", 0)
        total_tokens += d.get("total_tokens", 0)
        cost = d.get("estimated_cost")
        if cost is None:
            cost = d.get("estimated_cost_usd")
        if cost is not None:
            total_cost += cost
            has_cost = True
        if d.get("currency"):
            currency = d["currency"]
        breakdown.append({
            "provider": d.get("provider"),
            "model": d.get("model"),
            "input_tokens": d.get("input_tokens", 0),
            "output_tokens": d.get("output_tokens", 0),
            "total_tokens": d.get("total_tokens", 0),
            "estimated_cost": cost,
            "currency": d.get("currency") or currency,
            "estimated_cost_usd": cost,  # back-compat; value is display currency
        })
    rounded = round(total_cost, 4) if has_cost else None
    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_tokens,
        "estimated_cost": rounded,
        "currency": currency,
        "estimated_cost_usd": rounded,  # back-compat; value is display currency
        "calls": breakdown,
    }


def _build_response(
    status: str = "success",
    *,
    xml: str | None = None,
    assistant_message: str = "",
    suggestions: list[Any] | None = None,
    renderer_used: str | None = None,
    mcp_preview_url: str | None = None,
    logger: Any = None,
    cost_estimate: dict | None = None,
    well_architected: dict | None = None,
    compliance: dict | None = None,
) -> dict[str, Any]:
    """Build the standard API response dict."""
    return {
        "status": status,
        "xml": xml,
        "mermaid": None,
        "suggestions": suggestions or [],
        "assistant_message": assistant_message,
        "renderer_used": renderer_used,
        "mcp_preview_url": mcp_preview_url,
        "cost_estimate": cost_estimate,
        "well_architected": well_architected,
        "compliance": compliance,
        "llm_usage": _summarize_llm_usage(logger),
        "logs": logger.to_list() if logger else [],
    }


def _safe_enrichment(name: str, result: Any, logger: Any) -> Any:
    """Normalize a gather result: return the value or None on exception, logging a warning."""
    if isinstance(result, BaseException):
        if logger:
            # str(TimeoutError()) is empty — spell out what happened instead of a blank message.
            detail = (
                f"timed out after {get_config().enrichment_branch_timeout_seconds:g}s"
                if isinstance(result, asyncio.TimeoutError) else str(result)
            )
            logger.log(name, f"{name} enrichment failed, skipping: {detail}", level="warning")
        return None
    return result


async def _emit(
    on_progress: ProgressCallback | None,
    step: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if not on_progress:
        return
    result = on_progress(step, message, details)
    if inspect.isawaitable(result):
        await result


async def run_xml_pipeline(
    *,
    user_prompt: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    provider: str = "claude",
    platform: str = "aws",
    logger: Any = None,
    custom_system_message: str = "",
    diagram_renderer: str | None = None,
    applied_suggestions: list[Any] | None = None,
    three_d: bool = False,
    cost_estimate: bool = False,
    well_architected: bool = False,
    compliance: bool = False,
    compliance_frameworks: list[str] | None = None,
    context_text: str | None = None,
    attachment_documents: list[dict[str, Any]] | None = None,
    current_xml: str | None = None,
    libavoid: bool | None = None,
    image_replicate: bool = False,
    animated_connectors: bool = False,
    include_suggestions: bool = False,
    diagram_views: list[str] | None = None,
    adr_notes: bool = False,
    raid_register: bool = False,
    interview_answers: dict[str, str] | None = None,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    provider_key = (provider or "claude").strip().lower()
    platform_key = (platform or "aws").strip().lower()
    use_libavoid = get_config().drawio_mcp_libavoid if libavoid is None else bool(libavoid)
    views = normalize_diagram_views(diagram_views)
    primary_view = pick_primary_view(views)
    primary_page_name = VIEW_PAGE_NAMES.get(primary_view, "Network")
    extra_views = [v for v in views if v != primary_view]

    logger.log("request", "MCP XML generation request", details={
        "platform": platform_key, "provider": provider_key,
        "renderer": diagram_renderer or get_config().diagram_renderer,
        "libavoid": use_libavoid,
        "image_replicate": image_replicate,
        "animated_connectors": animated_connectors,
        "suggestions": include_suggestions,
        "diagram_views": views,
        "primary_view": primary_view,
        "extra_views": extra_views,
        "adr_notes": adr_notes,
        "raid_register": raid_register,
    })
    await _emit(on_progress, "llm", "Generating diagram XML…", {
        "platform": platform_key, "provider": provider_key,
    })

    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")

    # Auto image-replicate when vision attachments are present unless explicitly off
    if attachment_documents and not image_replicate:
        image_replicate = True

    # Token-light context: interview answers + primary-view focus only.
    # Extra views / ADR / RAID are separate cheaper passes — never ask the main
    # XML call to emit multipage docs (that left Network/Security empty).
    context_parts = [p for p in (
        context_text or "",
        build_interview_prompt_block(interview_answers),
        build_views_prompt_block(views),
    ) if p]
    enriched_context = "\n\n".join(context_parts) if context_parts else None

    xml_diagram = await run_llm_sync(
        generate_xml_diagram, human_prompt, provider_key, platform_key, logger, messages,
        custom_system_message, applied_suggestions, three_d, enriched_context, attachment_documents,
        current_xml=current_xml,
        image_replicate=image_replicate,
        animated_connectors=animated_connectors,
        include_suggestions=include_suggestions,
        primary_view=primary_view,
    )
    if 'vertex="1"' not in (xml_diagram.xml or ""):
        # A successful call with no diagram content (empty/near-empty xml) is not
        # a partial success — nothing downstream should validate/repair/enrich
        # what is effectively a blank canvas.
        if logger:
            logger.log("error", "LLM returned no diagram content", level="error")
        raise ValueError("LLM returned no diagram content — nothing to validate or enrich.")

    # Verify the generated XML is actually well-formed *before* spending time/cost
    # on downstream diagnostics/repair/MCP/compliance — every one of those steps
    # assumes a parseable document, so a truncated or malformed response here
    # can't be recovered by any of them. Retrying immediately (regardless of
    # *why* it was incomplete — a token ceiling, a less capable/lite model
    # stopping itself early, a JSON-escaping slip) is cheaper and more reliable
    # than discovering the breakage only at the final guarantee_loadable_xml
    # circuit breaker, after cost/WAF/compliance have already run against garbage.
    try:
        ET.fromstring(ensure_diagram_document(xml_diagram.xml, PLATFORM_LABELS.get(platform_key, "Architecture")))
    except ET.ParseError as exc:
        if logger:
            logger.log(
                "xml_diagram",
                f"Generated XML was incomplete or malformed ({exc}) — retrying generation once",
                level="warning",
            )
        await _emit(on_progress, "llm", "First attempt was incomplete, retrying diagram generation…")
        xml_diagram = await run_llm_sync(
            generate_xml_diagram, human_prompt, provider_key, platform_key, logger, messages,
            custom_system_message, applied_suggestions, three_d, enriched_context, attachment_documents,
            current_xml=current_xml,
            image_replicate=image_replicate,
            animated_connectors=animated_connectors,
            include_suggestions=include_suggestions,
            primary_view=primary_view,
            retry_hint=(
                "Your previous response was cut off or malformed before the diagram XML "
                "was complete — every mxCell must be fully written with its geometry, "
                "style, and closing tags; nothing may be left half-written. Regenerate "
                "the COMPLETE diagram from scratch; if it is large, prefer fewer/simpler "
                "shapes over leaving any shape unfinished."
            ),
        )
        if 'vertex="1"' not in (xml_diagram.xml or ""):
            if logger:
                logger.log("error", "LLM returned no diagram content on retry", level="error")
            raise ValueError("LLM returned no diagram content on retry — nothing to validate or enrich.")

    await _emit(on_progress, "validate", "Validating and normalizing XML…")
    xml_output = ensure_diagram_document(xml_diagram.xml, primary_page_name)
    check = validate_and_fix_xml(xml_output)
    xml_output = choose_fixed_xml(xml_output, check)
    xml_output = normalize_platform_styles(xml_output, platform_key)

    await _emit(on_progress, "repair", "Checking for overlaps, dangling edges, missing labels…")
    pre_repair_diagnostics = diagnose_xml(xml_output)
    xml_output, diagnostics = repair_xml(xml_output)
    fixed_count = (
        (len(pre_repair_diagnostics.errors) + len(pre_repair_diagnostics.warnings))
        - (len(diagnostics.errors) + len(diagnostics.warnings))
    )
    if fixed_count > 0 and logger:
        logger.log(
            "repair",
            f"Auto-fixed {fixed_count} diagram issue(s) — overlaps, missing labels, "
            "dangling edges, or misplaced icons",
            level="success",
            details={"before": len(pre_repair_diagnostics.errors) + len(pre_repair_diagnostics.warnings),
                      "after": len(diagnostics.errors) + len(diagnostics.warnings)},
        )
    if diagnostics.errors or diagnostics.warnings:
        if logger:
            logger.log(
                "repair",
                "Deterministic repair left residual issues (no LLM repair — format/structure "
                "fixes are auto-only; remaining items are logged for visibility)",
                level="warning",
                details={"errors": diagnostics.errors, "warnings": diagnostics.warnings},
            )
        await _emit(on_progress, "repair", "Deterministic repair complete")

    # ------------------------------------------------------------------
    # Post-Network fan-out: views + ADR + RAID + cost/WAF/compliance + MCP
    # resolve overlap on the same wall-clock window. Diagram return must not
    # wait on a waterfall of serial LLM/enrichment stages.
    # ------------------------------------------------------------------
    surgical = _was_successful_surgical_edit(logger)
    if surgical and (extra_views or adr_notes or raid_register):
        if logger:
            logger.log(
                "views",
                "Skipping extra views and ADR/RAID regeneration after surgical edit "
                "(tabs may be stale relative to the edited Network page)",
                level="warning",
            )

    # Primary badges before merge — post-merge apply_step_markers hits page 1 only.
    if xml_diagram.step_markers:
        xml_output = apply_step_markers(xml_output, xml_diagram.step_markers, logger)

    primary_xml_snapshot = xml_output
    primary_shape_types = extract_shape_types(primary_xml_snapshot) if (
        is_xml_pipeline_mode(diagram_renderer) and primary_xml_snapshot
    ) else []
    use_mcp = is_xml_pipeline_mode(diagram_renderer)
    branch_timeout = get_config().enrichment_branch_timeout_seconds

    extra_by_view: dict[str, str] = {}
    adr_list: list[AdrRecord] = []
    raid_list: list[RaidItem] = []
    primary_style_overrides: dict[str, str] = {}

    async def _run_view(view_key: str) -> tuple[str, str | None]:
        title = VIEW_PAGE_NAMES.get(view_key, view_key)
        cfg = get_config()
        view_provider = cfg.view_llm_provider or provider_key
        min_score = cfg.view_quality_min_score
        repair_passes = cfg.view_repair_max_passes
        await _emit(on_progress, "views", f"Generating {title} view…")
        page_xml = await run_llm_sync(
            _generate_single_view_page,
            human_prompt=human_prompt,
            platform_key=platform_key,
            primary_xml=primary_xml_snapshot,
            provider_key=view_provider,
            view=view_key,
            logger=logger,
        )
        if not page_xml:
            if logger:
                logger.log(
                    "views",
                    f"{title} view empty — emitting degraded placeholder",
                    level="warning",
                )
            await _emit(on_progress, "views", f"{title} view unavailable (placeholder)")
            return view_key, _degraded_view_placeholder(view_key, "empty or sparse LLM result")

        page_xml, scored = _score_and_maybe_keep(
            page_xml,
            view=view_key,
            primary_xml=primary_xml_snapshot,
            human_prompt=human_prompt or "",
            min_score=min_score,
        )
        if logger:
            codes = [v.code for v in scored.violations][:8]
            logger.log(
                "views",
                f"{title} view scored {scored.score}",
                level="info" if scored.score >= min_score else "warning",
                details={"score": scored.score, "violations": codes, "metrics": scored.metrics},
            )
        await _emit(on_progress, "views", f"{title} scored {scored.score}")

        best_xml, best_score = page_xml, scored
        for _ in range(repair_passes):
            if best_score.score >= min_score:
                break
            await _emit(
                on_progress,
                "views",
                f"{title} scored {best_score.score} — repairing {len(best_score.violations)} issue(s)…",
            )
            repaired = await run_llm_sync(
                _repair_single_view_page,
                human_prompt=human_prompt,
                platform_key=platform_key,
                primary_xml=primary_xml_snapshot,
                current_xml=best_xml,
                provider_key=view_provider,
                view=view_key,
                violations_text=format_violations_for_repair(best_score),
                logger=logger,
            )
            if not repaired:
                break
            repaired, repaired_score = _score_and_maybe_keep(
                repaired,
                view=view_key,
                primary_xml=primary_xml_snapshot,
                human_prompt=human_prompt or "",
                min_score=min_score,
            )
            if logger:
                logger.log(
                    "views",
                    f"{title} repair scored {repaired_score.score} (was {best_score.score})",
                    level="info",
                    details={"score": repaired_score.score},
                )
            # Never regress
            if repaired_score.score >= best_score.score:
                best_xml, best_score = repaired, repaired_score

        await _emit(on_progress, "views", f"{title} view ready (score {best_score.score})")
        return view_key, best_xml

    async def _run_adr() -> list[AdrRecord]:
        await _emit(on_progress, "docs", "Generating ADR Notes…")
        rows = await run_llm_sync(
            _generate_adr_list,
            human_prompt=human_prompt,
            platform_key=platform_key,
            xml_output=primary_xml_snapshot,
            provider_key=provider_key,
            logger=logger,
        )
        await _emit(on_progress, "docs", "ADR Notes ready")
        return rows

    async def _run_raid() -> list[RaidItem]:
        await _emit(on_progress, "docs", "Generating RAID register…")
        rows = await run_llm_sync(
            _generate_raid_list,
            human_prompt=human_prompt,
            platform_key=platform_key,
            xml_output=primary_xml_snapshot,
            provider_key=provider_key,
            logger=logger,
        )
        await _emit(on_progress, "docs", "RAID register ready")
        return rows

    async def _run_cost() -> dict | None:
        if not (cost_estimate and "network" in views and primary_xml_snapshot):
            return None
        data = await asyncio.to_thread(estimate_monthly_cost, primary_xml_snapshot, platform_key)
        if logger:
            logger.log(
                "cost",
                f"Estimated monthly cost: A${data['total']}",
                level="info",
                details={"service_count": data["services_matched"] + data["services_unmatched"], "cost_estimate": data},
            )
        await _emit(on_progress, "cost", f"Estimated monthly cost: A${data['total']}")
        return data

    async def _run_waf() -> dict | None:
        # WAF only applies when a Network page was requested
        if not (well_architected and "network" in views and primary_xml_snapshot):
            return None
        data = await asyncio.to_thread(assess_well_architected, primary_xml_snapshot, platform_key)
        if logger:
            logger.log(
                "waf",
                f"Well-Architected: {data['overall_status']}",
                level="info",
                details={"pillars": len(data["pillars"]), "services": data["services_analyzed"], "well_architected": data},
            )
        await _emit(on_progress, "waf", f"Well-Architected: {data['overall_status']}")
        return data

    async def _run_compliance() -> dict | None:
        if not (compliance and "network" in views and primary_xml_snapshot):
            return None
        await _emit(on_progress, "compliance", "Assessing compliance…")
        data = await asyncio.to_thread(
            assess_compliance,
            xml=primary_xml_snapshot,
            platform=platform_key,
            frameworks=compliance_frameworks,
            provider=provider_key if compliance_frameworks else None,
            logger=logger,
        )
        if logger:
            logger.log(
                "compliance",
                "Compliance assessment complete",
                level="info",
                details={"compliance": data},
            )
        await _emit(on_progress, "compliance", "Compliance assessment complete")
        return data

    async def _run_mcp_resolve_primary() -> dict[str, str]:
        if not use_mcp or not primary_shape_types:
            return {}
        await _emit(on_progress, "mcp", "Resolving official icon styles…")
        try:
            return await resolve_shape_styles(
                primary_shape_types, platform=platform_key, logger=logger,
            )
        except Exception as exc:
            if logger:
                logger.log("mcp", f"Primary icon resolve skipped: {exc}", level="warning")
            return {}

    fanout_jobs: list[tuple[str, Any]] = []
    if extra_views and not surgical:
        await _emit(
            on_progress,
            "views",
            f"Generating {len(extra_views)} additional view page(s) in parallel…",
        )
        for vk in extra_views:
            fanout_jobs.append(("view", _run_view(vk)))
    if adr_notes and not surgical:
        fanout_jobs.append(("adr", _run_adr()))
    if raid_register and not surgical:
        fanout_jobs.append(("raid", _run_raid()))
    fanout_jobs.append(("cost", _run_cost()))
    fanout_jobs.append(("waf", _run_waf()))
    fanout_jobs.append(("compliance", _run_compliance()))
    fanout_jobs.append(("mcp_primary", _run_mcp_resolve_primary()))

    await _emit(on_progress, "enrich", "Running views, docs, and enrichment in parallel…")
    fanout_results = await asyncio.gather(
        *[asyncio.wait_for(coro, timeout=branch_timeout) for _, coro in fanout_jobs],
        return_exceptions=True,
    )

    cost_data = None
    waf_data = None
    compliance_data = None
    for (kind, _), raw in zip(fanout_jobs, fanout_results):
        if isinstance(raw, BaseException):
            label = {
                "view": "views",
                "adr": "docs",
                "raid": "docs",
                "cost": "cost",
                "waf": "waf",
                "compliance": "compliance",
                "mcp_primary": "mcp",
            }.get(kind, kind)
            if logger:
                detail = (
                    f"timed out after {branch_timeout:g}s"
                    if isinstance(raw, asyncio.TimeoutError)
                    else str(raw)
                )
                logger.log(label, f"{kind} branch failed: {detail}", level="warning")
            continue
        if kind == "view":
            vk, page_xml = raw
            if page_xml:
                extra_by_view[vk] = page_xml
        elif kind == "adr":
            adr_list = raw or []
        elif kind == "raid":
            raid_list = raw or []
        elif kind == "cost":
            cost_data = raw
        elif kind == "waf":
            waf_data = raw
        elif kind == "compliance":
            compliance_data = raw
        elif kind == "mcp_primary":
            primary_style_overrides = raw or {}

    # Merge views (primary already has badges).
    if extra_by_view:
        try:
            merged = _merge_ordered_view_pages(
                views=views,
                primary_view=primary_view,
                primary_xml=xml_output,
                extra_by_view=extra_by_view,
            )

            async def _repair_named_page(name: str, page_xml: str) -> tuple[str, str]:
                repaired, _ = await asyncio.to_thread(repair_xml, page_xml)
                return name, repaired

            repair_jobs: list[Any] = []
            try:
                tree = ET.fromstring(ensure_diagram_document(merged, primary_page_name))
                if tree.tag == "mxfile":
                    for diag in tree.findall("diagram"):
                        name = diag.get("name") or "Page"
                        if name in {"ADR Notes", "RAID"}:
                            continue
                        inner = "".join(ET.tostring(c, encoding="unicode") for c in list(diag))
                        page_xml = ensure_diagram_document(inner, name)
                        repair_jobs.append(_repair_named_page(name, page_xml))
            except ET.ParseError:
                repair_jobs = []

            if repair_jobs:
                repaired_pages = await asyncio.gather(*repair_jobs)
                order_names = [VIEW_PAGE_NAMES[v] for v in views if VIEW_PAGE_NAMES.get(v)]
                by_name = dict(repaired_pages)
                ordered = [(n, by_name[n]) for n in order_names if n in by_name]
                for n, x in repaired_pages:
                    if n not in {p[0] for p in ordered}:
                        ordered.append((n, x))
                xml_output = merge_mxfile_pages(ordered)
            else:
                xml_output = merged
            if logger:
                logger.log(
                    "views",
                    f"Merged view pages: {[VIEW_PAGE_NAMES[v] for v in views if v == primary_view or v in extra_by_view]}",
                    level="success",
                    details={"extra_returned": len(extra_by_view)},
                )
        except Exception as exc:
            if logger:
                logger.log("views", f"Extra views merge skipped: {exc}", level="warning")

    # Apply icon styles to all pages; resolve any new shape_types from extras.
    preview_url = None
    renderer_used = "local"
    if use_mcp:
        local_xml = xml_output
        renderer_used = "mcp"
        try:
            overrides = dict(primary_style_overrides)
            all_types = extract_shape_types(local_xml) or []
            delta = [t for t in all_types if t not in overrides]
            if delta:
                try:
                    more = await resolve_shape_styles(delta, platform=platform_key, logger=logger)
                    overrides.update(more or {})
                except Exception as exc:
                    if logger:
                        logger.log("mcp", f"Delta icon resolve skipped: {exc}", level="warning")
            if overrides:
                local_xml = apply_mcp_styles(local_xml, overrides)
                if logger:
                    logger.log(
                        "mcp",
                        f"Applied {len(overrides)} MCP icon styles to diagram",
                        level="success",
                        details={"resolved_count": len(overrides)},
                    )
                await _emit(on_progress, "mcp", f"Applied {len(overrides)} icon styles")

            local_xml = fit_containers_to_children(local_xml)

            page_count = local_xml.count("<diagram")
            if use_libavoid and page_count > 1:
                if logger:
                    logger.log(
                        "mcp",
                        "Skipping libavoid on multipage diagram "
                        f"({page_count} pages) — duplicate cell ids across pages",
                        level="info",
                    )
            elif use_libavoid:
                await _emit(on_progress, "mcp", "Applying libavoid edge routing…")
                pre_routing_xml = local_xml
                try:
                    preview_url, routed_xml = await create_diagram(
                        local_xml, routing="libavoid", logger=logger,
                    )
                except DrawioMcpError as exc:
                    if logger:
                        logger.log("mcp", f"Libavoid skipped: {exc}", level="warning")
                    routed_xml = None
                if routed_xml:
                    if routed_xml.lower().count("<mxfile") > 1:
                        if logger:
                            logger.log(
                                "mcp",
                                "Libavoid routing discarded — nested mxfile in response",
                                level="warning",
                            )
                        local_xml = pre_routing_xml
                    else:
                        routed_check = validate_and_fix_xml(routed_xml)
                        candidate = choose_fixed_xml(routed_xml, routed_check)
                        try:
                            ET.fromstring(candidate)
                            if candidate.lower().count("<mxfile") > 1:
                                raise ET.ParseError("nested mxfile after repair")
                            local_xml = candidate
                            if logger:
                                logger.log("mcp", "Libavoid routing applied", level="success")
                        except ET.ParseError as exc:
                            if logger:
                                logger.log(
                                    "mcp",
                                    f"Libavoid routing discarded — unparseable: {exc}",
                                    level="warning",
                                )
                            local_xml = pre_routing_xml
        except DrawioMcpError as exc:
            if logger:
                logger.log("mcp", f"MCP enrichment skipped: {exc}", level="warning")
            renderer_used = "local"
            local_xml = fit_containers_to_children(local_xml)
        except Exception as exc:
            if logger:
                logger.log("mcp", f"MCP error: {exc}", level="warning")
            renderer_used = "local"
            local_xml = fit_containers_to_children(local_xml)
        xml_output = local_xml
    else:
        xml_output = fit_containers_to_children(xml_output)

    # Append ADR / RAID pages from fan-out (independent soft-fail).
    if (adr_list or raid_list) and not surgical:
        try:
            pack = ArchitectureDocPack(adrs=adr_list, raid=raid_list)
            xml_output = _append_doc_pages(
                xml_output,
                primary_page_name=primary_page_name,
                pack=pack,
                adr_notes=bool(adr_list),
                raid_register=bool(raid_list),
            )
            if logger:
                logger.log(
                    "docs",
                    "Appended architecture documentation pages",
                    level="success",
                    details={
                        "adr": bool(adr_list),
                        "raid": bool(raid_list),
                        "adr_count": len(adr_list),
                        "raid_count": len(raid_list),
                    },
                )
        except Exception as exc:
            if logger:
                logger.log("docs", f"Documentation pages skipped: {exc}", level="warning")


    # Final circuit breaker — every escaping/corruption bug hit so far surfaced
    # here, at the exit point, regardless of which upstream step caused it.
    # Guarantee loadability once, right before the response leaves the service,
    # instead of trusting every step above to have been gap-free.
    page_label = primary_page_name
    xml_output, xml_ok, xml_failure = guarantee_loadable_xml(xml_output, page_label)
    if not xml_ok and logger:
        logger.log(
            "error",
            "Diagram XML failed to parse after every repair pass — served an "
            "empty canvas instead of a broken diagram file",
            level="error",
            details=xml_failure,
        )

    if not use_mcp:
        logger.log(
            "complete",
            "XML pipeline finished (local mode)",
            level="success",
            details={"xml_chars": len(xml_output), "renderer_used": "local"},
        )
        await _emit(on_progress, "complete", "Diagram ready")
        return _build_response(
            xml=xml_output,
            assistant_message=xml_diagram.assistant_message,
            suggestions=filter_applied_suggestions(xml_diagram.suggestions, applied_suggestions),
            renderer_used="local",
            logger=logger,
            cost_estimate=cost_data,
            well_architected=waf_data,
            compliance=compliance_data,
        )

    logger.log(
        "complete",
        "XML pipeline finished",
        level="success",
        details={"xml_chars": len(xml_output), "renderer_used": renderer_used},
    )
    await _emit(on_progress, "complete", "Diagram ready")
    return _build_response(
        xml=xml_output,
        assistant_message=xml_diagram.assistant_message,
        suggestions=filter_applied_suggestions(xml_diagram.suggestions, applied_suggestions),
        renderer_used=renderer_used,
        mcp_preview_url=preview_url,
        logger=logger,
        cost_estimate=cost_data,
        well_architected=waf_data,
        compliance=compliance_data,
    )
