"""MCP-accelerated XML generation pipeline — LLM produces draw.io XML directly."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.drawio_mcp_client import DrawioMcpError, create_diagram, resolve_shape_styles
from app.adapters.outbound.diagram.mode import is_xml_pipeline_mode
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.adapters.outbound.diagram.xml_tools import (
    apply_mcp_styles,
    choose_fixed_xml,
    ensure_diagram_document,
    extract_shape_types,
    fit_containers_to_children,
    normalize_platform_styles,
    validate_and_fix_xml,
)
from app.adapters.outbound.llm.concurrency import run_llm_sync
from app.adapters.outbound.llm.prompt_builders import PLATFORM_LABELS
from app.adapters.outbound.compliance import assess_compliance
from app.adapters.outbound.cost_estimator import estimate_monthly_cost
from app.adapters.outbound.utils.chat import filter_applied_suggestions
from app.adapters.outbound.well_architected import assess_well_architected
from app.core.services.agent import generate_xml_diagram, repair_xml_diagram

ProgressCallback = Callable[[str, str, dict[str, Any] | None], Any]


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
    breakdown: list[dict[str, Any]] = []
    for entry in calls:
        d = entry.details or {}
        total_input += d.get("input_tokens", 0)
        total_output += d.get("output_tokens", 0)
        total_tokens += d.get("total_tokens", 0)
        cost = d.get("estimated_cost_usd")
        if cost is not None:
            total_cost += cost
            has_cost = True
        breakdown.append({
            "provider": d.get("provider"),
            "model": d.get("model"),
            "input_tokens": d.get("input_tokens", 0),
            "output_tokens": d.get("output_tokens", 0),
            "total_tokens": d.get("total_tokens", 0),
            "estimated_cost_usd": cost,
        })
    return {
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_tokens,
        "estimated_cost_usd": round(total_cost, 4) if has_cost else None,
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
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    provider_key = (provider or "claude").strip().lower()
    platform_key = (platform or "aws").strip().lower()

    logger.log("request", "MCP XML generation request", details={
        "platform": platform_key, "provider": provider_key,
        "renderer": diagram_renderer or get_config().diagram_renderer,
    })
    await _emit(on_progress, "llm", "Generating diagram XML…", {
        "platform": platform_key, "provider": provider_key,
    })

    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")

    loop = asyncio.get_running_loop()

    def sync_progress(step: str, message: str, details: dict[str, Any] | None = None) -> None:
        if not on_progress:
            return

        def _schedule() -> None:
            asyncio.create_task(_emit(on_progress, step, message, details))

        loop.call_soon_threadsafe(_schedule)

    xml_diagram = await run_llm_sync(
        generate_xml_diagram, human_prompt, provider_key, platform_key, logger, messages,
        custom_system_message, applied_suggestions, three_d, context_text, attachment_documents,
        sync_progress if provider_key == "cursor" else None,
    )
    if 'vertex="1"' not in (xml_diagram.xml or ""):
        # A successful call with no diagram content (empty/near-empty xml) is not
        # a partial success — nothing downstream should validate/repair/enrich
        # what is effectively a blank canvas.
        if logger:
            logger.log("error", "LLM returned no diagram content", level="error")
        raise ValueError("LLM returned no diagram content — nothing to validate or enrich.")

    await _emit(on_progress, "validate", "Validating and normalizing XML…")
    xml_output = ensure_diagram_document(xml_diagram.xml, PLATFORM_LABELS.get(platform_key, "Architecture"))
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
            logger.log("repair", "Deterministic repair left unresolved issues", level="warning", details={
                "errors": diagnostics.errors, "warnings": diagnostics.warnings,
            })
        await _emit(on_progress, "repair", "Requesting a corrective pass…")
        repaired = await run_llm_sync(
            repair_xml_diagram, xml_output, diagnostics, provider_key, platform_key, logger,
        )
        if repaired.xml:
            xml_output = ensure_diagram_document(repaired.xml, PLATFORM_LABELS.get(platform_key, "Architecture"))
            check = validate_and_fix_xml(xml_output)
            xml_output = choose_fixed_xml(xml_output, check)
            xml_output = normalize_platform_styles(xml_output, platform_key)
            xml_output, diagnostics = repair_xml(xml_output)
            if logger:
                logger.log("repair", "Repair pass complete", level="success", details={
                    "remaining_errors": diagnostics.errors, "remaining_warnings": diagnostics.warnings,
                })
        await _emit(on_progress, "repair", "Repair complete")

    use_mcp = is_xml_pipeline_mode(diagram_renderer)
    shape_types: list[str] = []
    if use_mcp and xml_output:
        shape_types = extract_shape_types(xml_output) or extract_shape_types(xml_diagram.xml)

    async def _run_cost() -> dict | None:
        if not (cost_estimate and xml_output):
            return None
        data = await asyncio.to_thread(estimate_monthly_cost, xml_output, platform_key)
        if logger:
            logger.log(
                "cost",
                f"Estimated monthly cost: ${data['total']}",
                level="info",
                details={"service_count": data["services_matched"] + data["services_unmatched"]},
            )
        await _emit(on_progress, "cost", f"Estimated monthly cost: ${data['total']}")
        return data

    async def _run_waf() -> dict | None:
        if not (well_architected and xml_output):
            return None
        data = await asyncio.to_thread(assess_well_architected, xml_output, platform_key)
        if logger:
            logger.log(
                "waf",
                f"Well-Architected: {data['overall_status']}",
                level="info",
                details={"pillars": len(data["pillars"]), "services": data["services_analyzed"]},
            )
        await _emit(on_progress, "waf", f"Well-Architected: {data['overall_status']}")
        return data

    async def _run_compliance() -> dict | None:
        if not (compliance and xml_output):
            return None
        await _emit(on_progress, "compliance", "Assessing compliance…")
        data = await asyncio.to_thread(
            assess_compliance,
            xml=xml_output,
            platform=platform_key,
            frameworks=compliance_frameworks,
            provider=provider_key if compliance_frameworks else None,
            logger=logger,
        )
        await _emit(on_progress, "compliance", "Compliance assessment complete")
        return data

    async def _run_mcp() -> tuple[str, str | None, str]:
        """Return (xml, preview_url, renderer_used)."""
        if not use_mcp:
            return fit_containers_to_children(xml_output), None, "local"

        await _emit(on_progress, "mcp", "Resolving official icon styles…")
        local_xml = xml_output
        preview_url = None
        renderer_used = "mcp"
        try:
            overrides: dict[str, str] = {}
            if shape_types:
                overrides = await resolve_shape_styles(shape_types, platform=platform_key, logger=logger)
                if overrides:
                    local_xml = apply_mcp_styles(local_xml, overrides)
                    if logger:
                        logger.log(
                            "mcp",
                            f"Applied {len(overrides)} MCP icon styles to diagram",
                            level="success",
                            details={"resolved": {k: v[:80] for k, v in overrides.items()}},
                        )
                    await _emit(on_progress, "mcp", f"Applied {len(overrides)} icon styles")

            local_xml = fit_containers_to_children(local_xml)

            if get_config().drawio_mcp_libavoid:
                await _emit(on_progress, "mcp", "Applying libavoid edge routing…")
                preview_url, routed_xml = await create_diagram(local_xml, routing="libavoid", logger=logger)
                if routed_xml:
                    local_xml = ensure_diagram_document(routed_xml)
                    if logger:
                        logger.log(
                            "mcp",
                            "Libavoid routing applied",
                            level="success",
                            details={"routed_xml_chars": len(routed_xml)},
                        )
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
        return local_xml, preview_url, renderer_used

    await _emit(on_progress, "enrich", "Running post-analysis and icon enrichment…")
    cost_data, waf_data, compliance_data, mcp_result = await asyncio.gather(
        _run_cost(),
        _run_waf(),
        _run_compliance(),
        _run_mcp(),
    )
    xml_output, preview_url, renderer_used = mcp_result

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
