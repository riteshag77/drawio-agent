"""Mermaid diagram model for the simple-diagram fast path."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MermaidDiagram(BaseModel):
    mermaid: str = Field(description="Valid Mermaid.js diagram source code")
    assistant_message: str = ""
    suggestions: list[str] = Field(default_factory=list)
