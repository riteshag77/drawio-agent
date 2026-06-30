"""Container size scaffolds applied before deterministic layout."""

from app.models import ArchitectureDiagram, Node


def apply_three_tier_scaffold(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Give containers sensible base sizes; the layout engine grows them to fit children."""
    size_by_role: dict[str, tuple[int, int]] = {
        "cloud": (920, 540),
        "vpc": (980, 420),
        "az": (320, 380),
        "public": (300, 260),
        "private": (300, 260),
    }

    updated_nodes: list[Node] = []
    for node in architecture.nodes:
        if not node.is_container:
            updated_nodes.append(node)
            continue
        combined = f" {node.label} {node.shape_type} {node.id} ".lower()
        w = h = None
        if any(t in combined for t in ("cloud", "account", "subscription")):
            w, h = size_by_role["cloud"]
        elif any(t in combined for t in ("vpc", "vnet", "project")):
            w, h = size_by_role["vpc"]
        elif "availability zone" in combined or "_az" in combined or " az " in combined or " az_" in combined:
            w, h = size_by_role["az"]
        elif any(t in combined for t in ("public", "ingress", "dmz")):
            w, h = size_by_role["public"]
        elif any(t in combined for t in ("private", "data", "compute", "app")):
            w, h = size_by_role["private"]
        if w is not None:
            updated_nodes.append(node.model_copy(update={"width": w, "height": h, "x": None, "y": None}))
        else:
            updated_nodes.append(node)

    return architecture.model_copy(update={"nodes": updated_nodes})
