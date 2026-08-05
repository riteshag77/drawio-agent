"""Draw.io hosted MCP client (search_shapes / create_diagram)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.style_cache import cache_get, cache_put, clear_style_cache, memory_empty
from app.adapters.outbound.diagram.xml_tools import _SERVICE_KEY_ALIASES, coerce_drawio_xml

#: name -> canonical short code, derived from the SAME alias table
#: find_service_keys() uses post-hoc (e.g. "application_load_balancer" ->
#: "alb"). Reused here so pick-time relevance scoring recognizes an acronym
#: shape_type ("aws_alb") as relevant to a full-name candidate
#: ("application_load_balancer") — without this, a query for "alb" scores
#: EVERY candidate 0 (no shared token with the acronym), so if search_shapes
#: returns both a correct "application_load_balancer" result and an
#: unrelated one, this app couldn't tell them apart and just kept trusting
#: search_shapes' own ranking for exactly the acronym-style services the
#: alias table exists for.
_STENCIL_NAME_ALIASES = {
    long.split("_", 1)[1]: short.split("_", 1)[1]
    for long, short in _SERVICE_KEY_ALIASES.items()
    if "_" in long and "_" in short
}

if TYPE_CHECKING:
    from app.core.logging import StepLogger

logger = logging.getLogger(__name__)
_log = logger  # unambiguous alias when StepLogger param is also named logger

# Re-export for tests that imported clear_style_cache from this module.
__all__ = [
    "DrawioMcpError",
    "clear_style_cache",
    "create_diagram",
    "resolve_shape_styles",
]


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


def _platform_from_shape_type(shape_type: str, fallback: str = "aws") -> str:
    """Infer vendor/library family from shape_type prefix."""
    st = (shape_type or "").lower()
    if st.startswith("logical_"):
        return "logical"
    if st.startswith("aws_"):
        return "aws"
    if st.startswith("gcp_"):
        return "gcp"
    if st.startswith("azure_"):
        return "azure"
    fb = (fallback or "aws").lower()
    return fb if fb in ("aws", "gcp", "azure", "logical") else "aws"


_LOGICAL_SHAPE_QUERIES: dict[str, str] = {
    "logical_actor": "uml actor",
    "logical_channel": "rounded rectangle",
    "logical_capability": "rounded rectangle application",
    "logical_api": "application",
    "logical_datastore": "cylinder database",
    "logical_saas": "cloud",
    "logical_platform": "rounded rectangle",
}

# Curated draw.io styles for Logical roles — always LEAF_W×LEAF_H.
# People/channel use AWS4 icons with the label in the band *below* the glyph.
# Capability / API / store / SaaS / platform are coloured tiles with the label
# *inside* the box — bottom-band labels left those tiles looking empty.
_LOGICAL_BUILTIN_STYLES: dict[str, str] = {
    "logical_actor": (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;"
        "fillColor=#232F3D;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
        "verticalAlign=top;align=center;html=1;fontSize=11;fontStyle=0;aspect=fixed;"
        "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.users;"
    ),
    "logical_channel": (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;"
        "fillColor=#232F3D;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
        "verticalAlign=top;align=center;html=1;fontSize=11;fontStyle=0;aspect=fixed;"
        "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.client;"
    ),
    "logical_capability": (
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#DAE8FC;strokeColor=#6C8EBF;"
        "fontColor=#1A1A2E;fontSize=11;arcSize=8;align=center;verticalAlign=middle;"
        "labelPosition=center;verticalLabelPosition=middle;"
    ),
    "logical_api": (
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#E1D5E7;strokeColor=#9673A6;"
        "fontColor=#1A1A2E;fontSize=11;arcSize=20;align=center;verticalAlign=middle;"
        "labelPosition=center;verticalLabelPosition=middle;"
    ),
    "logical_datastore": (
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#D5E8D4;strokeColor=#82B366;"
        "fontColor=#1A1A2E;fontSize=11;arcSize=8;align=center;verticalAlign=middle;"
        "labelPosition=center;verticalLabelPosition=middle;dashed=0;"
    ),
    "logical_saas": (
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF2CC;strokeColor=#D6B656;"
        "fontColor=#1A1A2E;fontSize=11;arcSize=50;align=center;verticalAlign=middle;"
        "labelPosition=center;verticalLabelPosition=middle;"
    ),
    "logical_platform": (
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#DAE8FC;strokeColor=#147EBA;"
        "fontColor=#1A1A2E;fontSize=11;arcSize=8;dashed=1;align=center;verticalAlign=middle;"
        "labelPosition=center;verticalLabelPosition=middle;"
    ),
}


def _shape_query(shape_type: str, platform: str) -> str:
    st = (shape_type or "").lower()
    if st in _LOGICAL_SHAPE_QUERIES:
        return _LOGICAL_SHAPE_QUERIES[st]
    if st.startswith("logical_"):
        return st.replace("logical_", "").replace("_", " ")
    # External actors: force queries that hit AWS4 resource icons, not VPN/IAM noise.
    if st in ("aws_users", "aws_user", "users", "user"):
        return "aws4 users"
    if st in ("aws_client", "client", "aws_mobile_client"):
        return "aws4 client"
    if st in ("aws_internet", "internet"):
        return "aws4 internet"
    resolved = _platform_from_shape_type(st, platform)
    for prefix in ("aws_", "gcp_", "azure_"):
        if st.startswith(prefix):
            name = st[len(prefix):].replace("_", " ")
            break
    else:
        name = st.replace("_", " ")
    if resolved == "gcp":
        return f"gcp {name}"
    if resolved == "azure":
        return f"azure {name}"
    return name


# Known-good draw.io styles for external actors — MCP keyword search often returns
# Client VPN / Cognito / WorkSpaces instead of the generic Users/Client icons.
_BUILTIN_SHAPE_STYLES: dict[str, str] = {
    "aws_users": (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;"
        "fillColor=#232F3D;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
        "verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
        "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.users;"
    ),
    "aws_user": (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;"
        "fillColor=#232F3D;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
        "verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
        "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.users;"
    ),
    "aws_client": (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;"
        "fillColor=#232F3D;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
        "verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
        "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.client;"
    ),
    "aws_mobile_client": (
        "sketch=0;outlineConnect=0;fontColor=#232F3E;gradientColor=none;"
        "fillColor=#232F3D;strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
        "verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;aspect=fixed;"
        "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.mobile_client;"
    ),
    # Business-partner org leaves (Security's zone_partner) previously had no
    # curated shape_type at all — the prompt just said "plain generic
    # partner-org leaves" with no vocabulary to match, so each one fell
    # through to MCP's keyword search with an arbitrary LLM-invented
    # shape_type and either got a lucky hit or stayed an undifferentiated
    # grey placeholder. Double-border rounded rect (thicker stroke) is a
    # standard, no-stencil-risk convention for "external organisation" —
    # matches security.txt's own partner/B2B section color.
    "security_partner_org": (
        "rounded=1;whiteSpace=wrap;html=1;fillColor=#D5E8D4;strokeColor=#82B366;"
        "strokeWidth=3;fontColor=#1A1A2E;fontSize=12;arcSize=8;align=center;verticalAlign=middle;"
    ),
}

# Curated SaaS stencils: SVG files in static/saas-icons/ embedded as data-URI image
# styles (self-contained diagrams; no MCP brand search). Fallback = colored tile.
_SAAS_LEAF = (
    "whiteSpace=wrap;html=1;fontColor=#1A1A2E;fontSize=11;align=center;"
    "verticalLabelPosition=bottom;verticalAlign=top;aspect=fixed;"
)
_SAAS_FALLBACK_COLORS: dict[str, str] = {
    "saas_salesforce": "#00A1E0",
    "saas_workday": "#F68D2E",
    "saas_successfactors": "#008FD3",
    "saas_servicenow": "#81B5A1",
    "saas_okta": "#007DC1",
    "saas_guidewire": "#E87722",
    "saas_duck_creek": "#6B2D7B",
    "saas_temenos": "#0033A0",
    "saas_finastra": "#FF5A00",
    "saas_fiserv": "#FF6600",
    "saas_fis": "#00A3E0",
    "saas_murex": "#1B3A5F",
    "saas_bloomberg": "#2800D1",
    "saas_refinitiv": "#FF8000",
    "saas_experian": "#3B6E8F",
    "saas_equifax": "#C8102E",
    "saas_stripe": "#635BFF",
    "saas_adyen": "#0ABF53",
    "saas_plaid": "#111111",
    "saas_slack": "#4A154B",
    "saas_office365": "#D83B01",
    "saas_microsoft_365": "#D83B01",
}


def _saas_icons_dir():
    from app.adapters.outbound.config.env_config import EnvConfigAdapter
    return EnvConfigAdapter.static_dir() / "saas-icons"


def _saas_image_style_from_svg(svg_text: str) -> str:
    import urllib.parse

    # Collapse whitespace so draw.io style attrs stay compact.
    compact = re.sub(r"\s+", " ", (svg_text or "").strip())
    encoded = urllib.parse.quote(compact, safe="")
    # imageAspect=1 keeps the logo's own ratio — imageAspect=0 stretches it to the
    # cell box, which looks wrong whenever a leaf is not square.
    return (
        f"shape=image;html=1;verticalLabelPosition=bottom;verticalAlign=top;"
        f"imageAspect=1;aspect=fixed;align=center;fontSize=11;fontColor=#1A1A2E;"
        f"image=data:image/svg+xml,{encoded};"
    )


def _saas_fallback_style(shape_type: str) -> str:
    fill = _SAAS_FALLBACK_COLORS.get(shape_type, "#FFF2CC")
    return (
        f"rounded=1;{_SAAS_LEAF}fillColor={fill};strokeColor=#666666;arcSize=12;"
    )


def _load_saas_builtin_styles() -> dict[str, str]:
    """Map saas_* → draw.io style using on-disk SVG stencils when present."""
    icons = _saas_icons_dir()
    out: dict[str, str] = {}
    for shape_type in _SAAS_FALLBACK_COLORS:
        stem = shape_type.removeprefix("saas_")
        path = icons / f"{stem}.svg"
        if path.is_file():
            try:
                out[shape_type] = _saas_image_style_from_svg(path.read_text(encoding="utf-8"))
                continue
            except OSError:
                pass
        out[shape_type] = _saas_fallback_style(shape_type)
    return out


_SAAS_BUILTIN_STYLES_CACHE: dict[str, str] | None = None


def _saas_builtin_styles() -> dict[str, str]:
    """Lazy-load SaaS SVG styles so icons added after process start are picked up."""
    global _SAAS_BUILTIN_STYLES_CACHE
    if _SAAS_BUILTIN_STYLES_CACHE is None:
        _SAAS_BUILTIN_STYLES_CACHE = _load_saas_builtin_styles()
    return _SAAS_BUILTIN_STYLES_CACHE


def __getattr__(name: str):
    # Tests / importers historically imported ``_SAAS_BUILTIN_STYLES`` as a dict.
    if name == "_SAAS_BUILTIN_STYLES":
        return _saas_builtin_styles()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _builtin_style(shape_type: str) -> str | None:
    """Early-resolve known-good styles — skip MCP when we already own the stencil.

    Cloud actors (Users/Client), all ``logical_*`` roles, and curated ``saas_*``
    stencil icons use builtins. Logical/SaaS must not go through MCP keyword search:
    vague queries often return empty or non-rendering shapes.
    """
    st = (shape_type or "").strip().lower()
    if st.startswith("logical_"):
        style = _LOGICAL_BUILTIN_STYLES.get(st)
        if style and not style.endswith(";"):
            return style + ";"
        return style
    if st.startswith("saas_"):
        styles = _saas_builtin_styles()
        style = styles.get(st)
        if style is None and st in _SAAS_FALLBACK_COLORS:
            style = _saas_fallback_style(st)
        if style and not style.endswith(";"):
            return style + ";"
        return style
    style = _BUILTIN_SHAPE_STYLES.get(st)
    if style and not style.endswith(";"):
        return style + ";"
    return style


def _logical_fallback_style(shape_type: str) -> str | None:
    st = (shape_type or "").strip().lower()
    style = _LOGICAL_BUILTIN_STYLES.get(st)
    if style and not style.endswith(";"):
        return style + ";"
    return style


def _is_cloud_vendor_style(style: str) -> bool:
    low = (style or "").lower()
    return (
        "mxgraph.aws4" in low
        or "resourceicon" in low
        or "mxgraph.gcp2" in low
        or "img/lib/azure2" in low
        or "img/lib/azure" in low
        or "mxgraph.azure" in low
    )


def _platform_style_predicate(platform: str):
    """Return a matcher that prefers styles from the configured draw.io lib."""
    cfg = get_config()
    platform = (platform or "aws").lower()
    if platform == "logical":
        def _logical_ok(low: str) -> bool:
            # People / portal icons: AWS4 users/client render reliably in the iframe.
            if "resicon=mxgraph.aws4.users" in low or "resicon=mxgraph.aws4.client" in low:
                return True
            if "resicon=mxgraph.aws4.mobile_client" in low:
                return True
            if _is_cloud_vendor_style(low):
                return False
            return any(
                token in low
                for token in (
                    "umlactor", "cylinder", "rounded=1", "ellipse",
                    "mxgraph.basic", "flowchart", "shape=mxgraph", "hexagon",
                )
            ) or ("shape=" in low and "resicon=" not in low)
        return _logical_ok
    if platform == "gcp":
        needle = (cfg.drawio_lib_gcp or "mxgraph.gcp2").lower()
        return lambda low: needle in low or "mxgraph.gcp2" in low or "img/lib/google" in low
    if platform == "azure":
        needle = (cfg.drawio_lib_azure or "img/lib/azure2").lower()
        return lambda low: needle in low or "img/lib/azure2" in low
    needle = (cfg.drawio_lib_aws or "mxgraph.aws4").lower()
    return lambda low: needle in low or "resourceicon" in low or "mxgraph.aws4" in low


_RESICON_PATTERN = re.compile(r'(?:resicon|pricon)=mxgraph\.\w+\.([a-z0-9_]+)')
_SHAPE_PATTERN = re.compile(r'shape=mxgraph\.\w+\.([a-z0-9_]+)')
_STENCIL_WRAPPER_NAMES = frozenset({"resourceicon", "producticon"})


def _stencil_name(style: str) -> str:
    """The service/icon name a style string actually renders.

    Tries resIcon=/prIcon= first — the actual target icon on the common
    ``shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.<name>`` form —
    before falling back to a standalone ``shape=mxgraph.aws4.<name>`` (e.g.
    ALB, which has no separate resIcon). Must not just take the leftmost
    ``mxgraph.*.*`` match in the string: a plain regex .search() on both
    patterns at once matches "shape=mxgraph.aws4.resourceIcon" (the WRAPPER,
    always first in the string) before ever reaching the real resIcon target
    later in the same style — silently extracting the wrong name.
    """
    low = (style or "").lower()
    m = _RESICON_PATTERN.search(low)
    if m:
        return m.group(1)
    m = _SHAPE_PATTERN.search(low)
    if m and m.group(1) not in _STENCIL_WRAPPER_NAMES:
        return m.group(1)
    return ""


def _relevance_score(shape_type: str, style: str) -> int:
    """Whole-token overlap between the requested shape_type's core name and
    what a candidate style actually renders — deliberately NOT a raw
    substring check: "iam" is a substring of "diagram", "s3" is a substring
    of any word containing that letter+digit pair, etc., so matching on
    substrings risks a false match to some unrelated service that merely
    happens to share a fragment. Splitting both names on "_" and requiring a
    WHOLE token to match avoids that class of false positive; the tradeoff is
    it won't bridge an acronym to its expansion (e.g. "alb" vs
    "application_load_balancer" share no token) — that specific, known class
    of mismatch is handled separately and exactly by
    ``xml_tools._SERVICE_KEY_ALIASES``, not by this heuristic.

    search_shapes ranks its own results, and this app previously trusted
    that ranking blindly (``styles[0]``) — confirmed via live captures that
    this occasionally top-ranks to something with no real relation to the
    query at all (a leaf labeled "AWS KMS" resolving to the Kinesis Data
    Firehose icon, which shares zero tokens with "kms"). This doesn't
    replace search_shapes' ranking — it only breaks ties among same-platform
    candidates in favor of the one that actually looks like what was asked
    for, when more than one option exists.
    """
    core = (shape_type or "").lower()
    for prefix in ("aws_", "gcp_", "azure_"):
        if core.startswith(prefix):
            core = core[len(prefix):]
            break
    name = _stencil_name(style)
    if not core or not name:
        return 0
    if core == name:
        return 100
    # An acronym query (e.g. "alb") vs a full-name candidate
    # ("application_load_balancer") shares no token at all — check the same
    # alias table find_service_keys() uses post-hoc, so a known acronym/
    # full-name pair scores as a real match here too, not just after the
    # fact.
    if _STENCIL_NAME_ALIASES.get(name) == core:
        return 100
    core_tokens = {t for t in core.split("_") if t}
    name_tokens = {t for t in name.split("_") if t}
    if not core_tokens or not name_tokens:
        return 0
    if core_tokens.issubset(name_tokens) or name_tokens.issubset(core_tokens):
        return 80
    overlap = core_tokens & name_tokens
    return 20 * len(overlap)


def _pick_style(styles: list[str], platform: str = "aws", shape_type: str = "") -> str | None:
    if not styles:
        return None
    st = (shape_type or "").lower()
    # Prefer exact resourceIcon match for external actors when MCP returns a list.
    prefer_token = ""
    if st in ("aws_users", "aws_user", "users", "user", "logical_actor"):
        prefer_token = "resicon=mxgraph.aws4.users"
    elif st in ("aws_client", "client", "logical_channel"):
        prefer_token = "resicon=mxgraph.aws4.client"
    if prefer_token:
        for style in styles:
            if prefer_token in style.lower():
                pick = style if style.endswith(";") else style + ";"
                return pick
    prefer = _platform_style_predicate(platform)
    # Logical: allow AWS4 users/client for people/portals; reject other cloud icons
    if platform == "logical" or st.startswith("logical_"):
        allow_people = st in {
            "logical_actor", "logical_channel", "aws_users", "aws_user", "aws_client",
        }

        def _keep(s: str) -> bool:
            low = s.lower()
            if allow_people and (
                "resicon=mxgraph.aws4.users" in low
                or "resicon=mxgraph.aws4.client" in low
                or "resicon=mxgraph.aws4.mobile_client" in low
            ):
                return True
            return not _is_cloud_vendor_style(s)

        candidates = [s for s in styles if _keep(s)]
        pick = None
        for style in candidates:
            if prefer(style.lower()):
                pick = style
                break
        if pick is None and candidates:
            pick = candidates[0]
        if pick is None:
            return None
        if not pick.endswith(";"):
            pick += ";"
        return pick
    same_platform = [s for s in styles if prefer(s.lower())] or styles
    # Break ties in favor of whichever same-platform candidate actually looks
    # like the requested service, instead of blindly trusting search_shapes'
    # own top result — see _relevance_score. Python's max() keeps the first
    # occurrence on a tie, so this is a no-op (identical to the old
    # first-match behavior) whenever nothing scores above 0 or shape_type
    # wasn't given, and never picks anything outside the same-platform set.
    pick = max(same_platform, key=lambda s: _relevance_score(shape_type, s))
    if not pick.endswith(";"):
        pick += ";"
    return pick


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


def _chunk(items: list[str], max_chunks: int) -> list[list[str]]:
    """Split ``items`` into at most ``max_chunks`` roughly-equal, order-preserving groups."""
    if max_chunks <= 1 or len(items) <= 1:
        return [items]
    n_chunks = min(max_chunks, len(items))
    size = -(-len(items) // n_chunks)  # ceil division
    return [items[i:i + size] for i in range(0, len(items), size)]


async def resolve_shape_styles(
    shape_types: list[str],
    platform: str = "aws",
    logger: StepLogger | None = None,
) -> dict[str, str]:
    """Map shape_type → best MCP style string (cached + batched MCP lookup).

    Cache hits use memory LRU backed by optional SQLite. Misses are split into
    at most ``DRAWIO_MCP_CONCURRENCY`` chunks, each resolved over a *single*
    MCP session handling all of that chunk's ``search_shapes`` calls — so N
    misses cost at most ``DRAWIO_MCP_CONCURRENCY`` connection handshakes
    instead of N, while chunks still run concurrently and a failure in one
    chunk doesn't take down the others' results.
    For ``platform=multi``, each shape_type's cloud prefix selects vendor/style.
    """
    platform = (platform or "aws").lower()
    unique = sorted({(st or "").strip() for st in shape_types if (st or "").strip()})
    if not unique:
        return {}
    if logger:
        logger.log("mcp_search_input", "MCP search_shapes request",
                    details={"shape_types": unique, "platform": platform})

    resolved: dict[str, str] = {}
    misses: list[str] = []
    unresolved: list[str] = []
    for st in unique:
        cloud = _platform_from_shape_type(st, platform)
        builtin = _builtin_style(st)
        if builtin:
            resolved[st] = builtin
            cache_put((cloud, st), builtin)
            continue
        cached = cache_get((cloud, st))
        if cached is not None and cached.strip():
            resolved[st] = cached
        else:
            misses.append(st)

    first_error: DrawioMcpError | None = None
    if misses:
        concurrency = max(1, int(get_config().drawio_mcp_concurrency))
        chunks = _chunk(misses, concurrency)

        async def _resolve_chunk(chunk: list[str]) -> tuple[list[tuple[str, str | None]], DrawioMcpError | None]:
            calls = [("search_shapes", {"query": _shape_query(st, platform), "limit": 5}) for st in chunk]
            try:
                payloads = await _mcp_call_tools(calls)
            except DrawioMcpError as exc:
                return [(st, None) for st in chunk], exc
            out: list[tuple[str, str | None]] = []
            for st, payload in zip(chunk, payloads):
                cloud = _platform_from_shape_type(st, platform)
                pick = _pick_style(_extract_styles(payload), cloud, shape_type=st)
                out.append((st, pick if pick and pick.strip() else None))
            return out, None

        chunk_results = await asyncio.gather(*[_resolve_chunk(c) for c in chunks])
        for results, err in chunk_results:
            if err is not None and first_error is None:
                first_error = err
            for st, pick in results:
                if pick:
                    cloud = _platform_from_shape_type(st, platform)
                    cache_put((cloud, st), pick)
                    resolved[st] = pick
                    _log.info("MCP resolved %s", st)
                else:
                    # Logical vocabulary: apply non-cloud builtins on MCP miss so
                    # placeholders never stay grey when we have a known stencil.
                    fallback = _logical_fallback_style(st)
                    if fallback:
                        cloud = _platform_from_shape_type(st, platform)
                        cache_put((cloud, st), fallback)
                        resolved[st] = fallback
                        _log.info("MCP miss for %s — logical builtin fallback", st)
                    else:
                        unresolved.append(st)
                        _log.info("MCP miss for %s", st)

    if logger:
        logger.log(
            "mcp_search_output",
            "MCP search_shapes response",
            details={"resolved_count": len(resolved), "resolved": resolved},
        )
        if unresolved:
            logger.log(
                "mcp_miss",
                f"MCP found no style for {len(unresolved)} shape_type(s) — placeholders kept",
                level="warning",
                details={"misses": unresolved},
            )
    # If nothing resolved and MCP is hard-failing, surface the error for fallback.
    if not resolved and first_error is not None and memory_empty():
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
                        details={
                            "preview_url": preview,
                            "updated_xml_chars": len(updated) if updated else 0,
                            "updated_xml": updated if updated else None,
                            "raw_response": _truncate(text or "", 200000),
                        })
        return preview, updated
    except DrawioMcpError as exc:
        if logger:
            logger.log("mcp_create_output", f"MCP create_diagram failed: {exc}", level="warning",
                        details={"error": str(exc)})
        return None, None
    except Exception as exc:
        if logger:
            logger.log("mcp_create_output", f"MCP create_diagram error: {exc}", level="warning",
                        details={"error": str(exc)})
        return None, None
