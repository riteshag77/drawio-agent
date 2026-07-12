"""Simple XML diagram output for MCP-accelerated mode."""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class XmlDiagram(BaseModel):
    xml: str = Field(description="Complete draw.io XML (mxGraphModel or mxfile document) describing the architecture")
    assistant_message: str = Field(default="Generated diagram.", description="Brief summary for the chat UI")
    suggestions: List[str] = Field(default_factory=list, description="Optional improvement suggestions")
