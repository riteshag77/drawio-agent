"""Validation domain models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


IssueType = Literal["overlap", "edge_routing", "text", "layout", "rendering"]
IssueSeverity = Literal["critical", "warning"]


class ValidationIssue(BaseModel):
    type: IssueType = Field(description="Type of visual or structural issue")
    severity: IssueSeverity = Field(description="Severity level")
    description: str = Field(description="Clear description of the issue")


class ValidationResult(BaseModel):
    valid: bool = Field(description="True if there are no critical issues")
    issues: list[ValidationIssue] = Field(default_factory=list, description="List of issues found")
    suggestions: list[str] = Field(default_factory=list, description="Actionable suggestions to fix issues")
    skipped: bool = Field(default=False, description="True when validation was not run (e.g. VLM unavailable)")


DEFAULT_VALID_RESULT = ValidationResult(valid=True, issues=[], suggestions=[], skipped=False)
DEFAULT_SKIPPED_RESULT = ValidationResult(
    valid=True,
    issues=[],
    suggestions=["Visual validation was skipped (unavailable or disabled)."],
    skipped=True,
)
