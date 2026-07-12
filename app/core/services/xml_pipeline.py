"""MCP-accelerated XML generation pipeline — LLM produces draw.io XML directly."""

from __future__ import annotations

import asyncio

from app.adapters.outbound.config.env_config import get_config


def _build_response(status="success", *, xml=None, assistant_message="",
                    suggestions=None, renderer_used=None, mcp_preview_url=None,
                    logger=None):
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
from app.adapters.outbound.diagram.drawio_mcp_client import DrawioMcpError, create_diagram, resolve_shape_styles
from app.adapters.outbound.diagram.mode import is_xml_pipeline_mode
from app.adapters.outbound.diagram.xml_tools import (
    choose_fixed_xml,
    ensure_diagram_document,
    validate_and_fix_xml,
)
from app.adapters.outbound.llm.prompt_builders import PLATFORM_LABELS
from app.adapters.outbound.utils.chat import filter_applied_suggestions
from app.core.services.agent import generate_xml_diagram


async def run_xml_pipeline(*, user_prompt=None, messages=None, provider="openai", platform="aws",
                           logger=None, custom_system_message="",
                           diagram_renderer=None, applied_suggestions=None,
                           three_d=False):
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
        shape_types = sorted({
            s.strip() for s in xml_diagram.xml.split("shape_type=")[1:] if s.strip()
        }) if "shape_type=" in xml_diagram.xml else []

        if shape_types:
            overrides = await resolve_shape_styles(shape_types, platform=platform_key, logger=logger)
            if overrides and logger:
                logger.log("mcp", "Styles enriched via MCP", details={"resolved": len(overrides)})

        if get_config().drawio_mcp_libavoid:
            preview_url, routed_xml = await create_diagram(xml_output, routing="libavoid", logger=logger)
            if routed_xml:
                xml_output = ensure_diagram_document(routed_xml)
                if logger:
                    logger.log("mcp", "Libavoid routing applied", level="success")

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
