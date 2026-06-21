"""Diagram edit models: ID-based operations and the structured edit response."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class DiagramOperation(BaseModel):
    operation: Literal["update", "add", "delete"] = Field(
        description="Operation to perform on a diagram cell"
    )
    cell_id: str = Field(description="The id of the mxCell (must match new_xml id for add/update)")
    new_xml: Optional[str] = Field(
        default=None,
        description="Complete mxCell XML element including mxGeometry (required for add/update)",
    )


class EditResponse(BaseModel):
    mode: Literal["edit", "replace"] = Field(
        default="edit",
        description="edit = incremental operations; replace = full mxCell fragment replacement",
    )
    operations: List[DiagramOperation] = Field(
        default_factory=list,
        description="ID-based diagram operations (when mode=edit)",
    )
    xml: Optional[str] = Field(
        default=None,
        description="Bare mxCell XML fragment for full replace (when mode=replace)",
    )
    assistant_message: str = Field(
        default="Diagram updated.",
        description="Brief summary for the chat UI",
    )
    suggestions: List[str] = Field(default_factory=list, description="Optional improvement suggestions")
