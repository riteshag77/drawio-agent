"""Simple XML diagram output for MCP-accelerated mode."""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SuggestionCategory = Literal["network", "logical", "security", "data_flow", "general"]
_SUGGESTION_CATEGORIES = frozenset(
    {"network", "logical", "security", "data_flow", "general"}
)


class DiagramSuggestion(BaseModel):
    """One follow-up improvement tagged with where it applies."""

    text: str = Field(description="Concrete, actionable architecture improvement")
    category: SuggestionCategory = Field(
        default="general",
        description=(
            "Where this suggestion applies: network | logical | security | data_flow | general"
        ),
    )


def normalize_suggestion_item(raw: Any) -> DiagramSuggestion | None:
    """Coerce str or dict-like suggestion into DiagramSuggestion."""
    if raw is None:
        return None
    if isinstance(raw, DiagramSuggestion):
        text = (raw.text or "").strip()
        if not text:
            return None
        cat = (raw.category or "general").strip().lower()
        if cat not in _SUGGESTION_CATEGORIES:
            cat = "general"
        return DiagramSuggestion(text=text, category=cat)  # type: ignore[arg-type]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        # Allow "network: Add NAT…" prefixes from older models
        lower = text.lower()
        for cat in ("network", "logical", "security", "data_flow", "general"):
            prefix = f"{cat}:"
            if lower.startswith(prefix):
                return DiagramSuggestion(text=text[len(prefix):].strip() or text, category=cat)  # type: ignore[arg-type]
        return DiagramSuggestion(text=text, category="general")
    if isinstance(raw, dict):
        text = str(raw.get("text") or raw.get("suggestion") or "").strip()
        if not text:
            return None
        cat = str(raw.get("category") or "general").strip().lower()
        if cat not in _SUGGESTION_CATEGORIES:
            cat = "general"
        return DiagramSuggestion(text=text, category=cat)  # type: ignore[arg-type]
    return None


def normalize_suggestions(raw: list[Any] | None) -> list[DiagramSuggestion]:
    out: list[DiagramSuggestion] = []
    for item in raw or []:
        norm = normalize_suggestion_item(item)
        if norm is not None:
            out.append(norm)
    return out


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
    suggestions: List[DiagramSuggestion] = Field(
        default_factory=list,
        description=(
            "2–3 concrete follow-up architecture improvements when suggestions are requested; "
            "each has text + category (network|logical|security|data_flow|general). "
            "Otherwise an empty list."
        ),
    )
    step_markers: List[StepMarker] = Field(
        default_factory=list,
        description=(
            "One entry per Overview Panel step that has a clear single on-diagram anchor. The backend uses "
            "cell_id to deterministically place a numbered badge on the diagram at that cell's position — "
            "you never compute coordinates yourself, just reference the id."
        ),
    )
    ir_title: str = Field(
        default="",
        description="Internal use only — always leave empty. Populated by the IR compiler path in Python, never by you.",
    )
    design_tradeoffs: List[str] = Field(
        default_factory=list,
        description=(
            "Internal use only — always leave empty. Populated from the IR's own "
            "design_tradeoffs field by Python on the IR compiler path, never by you."
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
