"""XML generation pipeline — IR+compiler for fresh cloud; direct XML for edit/3D."""

from __future__ import annotations

import asyncio
import inspect
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.drawio_mcp_client import DrawioMcpError, create_diagram, resolve_shape_styles
from app.adapters.outbound.diagram.mode import is_xml_pipeline_mode
from app.adapters.outbound.diagram.xml_diagnostics import (
    apply_step_markers,
    center_unresolved_leaf_labels,
    diagnose_xml,
    ensure_legend_position,
    ensure_overview_panel_position,
    repair_xml,
    reposition_step_badges,
    validate_layout,
)
from app.adapters.outbound.diagram.xml_tools import (
    apply_mcp_styles,
    choose_fixed_xml,
    ensure_diagram_document,
    extract_shape_types,
    extract_page_by_name,
    find_service_keys,
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
    GapAnalysisResult,
    RaidItem,
    RequiredAddition,
    VIEW_ORDER,
    VIEW_PAGE_NAMES,
    _gap_candidates,
    achievable_density_target,
    build_adr_page_xml,
    build_architecture_inventory,
    build_gap_analysis_block,
    build_interview_prompt_block,
    build_raid_page_xml,
    build_views_prompt_block,
    derive_story_markers,
    ensure_ir_overview_panel,
    ensure_minimal_overview_panel,
    refresh_ir_overview_panel,
    load_docs_prompt_pack,
    load_gap_analysis_prompt_pack,
    merge_mxfile_pages,
    normalize_diagram_views,
    normalize_single_page_xml,
    page_has_overview_panel,
    pick_primary_view,
    inject_shape_types_from_primary,
)
from app.adapters.outbound.diagram.view_conformance import score_view
from app.adapters.outbound.diagram.view_fixers import apply_view_fixers, ensure_gap_nodes
from app.adapters.outbound.llm.llm_client import is_quota_or_rate_limit_error
from app.core.ports.llm import get_llm
from app.core.services.agent import (
    NonSurgicalEditError,
    generate_generic_diagram_from_ir,
    generate_xml_diagram,
    generate_xml_diagram_from_ir,
)

_SERVICE_INVENTORY_LIMIT = 36
_REQUEST_TRUNCATE_DOCS = 600

#: Progress/SSE callback: (step, message, optional details) → sync or async.
ProgressCallback = Callable[[str, str, dict[str, Any] | None], Any | Awaitable[Any]]


def _should_use_ir_path(primary_view: str, current_xml: str | None, three_d: bool) -> bool:
    """Whether this request is eligible for the IR + compiler generation path.

    Fresh cloud diagrams are IR-only. Edit mode (substantial ``current_xml``) and
    3D keep the direct-XML path — IR cannot express "keep these cell ids" or
    isometric vocabulary.
    """
    cfg = get_config()
    if not cfg.diagram_ir_enabled:
        return False
    if three_d:
        return False
    if current_xml and len(current_xml) > 200:
        return False
    return (primary_view or "network").lower() in cfg.diagram_ir_views


def _require_ir_or_raise(view: str, *, context: str) -> None:
    """Fresh generation must use IR — no silent direct-XML fallback."""
    cfg = get_config()
    view_key = (view or "network").lower()
    if not cfg.diagram_ir_enabled:
        raise ValueError(
            f"{context}: fresh diagrams require DIAGRAM_IR_ENABLED=true "
            "(edit mode and 3D still use direct XML)."
        )
    if view_key not in cfg.diagram_ir_views:
        raise ValueError(
            f"{context}: view {view_key!r} is not in DIAGRAM_IR_VIEWS={cfg.diagram_ir_views}. "
            "Add it there, or omit the view from the request."
        )


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


def _fallback_gap_analysis(human_prompt: str) -> GapAnalysisResult:
    """Deterministic gap defaults when the gap-analysis LLM call fails.

    Reuses the existing keyword-based ``_gap_candidates`` helper (previously
    only run *after* generation, against the produced inventory) with an
    empty service inventory, since nothing has been generated yet at this
    point in the pipeline.
    """
    gaps = _gap_candidates(human_prompt or "", [])
    return GapAnalysisResult(
        required_additions=[
            RequiredAddition(
                component=g, viewpoint="generic",
                why="Deterministic fallback — gap-analysis LLM call was unavailable.",
            )
            for g in gaps
        ],
    )


class _AttachmentViewClassification(BaseModel):
    view: Literal["network", "security", "data_flow", "logical"] = Field(
        description="Which of this tool's diagram lenses the attached image most resembles."
    )


_ATTACHMENT_VIEW_CLASSIFY_SYSTEM = (
    "You classify an uploaded architecture diagram image (or PDF page) into exactly one of "
    "four lens categories used by this tool. Judge by the diagram's actual structure and "
    "labels, not just its general topic.\n\n"
    "- network: infrastructure/topology diagram — VPCs, subnets, availability zones, load "
    "balancers, security groups, network appliances, IP-level connectivity.\n"
    "- security: trust-zone / access-control diagram — trust boundaries, firewalls, IAM, "
    "encryption/compliance zones.\n"
    "- data_flow: data pipeline / ETL diagram — sources, processing stages, sinks, data "
    "movement phases.\n"
    "- logical: generic business/process/application architecture — the default when the "
    "image doesn't clearly match one of the three specific lenses above.\n\n"
    "When in doubt, return logical."
)


def _classify_attachment_view(
    *,
    attachment_documents: list[dict] | None,
    human_prompt: str,
    provider_key: str,
    logger: Any,
) -> str | None:
    """Best-effort: which view lens an uploaded image most resembles, or None on failure.

    Mirrors ``_run_gap_analysis``'s soft-fail shape — a classification miss never
    blocks generation, it just falls back to the caller's own default primary view.
    """
    if not attachment_documents:
        return None
    try:
        result = get_llm()(
            _ATTACHMENT_VIEW_CLASSIFY_SYSTEM,
            f"Request:\n{human_prompt or '(no accompanying text)'}",
            provider_key, _AttachmentViewClassification, logger, attachment_documents,
        )
        return result.view
    except Exception as exc:
        if logger:
            logger.log(
                "classify", f"Attachment view classification failed: {exc}", level="warning",
            )
        return None


def _run_gap_analysis(
    *,
    human_prompt: str,
    platform_key: str,
    interview_answers: dict[str, str] | None,
    views: list[str],
    provider_key: str,
    logger: Any,
) -> GapAnalysisResult:
    """Pre-generation architecture gap-analysis LLM call.

    Runs once, before the primary XML call, and enriches every downstream
    view (primary + fan-out) with defaulted security/resilience/
    observability/data components a thin brief omitted — see
    config/xml_gen/gap_analysis.txt. Mirrors ``_generate_adr_list``'s
    structured-call + deterministic-fallback pattern; a failure here never
    blocks diagram generation.
    """
    system = load_gap_analysis_prompt_pack()
    parts = [
        f"Platform: {platform_key}",
        f"Requested views: {', '.join(normalize_diagram_views(views))}",
        f"User brief: {human_prompt[:_REQUEST_TRUNCATE_DOCS]}",
        build_interview_prompt_block(interview_answers),
    ]
    human = "\n".join(p for p in parts if p)
    try:
        result = get_llm()(system, human, provider_key, GapAnalysisResult, logger, None)
        if isinstance(result, GapAnalysisResult):
            return result
    except Exception as exc:
        if logger:
            details = (
                {"quota_exceeded": True, "feature": "Gap analysis"}
                if is_quota_or_rate_limit_error(exc)
                else {"degraded": True, "feature": "Gap analysis", "reason": str(exc)}
            )
            logger.log("gap", f"Gap analysis LLM failed: {exc}", level="warning", details=details)
    return _fallback_gap_analysis(human_prompt)


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
            details = (
                {"quota_exceeded": True, "feature": "ADR notes"}
                if is_quota_or_rate_limit_error(exc)
                else {"degraded": True, "feature": "ADR notes", "reason": str(exc)}
            )
            logger.log("docs", f"ADR LLM failed: {exc}", level="warning", details=details)
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
            details = (
                {"quota_exceeded": True, "feature": "RAID register"}
                if is_quota_or_rate_limit_error(exc)
                else {"degraded": True, "feature": "RAID register", "reason": str(exc)}
            )
            logger.log("docs", f"RAID LLM failed: {exc}", level="warning", details=details)
    return _fallback_raid()


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


def _finalize_view_page_xml(
    xml: str,
    title: str,
    step_markers: list[Any] | None,
    logger: Any,
    *,
    primary_xml: str = "",
    view: str = "",
    human_prompt: str = "",
    ir_title: str = "",
) -> str | None:
    xml = normalize_single_page_xml(xml, title)
    check = validate_and_fix_xml(xml)
    xml = choose_fixed_xml(xml, check)
    xml = normalize_single_page_xml(xml, title)
    if primary_xml and view != "logical":
        # Logical keeps its own shape_types (logical_* and intentional aws_*).
        xml = inject_shape_types_from_primary(xml, primary_xml)
    if not page_has_overview_panel(xml):
        if logger:
            logger.log(
                "views",
                f"{title} page missing overview_panel — adding {'IR-synthesized' if ir_title else 'minimal stub'} overview",
                level="warning",
            )
        if ir_title:
            xml = ensure_ir_overview_panel(xml, title, ir_title)
        else:
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
        from app.adapters.outbound.diagram.architecture_docs import (
            assign_logical_shape_types,
            assign_saas_shape_types,
        )
        xml = assign_logical_shape_types(xml)
        # Brand saas_* SVGs overwrite logical_saas when vendor matches.
        xml = assign_saas_shape_types(xml)
    elif view in ("network", "security", "data_flow"):
        from app.adapters.outbound.diagram.architecture_docs import assign_saas_shape_types
        xml = assign_saas_shape_types(xml)
    # Refresh overview + badges from the post-fixer graph so numbers match.
    if ir_title:
        xml = refresh_ir_overview_panel(xml, title, ir_title)
        step_markers = derive_story_markers(xml, title)
    if step_markers:
        xml = apply_step_markers(xml, step_markers, logger)
        xml = reposition_step_badges(xml)
    if _count_service_leaves(xml) < 2 and "degraded_note" not in (xml or ""):
        return None
    return xml


def _generate_ir_view_page(
    *,
    human_prompt: str,
    platform_key: str,
    primary_xml: str,
    provider_key: str,
    view: str,
    logger: Any,
    context_text: str | None = None,
    attachment_documents: list[dict[str, Any]] | None = None,
    image_replicate: bool = False,
) -> str | None:
    """IR path for a secondary (extra) view — same generator as primary.

    Passes primary inventory + the same enriched context the primary page saw
    so Logical/Security/Data-flow describe the *same* system, not a parallel invent.
    """
    from app.adapters.outbound.diagram.architecture_docs import build_architecture_inventory

    inv = build_architecture_inventory(
        primary_xml, service_limit=80, request_text=human_prompt or "",
    )
    seeded = "\n\n".join(
        p for p in (
            context_text,
            "PRIMARY DIAGRAM INVENTORY — align this view to the same system "
            "(same services/zones/capabilities; do not invent a different architecture):\n"
            + inv,
        ) if p
    )
    diagram = generate_xml_diagram_from_ir(
        view, human_prompt, provider_key, platform_key, logger, None,
        context_text=seeded or None,
        attachment_documents=attachment_documents,
        image_replicate=image_replicate,
    )
    title = VIEW_PAGE_NAMES.get(view, view)
    return _finalize_view_page_xml(
        diagram.xml, title, diagram.step_markers, logger,
        primary_xml=primary_xml, view=view, human_prompt=human_prompt or "",
        ir_title=diagram.ir_title,
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
    primary_view: str,
    primary_xml: str,
    extra_by_view: dict[str, str],
) -> str:
    """Build mxfile pages in the fixed tab order Logical, Network, Data flow, Security.

    ``primary_view`` (usually Network — see ``pick_primary_view``) only decides
    which view's LLM call seeds generation and fans out the others; it does not
    control tab order. Tab order is always the canonical ``VIEW_ORDER``,
    independent of which view was generated first.
    """
    by_view: dict[str, str] = {primary_view: primary_xml}
    for key, xml in extra_by_view.items():
        if key in VIEW_PAGE_NAMES and (xml or "").strip():
            by_view[key] = xml
    ordered_keys = [v for v in VIEW_ORDER if v in by_view]
    pages: list[tuple[str, str]] = []
    for v in ordered_keys:
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


def _refresh_overview_and_legend_positions(xml: str) -> str:
    """Re-run ``ensure_overview_panel_position``/``ensure_legend_position``/
    ``center_unresolved_leaf_labels`` across EVERY page of a (possibly
    multi-page) document, not just the first.

    All three fixers use ``xml_diagnostics._parse_docroot`` →
    ``_find_diagram_root``, which only ever looks at the *first*
    ``<diagram>`` in an mxfile — correct for their other call sites
    (single-page XML at that point in the pipeline), but by the time this
    runs, ``xml_output`` is the merged multi-page mxfile and the page that
    actually grew/got new icons resolved (typically the primary view, e.g.
    Network) is very often NOT page 1 in tab order (Logical is, per
    ``VIEW_ORDER``). Fixing only page 1 silently no-ops for every other page
    — mirrors how ``fit_containers_to_children``/``apply_mcp_styles``
    already handle "every ``<root>``" for the same reason. Running
    ``center_unresolved_leaf_labels`` here specifically (not inside
    ``repair_xml``, which runs before MCP resolution) is what makes it only
    ever touch a leaf that's STILL unresolved once MCP has had its shot, not
    one merely pending resolution.
    """
    def _finalize(page_xml: str) -> str:
        page_xml = ensure_overview_panel_position(page_xml)
        page_xml = ensure_legend_position(page_xml)
        return center_unresolved_leaf_labels(page_xml)

    if "<mxfile" not in (xml or ""):
        return _finalize(xml)
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError:
        return xml
    names = [d.get("name") or f"Page-{i + 1}" for i, d in enumerate(tree.findall("diagram"))]
    if len(names) <= 1:
        return _finalize(xml)
    pages: list[tuple[str, str]] = []
    for name in names:
        page = extract_page_by_name(xml, name) or xml
        pages.append((name, _finalize(page)))
    return merge_mxfile_pages(pages)


def _collect_degraded_features(logger: Any) -> list[dict[str, str]]:
    """Surface parallel branches that soft-failed (quota, quality gate, or
    branch exception) so the UI can show one banner instead of a missing tab.
    """
    entries = getattr(logger, "entries", None) if logger else None
    if not entries:
        return []
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for entry in entries:
        details = getattr(entry, "details", None) or {}
        if not (details.get("quota_exceeded") or details.get("degraded")):
            continue
        feature = details.get("feature") or entry.step
        if feature in seen:
            continue
        seen.add(feature)
        reason = "quota" if details.get("quota_exceeded") else str(details.get("reason") or "degraded")
        out.append({"feature": feature, "message": entry.message, "reason": reason})
    return out


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
    generation_path: str = "direct_xml",
) -> dict[str, Any]:
    """Build the standard API response dict."""
    return {
        "status": status,
        "xml": xml,
        "mermaid": None,
        "suggestions": suggestions or [],
        "assistant_message": assistant_message,
        "renderer_used": renderer_used,
        # "ir" when the diagram was compiled from a validated NetworkIR, else
        # "direct_xml". Distinct from renderer_used (mcp/local), which describes
        # icon resolution, not how the structure itself was produced.
        "generation_path": generation_path,
        "mcp_preview_url": mcp_preview_url,
        "cost_estimate": cost_estimate,
        "well_architected": well_architected,
        "compliance": compliance,
        "llm_usage": _summarize_llm_usage(logger),
        "logs": logger.to_list() if logger else [],
        "degraded_features": _collect_degraded_features(logger),
    }


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


async def _generate_primary_page(
    *,
    primary_view: str,
    human_prompt: str,
    provider_key: str,
    platform_key: str,
    logger: Any,
    messages: list[dict[str, Any]] | None,
    custom_system_message: str,
    applied_suggestions: list[Any] | None,
    enriched_context: str | None,
    attachment_documents: list[dict[str, Any]] | None,
    current_xml: str | None,
    image_replicate: bool,
    animated_connectors: bool,
    include_suggestions: bool,
    three_d: bool,
    on_progress: ProgressCallback | None,
) -> tuple[Any, bool]:
    """Generate the primary page.

    * Fresh cloud → IR only
    * Edit → surgical XML patch, else IR regenerate (no core.txt rewrite)
    * 3D → isometric XML pack only

    Returns ``(xml_diagram, used_ir_path)``.
    """

    async def _ir_primary(*, context_override: str | None = None) -> Any:
        _require_ir_or_raise(primary_view, context="Primary page")
        await _emit(on_progress, "ir", "Generating validated architecture model…")
        ctx = enriched_context if context_override is None else context_override
        diagram = await run_llm_sync(
            generate_xml_diagram_from_ir, primary_view, human_prompt, provider_key, platform_key, logger,
            messages, custom_system_message, applied_suggestions, ctx,
            attachment_documents,
            image_replicate=image_replicate,
            include_suggestions=include_suggestions,
        )
        await _emit(on_progress, "ir", "Architecture model compiled")
        if 'vertex="1"' not in (diagram.xml or ""):
            if logger:
                logger.log("error", "IR compiler returned no diagram content", level="error")
            raise ValueError("IR compiler returned no diagram content — nothing to validate or enrich.")
        try:
            ET.fromstring(ensure_diagram_document(diagram.xml, PLATFORM_LABELS.get(platform_key, "Architecture")))
        except ET.ParseError as exc:
            if logger:
                logger.log(
                    "xml_diagram",
                    f"IR-compiled XML was malformed ({exc})",
                    level="error",
                )
            raise ValueError(
                f"IR-compiled diagram XML was incomplete or malformed ({exc}). Retry the request."
            ) from exc
        return diagram

    async def _three_d_primary() -> Any:
        diagram = await run_llm_sync(
            generate_xml_diagram, human_prompt, provider_key, platform_key, logger, messages,
            custom_system_message, applied_suggestions, True, enriched_context, attachment_documents,
            current_xml=current_xml,
            image_replicate=image_replicate,
            animated_connectors=animated_connectors,
            include_suggestions=include_suggestions,
            primary_view=primary_view,
        )
        if 'vertex="1"' not in (diagram.xml or ""):
            raise ValueError("LLM returned no diagram content — nothing to validate or enrich.")
        try:
            ET.fromstring(ensure_diagram_document(diagram.xml, PLATFORM_LABELS.get(platform_key, "Architecture")))
        except ET.ParseError as exc:
            if logger:
                logger.log(
                    "xml_diagram",
                    f"Generated XML was incomplete or malformed ({exc}) — retrying once",
                    level="warning",
                )
            await _emit(on_progress, "llm", "First attempt was incomplete, retrying diagram generation…")
            diagram = await run_llm_sync(
                generate_xml_diagram, human_prompt, provider_key, platform_key, logger, messages,
                custom_system_message, applied_suggestions, True, enriched_context, attachment_documents,
                current_xml=current_xml,
                image_replicate=image_replicate,
                animated_connectors=animated_connectors,
                include_suggestions=include_suggestions,
                primary_view=primary_view,
                retry_hint=(
                    "Your previous response was cut off or malformed before the diagram XML "
                    "was complete. Regenerate the COMPLETE 3D diagram from scratch."
                ),
            )
            if 'vertex="1"' not in (diagram.xml or ""):
                raise ValueError("LLM returned no diagram content on retry — nothing to validate or enrich.")
        return diagram

    if three_d:
        return await _three_d_primary(), False

    if current_xml and len(current_xml) > 200:
        # current_xml from the frontend is the full multi-page mxfile (tab order
        # is always Logical first — see VIEW_ORDER), but the edit-plan/apply code
        # below has no concept of multiple pages. Scope to just the page being
        # edited so it doesn't read or mutate the wrong tab (e.g. Logical instead
        # of Network).
        primary_page_name = VIEW_PAGE_NAMES.get(primary_view, "Network")
        scoped_current_xml = extract_page_by_name(current_xml, primary_page_name) or current_xml
        try:
            diagram = await run_llm_sync(
                generate_xml_diagram, human_prompt, provider_key, platform_key, logger, messages,
                custom_system_message, applied_suggestions, False, enriched_context, attachment_documents,
                current_xml=scoped_current_xml,
                image_replicate=image_replicate,
                animated_connectors=animated_connectors,
                include_suggestions=include_suggestions,
                primary_view=primary_view,
            )
            return diagram, False
        except NonSurgicalEditError as exc:
            if logger:
                logger.log(
                    "edit",
                    f"{exc} — regenerating via IR from current diagram inventory",
                    level="warning",
                )
            await _emit(on_progress, "ir", "Redesign requested — regenerating via IR…")
            from app.adapters.outbound.diagram.architecture_docs import build_architecture_inventory
            inv = build_architecture_inventory(
                current_xml, service_limit=100, request_text=human_prompt or "",
            )
            seeded = "\n\n".join(
                p for p in (
                    enriched_context,
                    "EXISTING DIAGRAM INVENTORY — every item below already exists on this "
                    "diagram (including anything added by an earlier edit in this session) "
                    "and MUST still appear in your output with its topology and labels intact "
                    "unless the user's request explicitly asks to remove or replace it. Apply "
                    "only the requested changes on top of this inventory — do not invent an "
                    "unrelated architecture and do not silently drop an existing item:\n" + inv,
                ) if p
            )
            return await _ir_primary(context_override=seeded), True

    return await _ir_primary(), True


async def _run_generic_image_pipeline(
    *,
    attachment_documents: list[dict[str, Any]] | None,
    human_prompt: str,
    provider_key: str,
    logger: Any,
    messages: list[dict[str, Any]] | None,
    custom_system_message: str,
    applied_suggestions: list[Any] | None,
    on_progress: ProgressCallback | None,
    diagram_renderer: str | None = None,
) -> dict[str, Any]:
    """As-is image replication: generic IR + compiler, skipping every
    cloud-specific enrichment step (platform styling, overview panel, SaaS
    shape stamping, multi-view fan-out, cost/WAF/compliance/ADR/RAID) — none
    of it applies to a non-cloud traced diagram. The one cloud-path mechanism
    reused here is icon resolution: a node with an explicit ``icon_hint``
    gets a best-effort MCP ``search_shapes`` lookup, same merge-after-compile
    convention the cloud path uses (see ``compiler/generic.py``'s module
    docstring) — everything else about this diagram stays untouched.
    """
    page_name = "Diagram"
    await _emit(on_progress, "ir", "Reproducing uploaded diagram as-is…")
    diagram = await run_llm_sync(
        generate_generic_diagram_from_ir, human_prompt, provider_key, logger,
        messages, custom_system_message, applied_suggestions, attachment_documents,
    )
    await _emit(on_progress, "ir", "Diagram compiled")
    if 'vertex="1"' not in (diagram.xml or ""):
        if logger:
            logger.log("error", "Generic IR compiler returned no diagram content", level="error")
        raise ValueError("Generic IR compiler returned no diagram content — nothing to validate.")
    try:
        ET.fromstring(ensure_diagram_document(diagram.xml, page_name))
    except ET.ParseError as exc:
        if logger:
            logger.log("xml_diagram", f"Generic IR-compiled XML was malformed ({exc})", level="error")
        raise ValueError(
            f"Generic IR-compiled diagram XML was incomplete or malformed ({exc}). Retry the request."
        ) from exc

    await _emit(on_progress, "validate", "Validating and normalizing XML…")
    xml_output = ensure_diagram_document(diagram.xml, page_name)
    check = validate_and_fix_xml(xml_output)
    xml_output = choose_fixed_xml(xml_output, check)

    await _emit(on_progress, "repair", "Checking for overlaps, dangling edges, missing labels…")
    pre_repair_diagnostics = diagnose_xml(xml_output)
    xml_output, diagnostics = repair_xml(xml_output)
    fixed_count = (
        (len(pre_repair_diagnostics.errors) + len(pre_repair_diagnostics.warnings))
        - (len(diagnostics.errors) + len(diagnostics.warnings))
    )
    if fixed_count > 0 and logger:
        logger.log(
            "repair", f"Auto-fixed {fixed_count} diagram issue(s)", level="success",
            details={
                "before": len(pre_repair_diagnostics.errors) + len(pre_repair_diagnostics.warnings),
                "after": len(diagnostics.errors) + len(diagnostics.warnings),
            },
        )
    if diagnostics.errors or diagnostics.warnings:
        if logger:
            logger.log(
                "ir_residual",
                "Deterministic repair left residual issues (no LLM repair — format/structure "
                "fixes are auto-only; remaining items are logged for visibility)",
                level="warning",
                details={"errors": diagnostics.errors, "warnings": diagnostics.warnings},
            )
        await _emit(on_progress, "repair", "Deterministic repair complete")

    layout_issues = validate_layout(xml_output)
    if layout_issues and logger:
        logger.log(
            "repair", f"Layout validation found {len(layout_issues)} informational issue(s)",
            level="warning", details={"issues": layout_issues},
        )

    renderer_used = "local"
    if is_xml_pipeline_mode(diagram_renderer):
        shape_types = extract_shape_types(xml_output) or []
        if shape_types:
            await _emit(on_progress, "mcp", "Resolving icon styles…")
            try:
                # platform="any": icon_hint is freeform text describing a brand/
                # cloud-service icon, not a closed AWS/GCP/Azure shape_type
                # vocabulary — no vendor-library filtering should apply.
                resolved = await resolve_shape_styles(shape_types, platform="any", logger=logger)
            except Exception as exc:
                resolved = {}
                if logger:
                    logger.log("mcp", f"Icon resolve skipped: {exc}", level="warning")
            if resolved:
                xml_output = apply_mcp_styles(xml_output, resolved)
                renderer_used = "mcp"
                if logger:
                    logger.log(
                        "mcp", f"Applied {len(resolved)} MCP icon style(s)", level="success",
                        details={"resolved_count": len(resolved)},
                    )
                await _emit(on_progress, "mcp", f"Applied {len(resolved)} icon style(s)")

    xml_output, xml_ok, xml_failure = guarantee_loadable_xml(xml_output, page_name)
    if not xml_ok and logger:
        logger.log(
            "error",
            "Diagram XML failed to parse after every repair pass — served an "
            "empty canvas instead of a broken diagram file",
            level="error",
            details=xml_failure,
        )

    if logger:
        logger.log("request", "Generic (as-is) image replication complete", level="success", details={
            "xml_chars": len(xml_output),
        })

    return _build_response(
        status="success",
        xml=xml_output,
        assistant_message=diagram.assistant_message or "Reproduced the uploaded diagram as-is.",
        suggestions=[],
        renderer_used=renderer_used,
        generation_path="generic_ir",
        logger=logger,
    )


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
    image_mode: str = "cloud",
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
    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")

    # Auto image-replicate when vision attachments are present unless explicitly off
    if attachment_documents and not image_replicate:
        image_replicate = True

    views = normalize_diagram_views(diagram_views)
    # An uploaded image may itself depict a specific lens (a Network/VPC topology,
    # a Security/trust-zone diagram, a Data-flow pipeline) rather than the generic
    # Logical default — classify it so that lens becomes the faithfully-replicated
    # primary page, with Logical (and any other checked views) still fanning out
    # from it, same as today's Network-primary mechanism.
    detected_view: str | None = None
    has_image_attachment = attachment_documents and any(
        (doc.get("mime") or "").startswith("image/") for doc in attachment_documents
    )
    # PDFs are sent as vision blocks in this app specifically to preserve an
    # embedded diagram/screenshot (see documents.py) — a PDF export of a
    # flowchart is exactly as eligible for as-is replication as a PNG of one.
    # Cloud-view classification (below) stays image-only: _classify_attachment_view's
    # structured output is scoped to the 4 cloud lenses, unrelated to this.
    has_visual_attachment = attachment_documents and any(
        (doc.get("mime") or "").startswith("image/") or doc.get("mime") == "application/pdf"
        for doc in attachment_documents
    )
    if has_visual_attachment and image_mode == "as_is" and not (current_xml or "").strip() and not three_d:
        return await _run_generic_image_pipeline(
            attachment_documents=attachment_documents,
            human_prompt=human_prompt,
            provider_key=provider_key,
            logger=logger,
            messages=messages,
            custom_system_message=custom_system_message,
            applied_suggestions=applied_suggestions,
            on_progress=on_progress,
            diagram_renderer=diagram_renderer,
        )
    if has_image_attachment and not (current_xml or "").strip():
        await _emit(on_progress, "classify", "Identifying diagram type from image…")
        detected_view = await run_llm_sync(
            _classify_attachment_view,
            attachment_documents=attachment_documents,
            human_prompt=human_prompt,
            provider_key=provider_key,
            logger=logger,
        )
        if detected_view and detected_view != "logical" and detected_view not in views:
            views = normalize_diagram_views([*views, detected_view])

    primary_view = (
        detected_view if (detected_view and detected_view != "logical")
        else pick_primary_view(views)
    )
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
        "detected_view": detected_view,
        "adr_notes": adr_notes,
        "raid_register": raid_register,
    })
    await _emit(on_progress, "llm", "Generating diagram XML…", {
        "platform": platform_key, "provider": provider_key,
    })

    # Pre-generation gap analysis: default missing security/resilience/observability/
    # data components before anything is drawn, so every view (primary + fan-out)
    # starts from an architecturally complete spec instead of the bare brief. Skipped
    # for edits of an existing diagram (current_xml present) — there is nothing to
    # "default" when the user is editing something already drawn.
    gap_context_block: str | None = None
    if get_config().gap_analysis_enabled and not (current_xml or "").strip():
        await _emit(on_progress, "gap", "Analyzing architecture gaps…")
        gap_result = await run_llm_sync(
            _run_gap_analysis,
            human_prompt=human_prompt,
            platform_key=platform_key,
            interview_answers=interview_answers,
            views=views,
            provider_key=provider_key,
            logger=logger,
        )
        gap_context_block = build_gap_analysis_block(gap_result, views)
        if logger:
            logger.log(
                "gap", "Gap analysis complete", level="success",
                details={
                    "domain": gap_result.inferred_domain,
                    "additions": [a.component for a in gap_result.required_additions],
                    "assumptions": len(gap_result.assumptions),
                },
            )
        await _emit(on_progress, "gap", "Gap analysis complete")

    # Token-light context: interview answers + primary-view focus only.
    # Extra views / ADR / RAID are separate cheaper passes — never ask the main
    # XML call to emit multipage docs (that left Network/Security empty).
    context_parts = [p for p in (
        context_text or "",
        build_interview_prompt_block(interview_answers),
        build_views_prompt_block(views),
        gap_context_block or "",
    ) if p]
    enriched_context = "\n\n".join(context_parts) if context_parts else None

    xml_diagram, used_ir_path = await _generate_primary_page(
        primary_view=primary_view,
        human_prompt=human_prompt,
        provider_key=provider_key,
        platform_key=platform_key,
        logger=logger,
        messages=messages,
        custom_system_message=custom_system_message,
        applied_suggestions=applied_suggestions,
        enriched_context=enriched_context,
        attachment_documents=attachment_documents,
        current_xml=current_xml,
        image_replicate=image_replicate,
        animated_connectors=animated_connectors,
        include_suggestions=include_suggestions,
        three_d=three_d,
        on_progress=on_progress,
    )

    await _emit(on_progress, "validate", "Validating and normalizing XML…")
    xml_output = ensure_diagram_document(xml_diagram.xml, primary_page_name)
    check = validate_and_fix_xml(xml_output)
    xml_output = choose_fixed_xml(xml_output, check)
    xml_output = normalize_platform_styles(xml_output, platform_key)

    derived_story_markers: list[Any] = []
    if used_ir_path and not page_has_overview_panel(xml_output):
        # Gap A (Fix 3): the IR compilers never emit an overview_panel cell —
        # only the secondary/extra-views path (_finalize_view_page_xml) wired
        # this in previously, leaving IR-compiled primary pages with none at
        # all. Injected before repair_xml so the panel gets positioned/sized
        # by the normal repair pass, same as every other page's panel.
        xml_output = ensure_ir_overview_panel(xml_output, primary_page_name, xml_diagram.ir_title)

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
                "ir_residual" if used_ir_path else "repair",
                "Deterministic repair left residual issues (no LLM repair — format/structure "
                "fixes are auto-only; remaining items are logged for visibility)"
                + ("; IR geometry should already be clean — residuals are structural" if used_ir_path else ""),
                level="warning",
                details={"errors": diagnostics.errors, "warnings": diagnostics.warnings},
            )
        await _emit(on_progress, "repair", "Deterministic repair complete")

    # Informational only (Fix 2e) — container/edge sanity checks distinct from
    # diagnose_xml's errors/warnings, which gate repair behavior. Logged for
    # visibility, never blocks the response.
    layout_issues = validate_layout(xml_output)
    if layout_issues and logger:
        logger.log(
            "repair",
            f"Layout validation found {len(layout_issues)} informational issue(s)",
            level="warning",
            details={"issues": layout_issues},
        )

    # ------------------------------------------------------------------
    # Post-Network fan-out: views + ADR + RAID + cost/WAF/compliance + MCP
    # resolve overlap on the same wall-clock window. Diagram return must not
    # wait on a waterfall of serial LLM/enrichment stages.
    # ------------------------------------------------------------------
    surgical = _was_successful_surgical_edit(logger)
    # Extra IR views regenerate after surgical edits so Logical/Security/Data-flow
    # tabs stay aligned with the edited primary. ADR/RAID stay skipped (docs cost
    # vs. value on a rename/move).
    if surgical and (adr_notes or raid_register):
        if logger:
            logger.log(
                "docs",
                "Skipping ADR/RAID regeneration after surgical edit",
                level="info",
            )

    # Primary badges before merge — post-merge apply_step_markers hits page 1 only.
    # Re-synthesize overview + derive markers *after* repair so panel steps and
    # badges share the final cell set (dangling edges / remounts already applied).
    if used_ir_path:
        xml_output = refresh_ir_overview_panel(
            xml_output, primary_page_name, xml_diagram.ir_title or "",
        )
        derived_story_markers = derive_story_markers(xml_output, primary_page_name)
    story_markers = xml_diagram.step_markers or derived_story_markers
    if story_markers:
        xml_output = apply_step_markers(xml_output, story_markers, logger)
        xml_output = reposition_step_badges(xml_output)

    # Stamp curated saas_* on primary leaves before MCP resolve (all architecture views).
    if primary_view in ("network", "security", "data_flow", "logical"):
        from app.adapters.outbound.diagram.architecture_docs import assign_saas_shape_types
        if primary_view == "logical":
            from app.adapters.outbound.diagram.architecture_docs import (
                assign_logical_shape_types,
                ensure_saas_labels_on_logical,
            )
            xml_output = ensure_saas_labels_on_logical(xml_output)
            xml_output = assign_logical_shape_types(xml_output)
        xml_output = assign_saas_shape_types(xml_output)

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
        await _emit(on_progress, "views", f"Generating {title} view…")

        # Extra views for fresh multi-view are IR-only (same contract as primary).
        _require_ir_or_raise(view_key, context=f"{title} view")

        async def _once(context: str | None) -> tuple[str, Any]:
            xml = await run_llm_sync(
                _generate_ir_view_page,
                human_prompt=human_prompt,
                platform_key=platform_key,
                primary_xml=primary_xml_snapshot,
                provider_key=view_provider,
                view=view_key,
                logger=logger,
                context_text=context,
                attachment_documents=attachment_documents,
                image_replicate=image_replicate,
            )
            if not xml:
                raise ValueError(f"{title} IR view returned empty XML")
            return _score_and_maybe_keep(
                xml,
                view=view_key,
                primary_xml=primary_xml_snapshot,
                human_prompt=human_prompt or "",
                min_score=min_score,
            )

        page_xml, scored = await _once(enriched_context)
        # One corrective IR retry when below VIEW_QUALITY_MIN_SCORE, then keep
        # the best attempt (soft-degrade) rather than dropping the tab.
        if not scored.passes:
            feedback = "; ".join(
                (v.message or v.code) for v in (scored.violations or [])[:6]
            ) or "raise leaf density and fix viewpoint violations"
            retry_ctx = "\n\n".join(
                p for p in (
                    enriched_context,
                    (
                        f"QUALITY CORRECTION — previous {title} view scored "
                        f"{scored.score}/{min_score}. Regenerate a complete IR that fixes:\n"
                        f"{feedback}"
                    ),
                ) if p
            )
            try:
                retry_xml, scored2 = await _once(retry_ctx or None)
                if scored2.score >= scored.score:
                    page_xml, scored = retry_xml, scored2
                    if logger:
                        logger.log(
                            "views",
                            f"{title} quality retry scored {scored.score}",
                            level="info" if scored.passes else "warning",
                            details={
                                "score": scored.score,
                                "violations": [v.code for v in scored.violations][:8],
                                "retry": True,
                            },
                        )
            except Exception as exc:
                if logger:
                    logger.log(
                        "views",
                        f"{title} quality retry failed: {exc}",
                        level="warning",
                    )

        if logger:
            codes = [v.code for v in scored.violations][:8]
            details: dict[str, Any] = {
                "score": scored.score,
                "violations": codes,
                "metrics": scored.metrics,
            }
            if not scored.passes:
                details.update({
                    "degraded": True,
                    "feature": f"{title} view quality",
                    "reason": "quality_gate",
                })
            logger.log(
                "views",
                f"{title} view scored {scored.score}",
                level="info" if scored.passes else "warning",
                details=details,
            )
        await _emit(on_progress, "views", f"{title} view ready (score {scored.score})")
        return view_key, page_xml

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
    if extra_views:
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
                feature_name = {
                    "view": "Additional view generation", "adr": "ADR notes",
                    "raid": "RAID register", "cost": "Cost estimate",
                    "waf": "Well-Architected review", "compliance": "Compliance assessment",
                    "mcp_primary": "Icon resolution",
                }.get(kind, kind)
                details = (
                    {"quota_exceeded": True, "feature": feature_name}
                    if is_quota_or_rate_limit_error(raw) else None
                )
                if kind == "view" and details is None:
                    details = {
                        "degraded": True,
                        "feature": feature_name,
                        "reason": "timeout" if isinstance(raw, asyncio.TimeoutError) else "error",
                    }
                elif kind == "view" and details is not None:
                    details = {**details, "degraded": True, "feature": feature_name}
                logger.log(label, f"{kind} branch failed: {detail}", level="warning", details=details)
            # Soft-degrade failed secondary views: keep primary + any siblings that
            # succeeded. Hard-failing the whole request for one bad tab was worse UX.
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
                # Same canonical tab order as _merge_ordered_view_pages.
                order_names = [VIEW_PAGE_NAMES[v] for v in VIEW_ORDER if VIEW_PAGE_NAMES.get(v)]
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
                    f"Merged view pages: "
                    f"{[VIEW_PAGE_NAMES[v] for v in VIEW_ORDER if v == primary_view or v in extra_by_view]}",
                    level="success",
                    details={"extra_returned": len(extra_by_view), "primary_view": primary_view},
                )
        except Exception as exc:
            if logger:
                # A requested secondary view (Security/Data-flow/etc.) silently
                # not appearing as a tab is exactly the "missing secondary tab"
                # case _collect_degraded_features exists for — must flag degraded.
                logger.log(
                    "views", f"Extra views merge skipped: {exc}", level="warning",
                    details={"degraded": True, "feature": "extra views", "reason": str(exc)},
                )

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

            if use_libavoid:
                await _emit(on_progress, "mcp", "Applying libavoid edge routing…")
                pre_routing_xml = local_xml
                try:
                    tree_la = ET.fromstring(ensure_diagram_document(local_xml, primary_page_name))
                except ET.ParseError:
                    tree_la = None
                routed_pages: list[tuple[str, str]] = []
                routing_ok = True
                if tree_la is not None and tree_la.tag == "mxfile":
                    diagrams = list(tree_la.findall("diagram"))
                else:
                    diagrams = []
                from app.adapters.outbound.diagram.edge_routing import (
                    restamp_edge_ports_after_external_routing,
                )
                _FOCUSED_PAGES = {"Logical", "Security", "Data flow"}
                if len(diagrams) <= 1:
                    single_name = ""
                    if diagrams:
                        single_name = diagrams[0].get("name") or ""
                    _LOCAL_ROUTED_PAGES = (
                        set() if get_config().libavoid_focused_views else _FOCUSED_PAGES
                    )
                    if single_name in _LOCAL_ROUTED_PAGES:
                        if logger:
                            logger.log(
                                "mcp",
                                f"Libavoid skipped for {single_name} — local router owns edges",
                                level="info",
                            )
                        routed_xml = None
                    else:
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
                                if single_name in _FOCUSED_PAGES or single_name == "Network":
                                    candidate = restamp_edge_ports_after_external_routing(candidate)
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
                else:
                    # Route each page alone to avoid duplicate cell-id collisions across tabs.
                    # Focused views (Logical / Security / Data flow) now go through libavoid
                    # too by default (LIBAVOID_FOCUSED_VIEWS) — label positions are re-parked
                    # onto libavoid's own waypoints afterward rather than re-routed locally.
                    _LOCAL_ROUTED_PAGES = (
                        set() if get_config().libavoid_focused_views else _FOCUSED_PAGES
                    )
                    for diag in diagrams:
                        name = diag.get("name") or "Page"
                        if name in {"ADR Notes", "RAID"} or name in _LOCAL_ROUTED_PAGES:
                            inner = "".join(ET.tostring(c, encoding="unicode") for c in list(diag))
                            routed_pages.append((name, ensure_diagram_document(inner, name)))
                            continue
                        inner = "".join(ET.tostring(c, encoding="unicode") for c in list(diag))
                        page_xml = ensure_diagram_document(inner, name)
                        try:
                            _preview, routed_one = await create_diagram(
                                page_xml, routing="libavoid", logger=logger,
                            )
                            if _preview and not preview_url:
                                preview_url = _preview
                        except DrawioMcpError as exc:
                            if logger:
                                logger.log(
                                    "mcp",
                                    f"Libavoid skipped for page {name!r}: {exc}",
                                    level="warning",
                                )
                            routed_one = None
                        if not routed_one:
                            routed_pages.append((name, page_xml))
                            routing_ok = False
                            continue
                        if routed_one.lower().count("<mxfile") > 1:
                            routed_pages.append((name, page_xml))
                            routing_ok = False
                            continue
                        routed_check = validate_and_fix_xml(routed_one)
                        candidate = choose_fixed_xml(routed_one, routed_check)
                        try:
                            ET.fromstring(candidate)
                            if candidate.lower().count("<mxfile") > 1:
                                raise ET.ParseError("nested mxfile")
                            if name in _FOCUSED_PAGES or name == "Network":
                                candidate = restamp_edge_ports_after_external_routing(candidate)
                            routed_pages.append((name, ensure_diagram_document(candidate, name)))
                        except ET.ParseError:
                            routed_pages.append((name, page_xml))
                            routing_ok = False
                    if routed_pages:
                        local_xml = merge_mxfile_pages(routed_pages)
                        if logger:
                            logger.log(
                                "mcp",
                                "Libavoid routing applied per page"
                                + ("" if routing_ok else " (some pages kept pre-route)"),
                                level="success" if routing_ok else "warning",
                            )
                    else:
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

    # Containers / libavoid can shift leaves after badges were parked.
    xml_output = reposition_step_badges(xml_output)

    # repair_xml() already positioned the Overview Panel/legend once, earlier
    # in this function — but on the surgical-edit path (apply_diagram_edits
    # strips layout="ir" on any geometry op), fit_containers_to_children above
    # is what actually grows a container to enclose a newly added node, and it
    # runs AFTER that first pass. Recompute now that final bounds are settled;
    # both fixers no-op if the geometry is already correct (fresh IR path,
    # where layout="ir" made fit_containers_to_children a no-op above).
    xml_output = _refresh_overview_and_legend_positions(xml_output)

    # cost/WAF/compliance above ran in parallel with MCP icon resolution
    # against primary_xml_snapshot (a pre-resolution snapshot, deliberately —
    # so they don't wait on MCP) — accurate for what the LLM/edit intended,
    # but MCP occasionally resolves a shape_type to the wrong icon (confirmed
    # via live captures: KMS resolving to the Kinesis Data Firehose stencil),
    # and that divergence compounds turn over turn on the surgical-edit path
    # specifically: the NEXT edit's current_xml starts from THIS turn's
    # already-resolved (possibly already-wrong) output, with no shape_type
    # left for that later snapshot to fall back on — which is what made a
    # passing Well-Architected check flip to failing one edit later with no
    # findings actually regressing. Re-run against the final, fully-resolved
    # XML so the response always matches what the user actually sees,
    # instead of a guess from before icons were resolved.
    if (cost_estimate or well_architected or compliance) and "network" in views and primary_xml_snapshot:
        final_network_xml = extract_page_by_name(xml_output, "Network") or xml_output
        # Skip the redundant re-run entirely when MCP resolution didn't
        # actually change what services are detectable (e.g. diagram_renderer
        # == "local", which skips MCP outright, or a resolution that simply
        # confirmed the same services) — compare the SERVICE SET, not the raw
        # XML string, since geometry/whitespace changes from
        # fit_containers_to_children etc. don't affect what cost/WAF/
        # compliance actually analyze.
        if set(find_service_keys(final_network_xml)) != set(find_service_keys(primary_xml_snapshot)):
            if cost_estimate:
                cost_data = await asyncio.to_thread(estimate_monthly_cost, final_network_xml, platform_key)
            if well_architected:
                waf_data = await asyncio.to_thread(assess_well_architected, final_network_xml, platform_key)
                if logger:
                    logger.log(
                        "waf", f"Well-Architected (post-resolve): {waf_data['overall_status']}",
                        level="info", details={"well_architected": waf_data},
                    )
            if compliance:
                compliance_data = await asyncio.to_thread(
                    assess_compliance, xml=final_network_xml, platform=platform_key,
                    frameworks=compliance_frameworks,
                    provider=provider_key if compliance_frameworks else None,
                    logger=logger,
                )
                if logger:
                    logger.log(
                        "compliance", "Compliance assessment (post-resolve) complete",
                        level="info", details={"compliance": compliance_data},
                    )

    if logger:
        try:
            from app.adapters.outbound.diagram.xml_diagnostics import count_edge_crossings
            parsed_out = ET.fromstring(xml_output) if xml_output.strip() else None
            page_names = (
                [d.get("name") or "" for d in parsed_out.findall("diagram")]
                if parsed_out is not None and parsed_out.tag == "mxfile"
                else [primary_page_name]
            )
            for name in page_names:
                if name.lower() not in {"logical", "security", "data flow", "data_flow"}:
                    continue
                n_crossings = count_edge_crossings(xml_output, page=name)
                logger.log(
                    "layout", f"{name}: {n_crossings} edge crossing(s)",
                    level="warning" if n_crossings > 2 else "info",
                    details={"page": name, "crossings": n_crossings},
                )
        except Exception:
            pass  # crossing count is a diagnostic signal only — never fail the response over it

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
                # adr_list/raid_list were already generated (real LLM output) —
                # failing to attach them to the diagram here silently discards
                # a feature the user explicitly toggled on, same class of bug
                # as the suggestions-call swallow this pattern was added for.
                logger.log(
                    "docs", f"Documentation pages skipped: {exc}", level="warning",
                    details={"degraded": True, "feature": "ADR/RAID pages", "reason": str(exc)},
                )


    # Final circuit breaker — every escaping/corruption bug hit so far surfaced
    # here, at the exit point, regardless of which upstream step caused it.
    # Guarantee loadability once, right before the response leaves the service,
    # instead of trusting every step above to have been gap-free.
    if get_config().diagram_vlm_qa:
        from app.adapters.outbound.diagram.diagram_vlm_qa import run_visual_qa
        xml_output = run_visual_qa(xml_output, logger=logger)

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
            generation_path="ir" if used_ir_path else "direct_xml",
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
        generation_path="ir" if used_ir_path else "direct_xml",
    )
