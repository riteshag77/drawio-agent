"""Surgical diagram edit operations — apply small changes without full regenerate."""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.domain.entities.xml_diagram import DiagramSuggestion, normalize_suggestion_item


class DiagramEditOp(BaseModel):
    op: Literal["add_node", "delete_node", "connect", "disconnect", "move", "relabel", "update_shape"] = Field(
        description="Operation type"
    )
    id: Optional[str] = Field(default=None, description="Target cell id (required for most ops)")
    label: Optional[str] = Field(default=None, description="New label for add/relabel")
    shape_type: Optional[str] = Field(default=None, description="shape_type for add_node / update_shape")
    parent: Optional[str] = Field(default=None, description="Parent id for add_node / move")
    source: Optional[str] = Field(default=None, description="Edge source for connect")
    target: Optional[str] = Field(default=None, description="Edge target for connect")
    x: Optional[float] = Field(default=None, description="Absolute x for move/add")
    y: Optional[float] = Field(default=None, description="Absolute y for move/add")
    edge_id: Optional[str] = Field(default=None, description="Edge id for connect/disconnect")
    edge_label: Optional[str] = Field(default=None, description="Optional edge label on connect")


class DiagramEditPlan(BaseModel):
    """LLM output for surgical edits when current_xml exists and the change is small."""

    applicable: bool = Field(
        description="True if these ops fully address the user request; false to fall back to full regenerate"
    )
    ops: List[DiagramEditOp] = Field(default_factory=list)
    assistant_message: str = Field(default="Updated diagram.", description="Brief chat summary")
    suggestions: List[DiagramSuggestion] = Field(
        default_factory=list,
        description=(
            "2–3 concrete follow-up improvements (text + category) when suggestions are requested; "
            "otherwise an empty list"
        ),
    )

    @field_validator("suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, value):
        if not value:
            return []
        if not isinstance(value, list):
            return []
        return [s for s in (normalize_suggestion_item(v) for v in value) if s is not None]
