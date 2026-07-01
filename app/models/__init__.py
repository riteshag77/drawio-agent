"""Domain models — backward-compatible re-export from domain/entities."""

from app.domain.entities import (  # noqa: F401
    ArchitectureDiagram,
    DEFAULT_VALID_RESULT,
    DiagramOperation,
    Edge,
    EditResponse,
    FlowStep,
    IssueSeverity,
    IssueType,
    MermaidDiagram,
    Node,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    "ArchitectureDiagram",
    "Edge",
    "FlowStep",
    "Node",
    "DiagramOperation",
    "EditResponse",
    "DEFAULT_VALID_RESULT",
    "IssueSeverity",
    "IssueType",
    "ValidationIssue",
    "ValidationResult",
    "MermaidDiagram",
]
