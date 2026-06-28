"""Topology helpers: parent/child maps, tier ordering, layout placement."""

from __future__ import annotations

LEAF_W, LEAF_H = 60, 60
LEAF_CELL_W, LEAF_GAP = 148, 24
CONTAINER_PAD, CONTAINER_PAD_TOP, CONTAINER_GAP = 24, 44, 40
ROOT_TOP, MAX_COLS = 40, 4
DEFAULT_CONTAINER_W, DEFAULT_CONTAINER_H = 320, 240

# ponytail: x/y are sibling-index placeholders; Draw.io horizontalFlow reflows on embed load.


def build_children_map(nodes) -> tuple[dict[str, list[str]], list[str], dict]:
    """Return ``(children_map, roots, nodes_by_id)`` from architecture nodes."""
    nodes_by_id = {node.id: node for node in nodes}
    children_map: dict[str, list[str]] = {}
    roots: list[str] = []

    for node in nodes:
        if node.parent_id and node.parent_id in nodes_by_id:
            children_map.setdefault(node.parent_id, []).append(node.id)
        else:
            roots.append(node.id)
    return children_map, roots, nodes_by_id


def ancestor_ids(node_id: str, nodes_by_id: dict) -> set[str]:
    seen: set[str] = set()
    current = nodes_by_id.get(node_id)
    while current and current.parent_id and current.parent_id in nodes_by_id:
        seen.add(current.parent_id)
        current = nodes_by_id[current.parent_id]
    return seen


def lowest_common_ancestor_parent(source_id: str, target_id: str, nodes_by_id: dict) -> str:
    target_ancestors = ancestor_ids(target_id, nodes_by_id)
    for ancestor in ancestor_ids(source_id, nodes_by_id):
        if ancestor in target_ancestors:
            return ancestor
    return "1"


def node_tier(node) -> int:
    text = f"{node.label} {node.shape_type}".lower()
    if node.is_container:
        if any(word in text for word in ("public", "ingress", "frontend", "dmz", "edge")):
            return 0
        if any(word in text for word in ("data", "database", "db", "storage", "persistence")):
            return 2
        if any(word in text for word in ("private", "compute", "app", "backend", "worker")):
            return 1
        return 1
    if "public" in text:
        return 0
    if "private" in text:
        return 1
    if any(word in text for word in ("monitoring", "logging", "security", "ops", "compliance")):
        return 3
    return 2


def sort_key(node_id: str, nodes_by_id: dict) -> tuple:
    node = nodes_by_id[node_id]
    step = node.step_number if node.step_number is not None else 500
    return (node_tier(node), step, node_id)


def node_size(node) -> tuple[int, int]:
    if node.is_container:
        return node.width or DEFAULT_CONTAINER_W, node.height or DEFAULT_CONTAINER_H
    return node.width or LEAF_W, node.height or LEAF_H


def fit_container_bounds(
    container_id: str,
    layout: dict[str, dict],
    children_map: dict[str, list[str]],
    nodes_by_id: dict,
) -> None:
    children = children_map.get(container_id, [])
    if not children or container_id not in layout:
        return

    node = nodes_by_id[container_id]
    max_right = CONTAINER_PAD
    max_bottom = CONTAINER_PAD_TOP
    for child_id in children:
        child_layout = layout.get(child_id, {})
        max_right = max(max_right, child_layout.get("x", 0) + child_layout.get("w", LEAF_W) + CONTAINER_PAD)
        max_bottom = max(max_bottom, child_layout.get("y", 0) + child_layout.get("h", LEAF_H) + CONTAINER_PAD)

    layout[container_id]["w"] = max(layout[container_id].get("w", 0), max_right, node.width or 0)
    layout[container_id]["h"] = max(layout[container_id].get("h", 0), max_bottom, node.height or 0)


def layout_siblings(
    parent_id: str | None,
    child_ids: list[str],
    nodes_by_id: dict,
    children_map: dict[str, list[str]],
    layout: dict[str, dict],
) -> None:
    x = CONTAINER_PAD if parent_id else 40
    y = CONTAINER_PAD_TOP if parent_id else ROOT_TOP
    row_height = 0
    column = 0

    for child_id in sorted(child_ids, key=lambda key: sort_key(key, nodes_by_id)):
        node = nodes_by_id[child_id]
        width, height = node_size(node)

        if column and column % MAX_COLS == 0:
            x = CONTAINER_PAD if parent_id else 40
            y += row_height + LEAF_GAP
            row_height = 0
            column = 0

        layout[child_id] = {"x": x, "y": y, "w": width, "h": height}

        if node.is_container:
            grandchildren = children_map.get(child_id, [])
            if grandchildren:
                layout_siblings(child_id, grandchildren, nodes_by_id, children_map, layout)
                fit_container_bounds(child_id, layout, children_map, nodes_by_id)
                width, height = layout[child_id]["w"], layout[child_id]["h"]

        step_x = (width + CONTAINER_GAP) if node.is_container else (LEAF_CELL_W + LEAF_GAP)
        x += step_x
        row_height = max(row_height, height)
        column += 1

    if parent_id:
        fit_container_bounds(parent_id, layout, children_map, nodes_by_id)


def build_layout(nodes_by_id: dict, children_map: dict[str, list[str]], roots: list[str]) -> dict[str, dict]:
    layout: dict[str, dict] = {}
    layout_siblings(None, roots, nodes_by_id, children_map, layout)
    for node_id, node in nodes_by_id.items():
        if node_id not in layout:
            width, height = node_size(node)
            layout[node_id] = {"x": 0, "y": 0, "w": width, "h": height}
    return layout


def edge_sort_key(edge, nodes_by_id: dict) -> tuple:
    source = nodes_by_id.get(edge.source_id)
    target = nodes_by_id.get(edge.target_id)
    source_step = source.step_number if source and source.step_number else 999
    target_step = target.step_number if target and target.step_number else 999
    is_adjacent_flow = source_step < 900 and target_step < 900 and abs(source_step - target_step) <= 1
    return (0 if is_adjacent_flow else 1, 1 if edge.style == "dashed" else 0, source_step, target_step)
