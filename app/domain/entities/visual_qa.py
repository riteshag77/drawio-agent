"""Structured output for the real screenshot-based visual QA pass (VLM validation)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VisualQaIssue(BaseModel):
    category: Literal["overlap", "edge_routing", "text_readability", "layout", "other"]
    severity: Literal["critical", "warning"]
    description: str = Field(max_length=500)
    element_hint: str = Field(default="", max_length=200)


class VisualQaResult(BaseModel):
    passed: bool
    issues: list[VisualQaIssue] = Field(default_factory=list)
    summary: str = Field(default="", max_length=800)
