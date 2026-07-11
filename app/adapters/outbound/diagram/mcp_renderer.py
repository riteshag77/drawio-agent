"""Render ArchitectureDiagram XML with optional Draw.io MCP shape enrichment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.draftsman import prepare_and_generate_xml
from app.adapters.outbound.diagram.drawio_mcp_client import (
    DrawioMcpError,
    create_diagram,
    resolve_shape_styles,
)

if TYPE_CHECKING:
    from app.core.logging import StepLogger
    from app.domain.entities import ArchitectureDiagram


def _normalize_renderer(value: str | None) -> str:
    cfg_default = get_config().diagram_renderer
    raw = (value or cfg_default or "auto").strip().lower()
    return raw if raw in ("local", "mcp", "auto") else "auto"


async def render_architecture_xml(
    architecture: ArchitectureDiagram,
    platform: str = "aws",
    logger: StepLogger | None = None,
    *,
    diagram_renderer: str | None = None,
) -> tuple[str, str, str | None, ArchitectureDiagram]:
    """Return (xml, renderer_used, mcp_preview_url, canonical_architecture).

    LLM owns architecture; MCP resolves icon styles and optionally libavoid-routes edges.
    """
    mode = _normalize_renderer(diagram_renderer)
    preview_url: str | None = None

    if mode == "local":
        prepared, xml = prepare_and_generate_xml(architecture, platform, logger)
        return xml, "local", None, prepared

    shape_types = sorted({
        (n.shape_type or "").strip()
        for n in architecture.nodes
        if (n.shape_type or "").strip() and not n.is_container
    })

    try:
        overrides = await resolve_shape_styles(shape_types, platform=platform)
        if overrides and logger:
            logger.log("mcp", "Draw.io MCP styles applied", details={
                "resolved": len(overrides),
                "requested": len(shape_types),
            })
        elif logger:
            logger.log("mcp", "Draw.io MCP returned no styles; using local catalogue", level="warning")

        prepared, xml = prepare_and_generate_xml(
            architecture, platform, logger, style_overrides=overrides or None,
        )

        # Libavoid only helps when MCP returns updated mxGraph XML (preview-only = skip)
        preview_url, routed_xml = await create_diagram(xml, routing="libavoid")
        if routed_xml and ("<mxGraphModel" in routed_xml or "<mxfile" in routed_xml):
            from app.adapters.outbound.diagram.xml_tools import ensure_diagram_document

            xml = ensure_diagram_document(routed_xml)
            if logger:
                logger.log("mcp", "create_diagram libavoid XML applied", details={
                    "preview": (preview_url or "")[:200],
                })
            return xml, "mcp", preview_url, prepared

        if logger and preview_url:
            logger.log(
                "mcp",
                "create_diagram returned preview only (no XML); keeping local edges",
                level="warning",
                details={"preview": preview_url[:200]},
            )
        return xml, "mcp", None, prepared
    except DrawioMcpError as exc:
        if logger:
            logger.log("mcp", f"MCP unavailable, falling back to local: {exc}", level="warning")
        prepared, xml = prepare_and_generate_xml(architecture, platform, logger)
        return xml, "local", None, prepared
