"""Container scaffolds and pattern blueprint merge."""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import scaffold_role
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.adapters.outbound.patterns.file_repository import FilePatternRepository
from app.domain.entities import ArchitectureDiagram, Edge, Node


def apply_three_tier_scaffold(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    s = layout_settings()
    sizes = {
        "cloud": s.scaffold_cloud,
        "vpc": s.scaffold_vpc,
        "az": s.scaffold_az,
        "public": s.scaffold_subnet,
        "private": s.scaffold_subnet,
    }
    updated: list[Node] = []
    for node in architecture.nodes:
        role = scaffold_role(node)
        if role and role in sizes:
            w, h = sizes[role]
            updated.append(node.model_copy(update={"width": w, "height": h, "x": None, "y": None}))
        else:
            updated.append(node)
    return architecture.model_copy(update={"nodes": updated})


def _node_from_blueprint(raw: dict) -> Node:
    return Node(
        id=raw["id"],
        label=raw.get("label") or raw["id"],
        shape_type=raw["shape_type"],
        is_container=bool(raw.get("is_container", False)),
        parent_id=raw.get("parent_id"),
        step_number=raw.get("step_number"),
    )


def _edge_from_blueprint(raw: dict) -> Edge:
    return Edge(
        source_id=raw["source_id"],
        target_id=raw["target_id"],
        label=raw.get("label") or "",
        style=raw.get("style") or "solid",
    )


def blueprint_diagram(pattern_id: str, platform: str = "aws") -> ArchitectureDiagram | None:
    pattern = FilePatternRepository().get_by_id(pattern_id)
    if not pattern:
        return None
    platform = (platform or "aws").lower()
    bp = pattern.blueprint.get(platform) or pattern.blueprint.get("aws")
    if not bp or not bp.get("nodes"):
        return None
    return ArchitectureDiagram(
        nodes=[_node_from_blueprint(n) for n in bp["nodes"]],
        edges=[_edge_from_blueprint(e) for e in bp.get("edges", [])],
        suggestions=[],
        flow_steps=[],
        pattern_id=pattern.id,
        title=pattern.name,
    )


def apply_pattern_blueprint(
    architecture: ArchitectureDiagram,
    pattern_id: str | None = None,
    platform: str = "aws",
) -> ArchitectureDiagram:
    """Fill missing blueprint containers/services; LLM nodes win on id collision."""
    pid = pattern_id or architecture.pattern_id
    if not pid:
        return architecture
    skeleton = blueprint_diagram(pid, platform)
    if skeleton is None:
        return architecture

    existing = {n.id for n in architecture.nodes}
    nodes = list(architecture.nodes) + [n for n in skeleton.nodes if n.id not in existing]
    existing = {n.id for n in nodes}

    edge_keys = {(e.source_id, e.target_id, (e.label or "").strip().lower()) for e in architecture.edges}
    edges = list(architecture.edges)
    for edge in skeleton.edges:
        if edge.source_id not in existing or edge.target_id not in existing:
            continue
        key = (edge.source_id, edge.target_id, (edge.label or "").strip().lower())
        if key not in edge_keys:
            edges.append(edge)
            edge_keys.add(key)

    return architecture.model_copy(update={
        "nodes": nodes,
        "edges": edges,
        "pattern_id": architecture.pattern_id or pid,
        "title": architecture.title or skeleton.title,
    })
