"""Mermaid diagram model for the simple-diagram fast path."""

from typing import Any, List

from pydantic import BaseModel, Field, field_validator

from app.domain.entities.xml_diagram import DiagramSuggestion, normalize_suggestion_item


class MermaidDiagram(BaseModel):
    mermaid: str = Field(description="Valid Mermaid.js diagram source code")
    assistant_message: str = ""
    suggestions: List[DiagramSuggestion] = Field(
        default_factory=list,
        description=(
            "2–3 concrete follow-up ideas (text + category) when suggestions are requested; "
            "otherwise an empty list"
        ),
    )

    @field_validator("suggestions", mode="before")
    @classmethod
    def _coerce_suggestions(cls, value: Any) -> list[Any]:
        if not value:
            return []
        if not isinstance(value, list):
            return []
        return [s for s in (normalize_suggestion_item(v) for v in value) if s is not None]
