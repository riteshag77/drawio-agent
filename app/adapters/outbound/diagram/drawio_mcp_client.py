"""Draw.io hosted MCP client (search_shapes / create_diagram)."""

from __future__ import annotations

import asyncio
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

    for line in payload.splitlines():
        line = line.strip().strip("`").strip('"').strip("'")
        if "shape=" in line or "resIcon=" in line or "image=img/lib/" in line:
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


def _pick_style(styles: list[str]) -> str | None:
    if not styles:
        return None
    pick = styles[0]
    for style in styles:
        low = style.lower()
        if "resourceicon" in low or "mxgraph.aws4" in low or "mxgraph.gcp" in low or "azure" in low:
            pick = style
            break
    if not pick.endswith(";"):
        pick += ";"
    return pick


# Process-level style cache: (platform, shape_type) → style string
_STYLE_CACHE: dict[tuple[str, str], str] = {}
_STYLE_CACHE_MAX = 512


def clear_style_cache() -> None:
    """Test helper."""
    _STYLE_CACHE.clear()


async def _mcp_call_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """One MCP session, one tool call, with configured timeout."""
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:
        raise DrawioMcpError("mcp package not installed; pip install mcp") from exc

    cfg = get_config()
    timeout = max(5, int(cfg.drawio_mcp_timeout))

    async def _run() -> str:
        async with streamablehttp_client(cfg.drawio_mcp_url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                return _content_to_text(result)

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise DrawioMcpError(f"Draw.io MCP {tool_name} timed out after {timeout}s") from exc
    except DrawioMcpError:
        raise
    except Exception as exc:
        raise DrawioMcpError(f"Draw.io MCP {tool_name} failed: {exc}") from exc


async def search_shapes(query: str, *, limit: int = 5) -> list[str]:
    """Return candidate draw.io style strings for a keyword query."""
    text = await _mcp_call_tool("search_shapes", {"query": query, "limit": limit})
    return _extract_styles(text)


async def _resolve_one(shape_type: str, platform: str) -> tuple[str, str | None]:
    cache_key = (platform, shape_type)
    if cache_key in _STYLE_CACHE:
        return shape_type, _STYLE_CACHE[cache_key]

    query = _shape_query(shape_type, platform)
    try:
        styles = await search_shapes(query, limit=5)
    except DrawioMcpError:
        raise
    pick = _pick_style(styles)
    if pick:
        if len(_STYLE_CACHE) >= _STYLE_CACHE_MAX:
            # ponytail: drop arbitrary oldest-ish entry; upgrade to OrderedDict LRU if needed
            _STYLE_CACHE.pop(next(iter(_STYLE_CACHE)))
        _STYLE_CACHE[cache_key] = pick
        logger.info("MCP resolved %s via %r", shape_type, query)
    else:
        logger.info("MCP no style for %s (%r)", shape_type, query)
    return shape_type, pick


async def resolve_shape_styles(
    shape_types: list[str],
    platform: str = "aws",
) -> dict[str, str]:
    """Map shape_type → best MCP style string (parallel lookups, process cache)."""
    platform = (platform or "aws").lower()
    unique = sorted({(st or "").strip() for st in shape_types if (st or "").strip()})
    if not unique:
        return {}

    # Cap concurrency so we don't open dozens of MCP sessions at once.
    sem = asyncio.Semaphore(6)

    async def _guarded(st: str) -> tuple[str, str | None]:
        async with sem:
            return await _resolve_one(st, platform)

    results = await asyncio.gather(*[_guarded(st) for st in unique], return_exceptions=True)
    resolved: dict[str, str] = {}
    first_error: DrawioMcpError | None = None
    for item in results:
        if isinstance(item, DrawioMcpError):
            first_error = first_error or item
            continue
        if isinstance(item, BaseException):
            first_error = first_error or DrawioMcpError(str(item))
            continue
        shape_type, style = item
        if style:
            resolved[shape_type] = style

    # If nothing resolved and MCP is hard-failing, surface the error for fallback.
    if not resolved and first_error is not None and not _STYLE_CACHE:
        raise first_error
    return resolved


def _extract_xml(payload: str) -> str | None:
    if not payload:
        return None
    if "<mxGraphModel" in payload or "<mxfile" in payload:
        fence = re.search(
            r"```(?:xml)?\s*(<(?:mxfile|mxGraphModel)[\s\S]*?</(?:mxfile|mxGraphModel)>)\s*```",
            payload,
        )
        if fence:
            return fence.group(1).strip()
        for tag in ("<mxfile", "<mxGraphModel"):
            start = payload.find(tag)
            if start < 0:
                continue
            end_tag = "</mxfile>" if tag == "<mxfile" else "</mxGraphModel>"
            end = payload.find(end_tag, start)
            if end > start:
                return payload[start:end + len(end_tag)].strip()
    try:
        data = json.loads(payload)
        if isinstance(data, dict):
            for key in ("xml", "diagram", "mxfile", "content"):
                val = data.get(key)
                if isinstance(val, str) and ("<mxGraphModel" in val or "<mxfile" in val):
                    return val
    except json.JSONDecodeError:
        pass
    return None


async def create_diagram(xml: str, *, routing: str = "libavoid") -> tuple[str | None, str | None]:
    """Call create_diagram; return (preview_url_or_text, updated_xml_or_none)."""
    try:
        from mcp import ClientSession  # noqa: F401 — availability check
        from mcp.client.streamable_http import streamablehttp_client  # noqa: F401
    except ImportError:
        return None, None

    args: dict[str, Any] = {"xml": xml}
    if routing:
        args["routing"] = routing
    try:
        text = await _mcp_call_tool("create_diagram", args)
        updated = _extract_xml(text or "")
        match = re.search(r"https?://[^\s\"']+", text or "")
        preview = match.group(0) if match else (text[:500] if text and not updated else None)
        return preview, updated
    except DrawioMcpError as exc:
        logger.warning("create_diagram skipped: %s", exc)
        return None, None
    except Exception as exc:
        logger.warning("create_diagram skipped: %s", exc)
        return None, None
