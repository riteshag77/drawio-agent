"""Domain entities — core business objects."""
from app.domain.entities.diagram_edit import DiagramEditOp, DiagramEditPlan
from app.domain.entities.mermaid import MermaidDiagram
from app.domain.entities.visual_qa import VisualQaIssue, VisualQaResult
from app.domain.entities.xml_diagram import DiagramSuggestion, StepMarker, XmlDiagram

__all__ = [
    "DiagramEditOp",
    "DiagramEditPlan",
    "DiagramSuggestion",
    "MermaidDiagram",
    "StepMarker",
    "VisualQaIssue",
    "VisualQaResult",
    "XmlDiagram",
]
