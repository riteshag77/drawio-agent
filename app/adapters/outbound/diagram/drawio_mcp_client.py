"""Draw.io hosted MCP client (search_shapes / create_diagram)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.xml_tools import coerce_drawio_xml

if TYPE_CHECKING:
    from app.core.logging import StepLogger

logger = logging.getLogger(__name__)
_log = logger  # unambiguous alias when StepLogger param is also named logger


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


# Process-level LRU style cache: (platform, shape_type) → style string
_STYLE_CACHE: OrderedDict[tuple[str, str], str] = OrderedDict()
_STYLE_CACHE_MAX = 512


def clear_style_cache() -> None:
    """Test helper."""
    _STYLE_CACHE.clear()


def _cache_get(key: tuple[str, str]) -> str | None:
    if key not in _STYLE_CACHE:
        return None
    _STYLE_CACHE.move_to_end(key)
    return _STYLE_CACHE[key]


def _cache_put(key: tuple[str, str], value: str) -> None:
    if key in _STYLE_CACHE:
        _STYLE_CACHE.move_to_end(key)
    _STYLE_CACHE[key] = value
    while len(_STYLE_CACHE) > _STYLE_CACHE_MAX:
        _STYLE_CACHE.popitem(last=False)


def _truncate(text: str, max_chars: int = 2000) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + f"... (truncated, {len(text)} total chars)"


async def _mcp_call_tool(tool_name: str, arguments: dict[str, Any]) -> str:
    """One MCP session, one tool call, with configured timeout."""
    results = await _mcp_call_tools([(tool_name, arguments)])
    return results[0]


async def _mcp_call_tools(calls: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """Run multiple tool calls on a single MCP session."""
    if not calls:
        return []

    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:
        raise DrawioMcpError("mcp package not installed; pip install mcp") from exc

    cfg = get_config()
    per_call = max(5, int(cfg.drawio_mcp_timeout))
    timeout = per_call * max(1, len(calls))

    async def _run() -> list[str]:
        async with streamablehttp_client(cfg.drawio_mcp_url) as streams:
            read_stream, write_stream = streams[0], streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                out: list[str] = []
                for tool_name, arguments in calls:
                    result = await session.call_tool(tool_name, arguments)
                    out.append(_content_to_text(result))
                return out

    try:
        return await asyncio.wait_for(_run(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        names = ", ".join(sorted({c[0] for c in calls}))
        raise DrawioMcpError(f"Draw.io MCP {names} timed out after {timeout}s") from exc
    except DrawioMcpError:
        raise
    except Exception as exc:
        names = ", ".join(sorted({c[0] for c in calls}))
        raise DrawioMcpError(f"Draw.io MCP {names} failed: {exc}") from exc


async def search_shapes(query: str, *, limit: int = 5) -> list[str]:
    """Return candidate draw.io style strings for a keyword query."""
    text = await _mcp_call_tool("search_shapes", {"query": query, "limit": limit})
    return _extract_styles(text)


async def resolve_shape_styles(
    shape_types: list[str],
    platform: str = "aws",
    logger: StepLogger | None = None,
) -> dict[str, str]:
    """Map shape_type → best MCP style string (one MCP session, process LRU cache)."""
    platform = (platform or "aws").lower()
    unique = sorted({(st or "").strip() for st in shape_types if (st or "").strip()})
    if not unique:
        return {}
    if logger:
        logger.log("mcp_search_input", "MCP search_shapes request",
                    details={"shape_types": unique, "platform": platform})

    resolved: dict[str, str] = {}
    misses: list[str] = []
    for st in unique:
        cached = _cache_get((platform, st))
        if cached is not None:
            resolved[st] = cached
        else:
            misses.append(st)

    first_error: DrawioMcpError | None = None
    if misses:
        calls = [
            ("search_shapes", {"query": _shape_query(st, platform), "limit": 5})
            for st in misses
        ]
        try:
            payloads = await _mcp_call_tools(calls)
            for st, payload in zip(misses, payloads, strict=True):
                pick = _pick_style(_extract_styles(payload))
                if pick:
                    _cache_put((platform, st), pick)
                    resolved[st] = pick
                    _log.info("MCP resolved %s via %r", st, _shape_query(st, platform))
                else:
                    _log.info("MCP no style for %s (%r)", st, _shape_query(st, platform))
        except DrawioMcpError as exc:
            first_error = exc

    if logger:
        logger.log("mcp_search_output", "MCP search_shapes response",
                    details={"resolved_count": len(resolved), "resolved": resolved})
    # If nothing resolved and MCP is hard-failing, surface the error for fallback.
    if not resolved and first_error is not None and not _STYLE_CACHE:
        raise first_error
    return resolved


def _extract_xml(payload: str) -> str | None:
    if not payload:
        return None
    coerced = coerce_drawio_xml(payload)
    if coerced and ("<mxfile" in coerced or "<mxGraphModel" in coerced):
        return coerced
    return None


async def create_diagram(xml: str, *, routing: str = "libavoid",
                         logger: StepLogger | None = None) -> tuple[str | None, str | None]:
    """Call create_diagram; return (preview_url_or_text, updated_xml_or_none)."""
    try:
        from mcp import ClientSession  # noqa: F401 — availability check
        from mcp.client.streamable_http import streamablehttp_client  # noqa: F401
    except ImportError:
        return None, None

    args: dict[str, Any] = {"xml": xml}
    if routing:
        args["routing"] = routing
    if logger:
        # Do not log full XML twice (args.xml + xml_preview) — that blob is often
        # copied into a .drawio file and fails to open in draw.io.
        logger.log(
            "mcp_create_input",
            "MCP create_diagram request",
            details={
                "routing": routing,
                "xml_chars": len(xml or ""),
                "xml_preview": _truncate(xml or "", 500),
            },
        )
    try:
        text = await _mcp_call_tool("create_diagram", args)
        updated = _extract_xml(text or "")
        match = re.search(r"https?://[^\s\"']+", text or "")
        preview = match.group(0) if match else (text[:500] if text and not updated else None)
        if logger:
            logger.log("mcp_create_output", "MCP create_diagram response",
                        details={"preview_url": preview, "updated_xml_chars": len(updated) if updated else 0})
        return preview, updated
    except DrawioMcpError as exc:
        if logger:
            logger.log("mcp_create_output", f"MCP create_diagram failed: {exc}", level="warning")
        return None, None
    except Exception as exc:
        if logger:
            logger.log("mcp_create_output", f"MCP create_diagram error: {exc}", level="warning")
        return None, None
