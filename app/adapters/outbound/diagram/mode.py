"""Diagram renderer mode detection — shared between routes, mcp_renderer, and pipeline."""

from __future__ import annotations

from app.adapters.outbound.config.env_config import get_config


def normalize_renderer(value: str | None = None) -> str:
    """Return the effective renderer mode: 'local', 'mcp', or 'auto'."""
    cfg_default = get_config().diagram_renderer
    raw = (value or cfg_default or "auto").strip().lower()
    return raw if raw in ("local", "mcp", "auto") else "auto"


def is_xml_pipeline_mode(value: str | None = None) -> bool:
    """Return True when the MCP/auto XML generation path should be used."""
    return normalize_renderer(value) in ("auto", "mcp")
