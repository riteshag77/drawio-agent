"""Render ArchitectureDiagram XML with optional Draw.io MCP shape enrichment."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.draftsman import generate_xml
from app.adapters.outbound.diagram.drawio_mcp_client import (
    DrawioMcpError,
    create_diagram,
    resolve_shape_styles,
)
from app.adapters.outbound.diagram.styles import clear_style_overrides, set_style_overrides

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
) -> tuple[str, str, str | None]:
    """Return (xml, renderer_used, mcp_preview_url).

    LLM owns architecture; MCP optionally resolves icon styles before local layout.
    """
    mode = _normalize_renderer(diagram_renderer)
    preview_url: str | None = None

    if mode == "local":
        xml = generate_xml(architecture, platform, logger)
        return xml, "local", None

    shape_types = sorted({
        (n.shape_type or "").strip()
        for n in architecture.nodes
        if (n.shape_type or "").strip() and not n.is_container
    })

    try:
        overrides = await resolve_shape_styles(shape_types, platform=platform)
        if overrides:
            set_style_overrides(overrides)
            if logger:
                logger.log("mcp", "Draw.io MCP styles applied", details={
                    "resolved": len(overrides),
                    "requested": len(shape_types),
                })
        else:
            if logger:
                logger.log("mcp", "Draw.io MCP returned no styles; using local catalogue", level="warning")
        try:
            xml = generate_xml(architecture, platform, logger)
        finally:
            clear_style_overrides()

        if mode == "mcp":
            preview_url = await create_diagram(xml)
            if preview_url and logger:
                logger.log("mcp", "create_diagram preview", details={"preview": preview_url[:200]})

        return xml, "mcp", preview_url
    except DrawioMcpError as exc:
        clear_style_overrides()
        if logger:
            logger.log("mcp", f"MCP unavailable, falling back to local: {exc}", level="warning")
        xml = generate_xml(architecture, platform, logger)
        return xml, "local", None
