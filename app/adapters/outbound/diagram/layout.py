"""Layout engine — uses LLM-provided coordinates when present, else auto-places.

The LLM can specify x/y/width/height on nodes for precise positioning.
When coordinates are missing, a simple left-to-right fallback with child
placement inside containers is used.
"""

from __future__ import annotations

from app.adapters.outbound.diagram.layout_settings import LayoutSettings, layout_settings

__all__ = [
    "absolute_box",
    "absolute_layout_bounds",
    "build_children_map",
    "build_layout",
    "edge_parent_cell",
    "edge_sort_key",
]


def build_children_map(nodes) -> tuple[dict[str, list[str]], list[str], dict]:
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
        if current.parent_id in seen:
            break
        seen.add(current.parent_id)
        current = nodes_by_id[current.parent_id]
    return seen


def edge_parent_cell(source_id: str, target_id: str, nodes_by_id: dict) -> str:
    src = nodes_by_id.get(source_id)
    tgt = nodes_by_id.get(target_id)
    if not src or not tgt:
        return "1"
    src_parent = src.parent_id
    tgt_parent = tgt.parent_id
    if src_parent and src_parent == tgt_parent and src_parent in nodes_by_id:
        return src_parent
    return "1"


def edge_sort_key(edge, nodes_by_id: dict) -> tuple:
    source = nodes_by_id.get(edge.source_id)
    target = nodes_by_id.get(edge.target_id)
    source_step = source.step_number if source and source.step_number else 999
    target_step = target.step_number if target and target.step_number else 999
    adjacent = source_step < 900 and target_step < 900 and abs(source_step - target_step) <= 1
    return (0 if adjacent else 1, 1 if edge.style == "dashed" else 0, source_step, target_step)


def node_size(node, s: LayoutSettings | None = None) -> tuple[int, int]:
    s = s or layout_settings()
    if node.x is not None and node.y is not None and node.width and node.height:
        return node.width, node.height
    if node.is_container:
        return node.width or s.default_container_w, node.height or s.default_container_h
    return node.width or s.leaf_w, node.height or s.leaf_h


def _llm_placed(node) -> bool:
    return node.x is not None and node.y is not None


def _step_sort(nodes_by_id):
    def key(nid):
        node = nodes_by_id[nid]
        step = node.step_number if node.step_number is not None else 9999
        return (step, nid)
    return key


def _is_az_child(name: str) -> bool:
    t = name.lower().replace("_", " ").replace("-", " ")
    return "availability zone" in t or " az " in f" {t} " or "zone a" in t or "zone b" in t or t.strip().startswith("az")


def _place_children(parent_id, children_map, nodes_by_id, layout, s):
    children = children_map.get(parent_id, [])
    if not children:
        return
    pad = s.container_pad
    sorted_children = sorted(children, key=_step_sort(nodes_by_id))
    all_az = all(_is_az_child(nodes_by_id[c].label) or _is_az_child(c) for c in sorted_children)
    cur_x = pad
    cur_y = s.container_pad_top
    for child_id in sorted_children:
        node = nodes_by_id[child_id]
        if _llm_placed(node):
            continue
        w, h = node_size(node, s)
        layout[child_id] = {"x": pad if all_az else cur_x, "y": cur_y, "w": w, "h": h}
        if all_az:
            cur_y += h + s.container_gap
        else:
            cur_x += w + s.leaf_gap
        if node.is_container and children_map.get(child_id):
            _place_children(child_id, children_map, nodes_by_id, layout, s)
    if children:
        max_right = max(layout[c]["x"] + layout[c]["w"] for c in children)
        max_bottom = max(layout[c]["y"] + layout[c]["h"] for c in children)
        layout[parent_id]["w"] = max(max_right + pad, pad * 2, layout[parent_id]["w"])
        layout[parent_id]["h"] = max(max_bottom + pad, s.container_pad_top + pad, layout[parent_id]["h"])


def build_layout(nodes_by_id: dict, children_map: dict[str, list[str]], roots: list[str]) -> dict[str, dict]:
    s = layout_settings()
    layout: dict[str, dict] = {}

    for nid, node in nodes_by_id.items():
        if _llm_placed(node):
            layout[nid] = {"x": node.x, "y": node.y, "w": node.width or s.leaf_w, "h": node.height or s.leaf_h}

    x = s.root_left
    y = s.root_top
    sorted_roots = sorted((r for r in roots if r not in layout), key=_step_sort(nodes_by_id))
    for nid in sorted_roots:
        node = nodes_by_id[nid]
        w, h = node_size(node, s)
        layout[nid] = {"x": x, "y": y, "w": w, "h": h}
        if node.is_container and children_map.get(nid):
            _place_children(nid, children_map, nodes_by_id, layout, s)
        x += layout[nid]["w"] + s.leaf_gap

    return layout


def absolute_box(node_id: str, layout: dict[str, dict], nodes_by_id: dict) -> tuple[int, int, int, int]:
    s = layout_settings()
    ax = ay = 0
    chain: list[str] = []
    current: str | None = node_id
    while current and current in nodes_by_id:
        chain.append(current)
        parent = nodes_by_id[current].parent_id
        current = parent if parent and parent in nodes_by_id else None
    for nid in reversed(chain):
        nl = layout.get(nid, {})
        ax += nl.get("x", 0)
        ay += nl.get("y", 0)
    nl = layout.get(node_id, {})
    return ax, ay, nl.get("w", s.leaf_w), nl.get("h", s.leaf_h)


def absolute_layout_bounds(layout, nodes_by_id, children_map, roots) -> tuple[int, int, int, int]:
    s = layout_settings()
    min_x = min_y = 10_000
    max_x = max_y = 0

    def visit(node_id: str, off_x: int, off_y: int) -> None:
        nonlocal min_x, min_y, max_x, max_y
        nl = layout.get(node_id, {})
        x, y = off_x + nl.get("x", 0), off_y + nl.get("y", 0)
        w, h = nl.get("w", s.leaf_w), nl.get("h", s.leaf_h)
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x + w), max(max_y, y + h)
        for child_id in children_map.get(node_id, []):
            visit(child_id, x, y)

    for root_id in roots:
        visit(root_id, 0, 0)
    if min_x == 10_000:
        return 0, 0, s.default_container_w, s.default_container_h
    return min_x, min_y, max_x, max_y
