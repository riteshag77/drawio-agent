"""Simple XML diagram output for MCP-accelerated mode."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class StepMarker(BaseModel):
    step_number: int = Field(
        description="Must match the step's number in the Overview Panel's own numbered step list — same numbering, same order."
    )
    cell_id: Optional[str] = Field(
        default=None,
        description=(
            "The exact `id` you already assigned to the single mxCell (leaf icon or container) this step "
            "is about — never invent a new id, never a shape_type or service name. Omit (leave null) when "
            "the step describes something general that isn't one specific cell (e.g. 'requests flow through "
            "the whole VPC') — never guess an approximate anchor."
        ),
    )


class XmlDiagram(BaseModel):
    xml: str = Field(description="Complete draw.io XML (mxGraphModel or mxfile document) describing the architecture")
    assistant_message: str = Field(default="Generated diagram.", description="Brief summary for the chat UI")
    suggestions: List[str] = Field(default_factory=list, description="Optional improvement suggestions")
    step_markers: List[StepMarker] = Field(
        default_factory=list,
        description=(
            "One entry per Overview Panel step that has a clear single on-diagram anchor. The backend uses "
            "cell_id to deterministically place a numbered badge on the diagram at that cell's position — "
            "you never compute coordinates yourself, just reference the id."
        ),
    )
