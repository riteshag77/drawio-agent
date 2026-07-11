"""Draw.io hosted MCP client (search_shapes / create_diagram)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.adapters.outbound.config.env_config import get_config

logger = logging.getLogger(__name__)


class DrawioMcpError(RuntimeError):
    """Raised when the Draw.io MCP server cannot be used."""


def _content_to_text(result: Any) -> str:
    parts: list[str] = []
    content = getattr(result, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
        elif isinstance(item, dict) and item.get("text"):
            parts.append(str(item["text"]))
    if not parts and hasattr(result, "structuredContent") and result.structuredContent:
        parts.append(json.dumps(result.structuredContent))
    return "\n".join(parts).strip()


def _extract_styles(payload: str) -> list[str]:
    """Pull draw.io style strings from MCP search_shapes text/JSON."""
    styles: list[str] = []
    if not payload:
        return styles

    # JSON array / object with style fields
    try:
        data = json.loads(payload)
        items = data if isinstance(data, list) else data.get("shapes") or data.get("results") or [data]
        for item in items:
            if isinstance(item, dict):
                style = item.get("style") or item.get("styleString") or item.get("value")
                if style and isinstance(style, str) and ("shape=" in style or "image=" in style or "fillColor=" in style):
                    styles.append(style)
            elif isinstance(item, str) and ("shape=" in item or "image=" in item):
                styles.append(item)
        if styles:
            return styles
    except json.JSONDecodeError:
        pass

    # Heuristic: lines that look like style strings
    for line in payload.splitlines():
        line = line.strip().strip("`").strip('"').strip("'")
        if "shape=" in line or "resIcon=" in line or "image=img/lib/" in line:
            # strip leading labels like "style: "
            if ":" in line and not line.startswith("shape=") and "shape=" in line:
                line = line.split(":", 1)[-1].strip()
            styles.append(line.rstrip(";"))
    return styles


def _shape_query(shape_type: str, platform: str) -> str:
    st = (shape_type or "").lower()
    for prefix in ("aws_", "gcp_", "azure_"):
        if st.startswith(prefix):
            return st[len(prefix):].replace("_", " ")
    platform = (platform or "aws").lower()
    if platform == "gcp":
        return f"gcp {st.replace('_', ' ')}"
    if platform == "azure":
        return f"azure {st.replace('_', ' ')}"
    return st.replace("_", " ")


async def search_shapes(query: str, *, limit: int = 5) -> list[str]:
    """Return candidate draw.io style strings for a keyword query."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:
        raise DrawioMcpError("mcp package not installed; pip install mcp") from exc

    cfg = get_config()
    url = cfg.drawio_mcp_url
    timeout = cfg.drawio_mcp_timeout

    try:
        async with streamablehttp_client(url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(
                    "search_shapes",
                    {"query": query, "limit": limit},
                )
                text = _content_to_text(result)
                return _extract_styles(text)
    except DrawioMcpError:
        raise
    except Exception as exc:
        raise DrawioMcpError(f"Draw.io MCP search_shapes failed: {exc}") from exc


async def resolve_shape_styles(
    shape_types: list[str],
    platform: str = "aws",
) -> dict[str, str]:
    """Map shape_type → best MCP style string (first hit wins)."""
    resolved: dict[str, str] = {}
    seen_queries: dict[str, str] = {}

    for shape_type in shape_types:
        if not shape_type or shape_type in resolved:
            continue
        query = _shape_query(shape_type, platform)
        if query in seen_queries:
            resolved[shape_type] = seen_queries[query]
            continue
        try:
            styles = await search_shapes(query, limit=5)
        except DrawioMcpError:
            raise
        if styles:
            # Prefer resourceIcon / aws4 / gcp / azure matches
            pick = styles[0]
            for style in styles:
                low = style.lower()
                if "resourceicon" in low or "mxgraph.aws4" in low or "mxgraph.gcp" in low or "azure" in low:
                    pick = style
                    break
            if not pick.endswith(";"):
                pick += ";"
            resolved[shape_type] = pick
            seen_queries[query] = pick
            logger.info("MCP resolved %s via %r", shape_type, query)
        else:
            logger.info("MCP no style for %s (%r)", shape_type, query)

    return resolved


async def create_diagram(xml: str) -> str | None:
    """Best-effort create_diagram; returns preview text/URL if any."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError:
        return None

    cfg = get_config()
    try:
        async with streamablehttp_client(cfg.drawio_mcp_url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool("create_diagram", {"xml": xml})
                text = _content_to_text(result)
                match = re.search(r"https?://[^\s\"']+", text or "")
                return match.group(0) if match else (text[:500] if text else None)
    except Exception as exc:
        logger.warning("create_diagram skipped: %s", exc)
        return None
