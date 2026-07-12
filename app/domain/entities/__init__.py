"""Domain entities — core business objects."""
from app.domain.entities.mermaid import MermaidDiagram
from app.domain.entities.xml_diagram import XmlDiagram

__all__ = [
    "MermaidDiagram",
    "XmlDiagram",
]
