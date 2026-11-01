"""Thin MCP server wrapping the drawio-agent HTTP API for Cursor/Claude agents.

Run (with the FastAPI app already on localhost:8000):

    python -m app.mcp_server

Or configure in Cursor ``.cursor/mcp.json``:

    {
      "mcpServers": {
        "drawio-agent": {
          "command": "python",
          "args": ["-m", "app.mcp_server"],
          "env": { "DRAWIO_AGENT_URL": "http://127.0.0.1:8000" }
        }
      }
    }
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("DRAWIO_AGENT_URL", "http://127.0.0.1:8000").rstrip("/")
mcp = FastMCP("drawio-agent")


def _post(path: str, payload: dict[str, Any], timeout: float = 180.0) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        res = client.post(f"{BASE_URL}{path}", json=payload)
        res.raise_for_status()
        return res.json()


@mcp.tool()
def create_architecture_diagram(
    prompt: str,
    platform: str = "aws",
    provider: str = "",
    libavoid: bool = True,
) -> str:
    """Generate a cloud architecture draw.io diagram from a natural-language prompt.

    Returns JSON with xml, assistant_message, and llm_usage when available.
    """
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "platform": platform,
        "diagram_mode": "cloud",
        "libavoid": libavoid,
        "cost_estimate": False,
        "well_architected": False,
        "compliance": False,
    }
    if provider:
        body["provider"] = provider
    data = _post("/chat", body)
    return json.dumps({
        "status": data.get("status"),
        "assistant_message": data.get("assistant_message"),
        "xml": data.get("xml"),
        "renderer_used": data.get("renderer_used"),
        "llm_usage": data.get("llm_usage"),
    }, default=str)


@mcp.tool()
def edit_architecture_diagram(
    prompt: str,
    current_xml: str,
    platform: str = "aws",
    provider: str = "",
) -> str:
    """Edit an existing draw.io architecture diagram via natural language (surgical when possible)."""
    body: dict[str, Any] = {
        "messages": [{"role": "user", "content": prompt}],
        "platform": platform,
        "diagram_mode": "cloud",
        "current_xml": current_xml,
    }
    if provider:
        body["provider"] = provider
    data = _post("/chat", body)
    return json.dumps({
        "status": data.get("status"),
        "assistant_message": data.get("assistant_message"),
        "xml": data.get("xml"),
    }, default=str)


@mcp.tool()
def get_providers() -> str:
    """List configured LLM providers on the drawio-agent server."""
    with httpx.Client(timeout=30.0) as client:
        res = client.get(f"{BASE_URL}/providers")
        res.raise_for_status()
        return json.dumps(res.json(), default=str)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
