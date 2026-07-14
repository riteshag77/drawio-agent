"""MCP-accelerated XML generation pipeline — LLM produces draw.io XML directly."""

from __future__ import annotations

import asyncio
from typing import Any

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.drawio_mcp_client import DrawioMcpError, create_diagram, resolve_shape_styles
from app.adapters.outbound.diagram.mode import is_xml_pipeline_mode
from app.adapters.outbound.diagram.xml_tools import (
    apply_mcp_styles,
    choose_fixed_xml,
    ensure_diagram_document,
    extract_shape_types,
    normalize_platform_styles,
    validate_and_fix_xml,
)
from app.adapters.outbound.llm.prompt_builders import PLATFORM_LABELS
from app.adapters.outbound.utils.chat import filter_applied_suggestions
from app.core.services.agent import generate_xml_diagram


def _build_response(
    status: str = "success",
    *,
    xml: str | None = None,
    assistant_message: str = "",
    suggestions: list[Any] | None = None,
    renderer_used: str | None = None,
    mcp_preview_url: str | None = None,
    logger: Any = None,
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
        "logs": logger.to_list() if logger else [],
    }


async def run_xml_pipeline(
    *,
    user_prompt: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    provider: str = "openai",
    platform: str = "aws",
    logger: Any = None,
    custom_system_message: str = "",
    diagram_renderer: str | None = None,
    applied_suggestions: list[Any] | None = None,
    three_d: bool = False,
) -> dict[str, Any]:
    provider_key = (provider or "openai").strip().lower()
    platform_key = (platform or "aws").strip().lower()

    logger.log("request", "MCP XML generation request", details={
        "platform": platform_key, "provider": provider_key,
        "renderer": diagram_renderer or get_config().diagram_renderer,
    })

    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")
    xml_diagram = await asyncio.to_thread(
        generate_xml_diagram, human_prompt, provider_key, platform_key, logger, messages,
        custom_system_message, applied_suggestions, three_d,
    )

    xml_output = ensure_diagram_document(xml_diagram.xml, PLATFORM_LABELS.get(platform_key, "Architecture"))
    check = validate_and_fix_xml(xml_output)
    xml_output = choose_fixed_xml(xml_output, check)
    xml_output = normalize_platform_styles(xml_output, platform_key)

    if not is_xml_pipeline_mode(diagram_renderer):
        logger.log("complete", "XML pipeline finished (local mode)", level="success",
                   details={"xml_chars": len(xml_output), "renderer_used": "local"})
        return _build_response(
            xml=xml_output,
            assistant_message=xml_diagram.assistant_message,
            suggestions=filter_applied_suggestions(xml_diagram.suggestions, applied_suggestions),
            renderer_used="local",
            logger=logger,
        )

    # MCP enrichment
    preview_url = None
    renderer_used = "mcp"
    try:
        # Prefer shape_types from normalized output (may still carry attrs from LLM)
        shape_types = extract_shape_types(xml_output) or extract_shape_types(xml_diagram.xml)

        overrides: dict[str, str] = {}
        if shape_types:
            overrides = await resolve_shape_styles(shape_types, platform=platform_key, logger=logger)
            if overrides:
                xml_output = apply_mcp_styles(xml_output, overrides)
                if logger:
                    logger.log(
                        "mcp",
                        f"Applied {len(overrides)} MCP icon styles to diagram",
                        level="success",
                        details={"resolved": {k: v[:80] for k, v in overrides.items()}},
                    )

        if get_config().drawio_mcp_libavoid:
            preview_url, routed_xml = await create_diagram(xml_output, routing="libavoid", logger=logger)
            if routed_xml:
                xml_output = ensure_diagram_document(routed_xml)
                if logger:
                    logger.log("mcp", "Libavoid routing applied", level="success",
                                details={"routed_xml_chars": len(routed_xml), "routed_xml_preview": routed_xml})

    except DrawioMcpError as exc:
        if logger:
            logger.log("mcp", f"MCP enrichment skipped: {exc}", level="warning")
        renderer_used = "local"
    except Exception as exc:
        if logger:
            logger.log("mcp", f"MCP error: {exc}", level="warning")
        renderer_used = "local"

    logger.log("complete", "XML pipeline finished", level="success",
               details={"xml_chars": len(xml_output), "renderer_used": renderer_used})
    return _build_response(
        xml=xml_output,
        assistant_message=xml_diagram.assistant_message,
        suggestions=filter_applied_suggestions(xml_diagram.suggestions, applied_suggestions),
        renderer_used=renderer_used,
        mcp_preview_url=preview_url,
        logger=logger,
    )
