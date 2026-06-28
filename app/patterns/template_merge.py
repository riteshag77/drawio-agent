"""Container template scaffolds for known architecture patterns.

Reference skeletons live in ``templates/`` (e.g. three_tier_web.xml).
The layout engine applies scaffold sizes before auto-layout runs.
"""

from app.models import ArchitectureDiagram, Node


def apply_three_tier_scaffold(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Pin standard container sizes for the three-tier pattern before layout."""
    if architecture.pattern_id != "three_tier_web":
        return architecture

    size_by_role: dict[str, tuple[int, int]] = {
        "cloud": (920, 540),
        "vpc": (820, 400),
        "public": (380, 220),
        "private": (380, 220),
    }

    updated_nodes: list[Node] = []
    for node in architecture.nodes:
        if not node.is_container:
            updated_nodes.append(node)
            continue
        combined = f"{node.label} {node.shape_type} {node.id}".lower()
        w, h = None, None
        if "cloud" in combined or "account" in combined or "subscription" in combined:
            w, h = size_by_role["cloud"]
        elif "vpc" in combined or "vnet" in combined or "project" in combined:
            w, h = size_by_role["vpc"]
        elif "public" in combined or "ingress" in combined or "dmz" in combined:
            w, h = size_by_role["public"]
        elif "private" in combined or "data" in combined or "compute" in combined:
            w, h = size_by_role["private"]
        if w is not None and h is not None:
            updated_nodes.append(
                node.model_copy(update={"width": w, "height": h, "x": None, "y": None})
            )
        else:
            updated_nodes.append(node)

    return architecture.model_copy(update={"nodes": updated_nodes})
