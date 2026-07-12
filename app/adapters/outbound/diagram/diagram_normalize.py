"""Minimal architecture normalization: dedup edges, strip obvious labels."""

from __future__ import annotations

from app.domain.entities import ArchitectureDiagram, Edge

_OBVIOUS_EDGE_LABELS = frozenset({
    "write", "read", "query", "call", "invoke", "request", "data", "flow",
    "to", "uses", "use", "access", "accesses", "connect", "connects",
})


def _strip_obvious_edge_labels(edges: list[Edge]) -> list[Edge]:
    out: list[Edge] = []
    for edge in edges:
        label = (edge.label or "").strip()
        if not label:
            out.append(edge)
            continue
        if edge.style == "dashed":
            out.append(edge)
            continue
        if label.lower() in _OBVIOUS_EDGE_LABELS:
            out.append(edge.model_copy(update={"label": None}))
        else:
            out.append(edge)
    return out


def _edge_key(edge: Edge) -> tuple[str, str, str]:
    return (edge.source_id, edge.target_id, (edge.label or "").strip().lower())


def _dedupe_edges(edges: list[Edge]) -> list[Edge]:
    seen: set[tuple[str, str, str]] = set()
    out: list[Edge] = []
    for edge in edges:
        key = _edge_key(edge)
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return out


def normalize_architecture(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    edges = _dedupe_edges(_strip_obvious_edge_labels(list(architecture.edges)))
    return architecture.model_copy(update={"edges": edges})
