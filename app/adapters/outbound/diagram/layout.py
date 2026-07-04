"""Topology + deterministic placement.

Coordinates are computed server-side so the exported .drawio file is self-contained
and renders correctly in any Draw.io client (no reliance on editor auto-layout).
Dimensions come from .env via layout_settings().
"""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import is_igw
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.adapters.outbound.diagram.service_bands import is_band_container


def _s():
    return layout_settings()


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


def is_band_container_node(node) -> bool:
    return is_band_container(node)


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


def _node_text(node) -> str:
    return f"{node.label} {node.shape_type} {node.id}".lower()


def is_entry_actor(node) -> bool:
    if node.is_container or is_igw(node):
        return False
    text = _node_text(node)
    return any(token in text for token in ("user", "client", "users", "external"))


def is_az_container(node) -> bool:
    if not node.is_container:
        return False
    text = f" {_node_text(node)} "
    return (
        "availability zone" in text
        or "_az" in text
        or " az " in text
        or " az_" in text
        or " zone a" in text
        or " zone b" in text
        or "gcp_zone" in text
    )


def is_subnet_container(node) -> bool:
    return node.is_container and "subnet" in _node_text(node)


def is_vpc_like(node) -> bool:
    text = _node_text(node)
    return node.is_container and any(token in text for token in ("vpc", "vnet", "virtual network"))


def is_region_like(node) -> bool:
    return node.is_container and "region" in _node_text(node)


def is_cloud_like(node) -> bool:
    text = _node_text(node)
    return node.is_container and any(token in text for token in ("cloud", "account", "subscription", "project"))


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


def effective_step(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> int:
    node = nodes_by_id[node_id]
    if node.step_number is not None:
        return node.step_number
    child_steps = [
        effective_step(child_id, nodes_by_id, children_map)
        for child_id in children_map.get(node_id, [])
    ]
    return min(child_steps) if child_steps else 10_000


def sort_key(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> tuple:
    node = nodes_by_id[node_id]
    return (effective_step(node_id, nodes_by_id, children_map), node_tier(node), node_id)


def service_stack_key(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> tuple:
    text = _node_text(nodes_by_id[node_id])
    role = 1
    if any(token in text for token in ("gateway", "load balancer", "alb", "nlb", "api gateway")):
        role = 2
    if any(token in text for token in ("cache", "redis", "elasticache", "memorystore")):
        role = 3
    if any(token in text for token in ("database", "aurora", "rds", "dynamodb", "sql", "cloud_sql")):
        role = 4
    if any(token in text for token in ("nat", "internet gateway", "igw")):
        role = 5
    return (role, effective_step(node_id, nodes_by_id, children_map), node_id)


def subnet_tier_key(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> tuple:
    return (node_tier(nodes_by_id[node_id]), node_id)


def node_size(node) -> tuple[int, int]:
    s = _s()
    if node.is_container:
        return node.width or s.default_container_w, node.height or s.default_container_h
    return node.width or s.leaf_w, node.height or s.leaf_h


def fit_container_bounds(container_id, layout, children_map, nodes_by_id) -> None:
    s = _s()
    children = children_map.get(container_id, [])
    if not children or container_id not in layout:
        return
    node = nodes_by_id[container_id]
    max_right = s.container_pad
    max_bottom = s.container_pad_top
    for child_id in children:
        child = layout.get(child_id, {})
        max_right = max(max_right, child.get("x", 0) + child.get("w", s.leaf_w) + s.container_pad)
        max_bottom = max(max_bottom, child.get("y", 0) + child.get("h", s.leaf_h) + s.container_pad)
    layout[container_id]["w"] = max(layout[container_id].get("w", 0), max_right, node.width or 0)
    layout[container_id]["h"] = max(layout[container_id].get("h", 0), max_bottom, node.height or 0)


def _place_igw_on_vpc(vpc_id: str, layout: dict, nodes_by_id: dict, children_map: dict[str, list[str]]) -> None:
    """Center IGW on the top edge of the VPC, straddling the border (AWS convention)."""
    s = _s()
    igw_ids = [cid for cid in children_map.get(vpc_id, []) if is_igw(nodes_by_id[cid])]
    if not igw_ids or vpc_id not in layout:
        return
    vpc_w = layout[vpc_id]["w"]
    for igw_id in igw_ids:
        w, h = node_size(nodes_by_id[igw_id])
        layout[igw_id] = {
            "x": max(s.igw_min_x_pad, vpc_w // 2 - w // 2),
            "y": s.igw_vpc_y,
            "w": w,
            "h": h,
        }


def _place_children(child_id, layout, children_map, nodes_by_id) -> tuple[int, int]:
    grandchildren = children_map.get(child_id, [])
    if grandchildren:
        layout_siblings(child_id, grandchildren, nodes_by_id, children_map, layout)
        fit_container_bounds(child_id, layout, children_map, nodes_by_id)
    return layout[child_id]["w"], layout[child_id]["h"]


def _vertical_stack(parent_id, child_ids, nodes_by_id, children_map, layout, *, sort_fn=sort_key) -> None:
    s = _s()
    x, y = s.container_pad, s.container_pad_top
    for child_id in sorted(child_ids, key=lambda key: sort_fn(key, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        width, height = node_size(node)
        layout[child_id] = {"x": x, "y": y, "w": width, "h": height}
        if node.is_container:
            width, height = _place_children(child_id, layout, children_map, nodes_by_id)
        y += height + s.leaf_gap
    if parent_id:
        fit_container_bounds(parent_id, layout, children_map, nodes_by_id)


def _horizontal_flow(parent_id, child_ids, nodes_by_id, children_map, layout, *, origin_y=None) -> int:
    s = _s()
    origin_y = s.container_pad_top if origin_y is None else origin_y
    x, y = s.container_pad, origin_y
    row_height = 0
    for child_id in sorted(child_ids, key=lambda key: sort_key(key, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        width, height = node_size(node)
        layout[child_id] = {"x": x, "y": y, "w": width, "h": height}
        if node.is_container:
            width, height = _place_children(child_id, layout, children_map, nodes_by_id)
        step = (width + s.container_gap) if node.is_container else (s.leaf_cell_w + s.leaf_gap)
        x += step
        row_height = max(row_height, height)
    if parent_id:
        fit_container_bounds(parent_id, layout, children_map, nodes_by_id)
    return row_height


def _tier_columns(parent_id, child_ids, nodes_by_id, children_map, layout) -> None:
    s = _s()
    tier_ids = sorted(child_ids, key=lambda key: (node_tier(nodes_by_id[key]), sort_key(key, nodes_by_id, children_map)))
    x = s.container_pad
    max_height = 0
    for child_id in tier_ids:
        node = nodes_by_id[child_id]
        layout[child_id] = {
            "x": x, "y": s.container_pad_top,
            "w": node.width or s.tier_column_w,
            "h": node.height or s.default_container_h,
        }
        _place_children(child_id, layout, children_map, nodes_by_id)
        layout[child_id]["w"] = max(layout[child_id]["w"], s.tier_column_w)
        max_height = max(max_height, layout[child_id]["h"])
        x += layout[child_id]["w"] + s.container_gap
    for child_id in tier_ids:
        layout[child_id]["h"] = max_height
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id)


def _az_columns(parent_id, child_ids, nodes_by_id, children_map, layout) -> None:
    s = _s()
    az_ids = sorted(child_ids, key=lambda key: sort_key(key, nodes_by_id, children_map))
    tier_heights: dict[int, int] = {}
    az_subnets: dict[str, list[str]] = {}

    for az_id in az_ids:
        subnets = [cid for cid in children_map.get(az_id, []) if is_subnet_container(nodes_by_id[cid])]
        az_subnets[az_id] = sorted(subnets, key=lambda key: subnet_tier_key(key, nodes_by_id, children_map))
        for subnet_id in az_subnets[az_id]:
            node = nodes_by_id[subnet_id]
            width, height = node_size(node)
            layout[subnet_id] = {"x": s.container_pad, "y": 0, "w": width, "h": height}
            _place_children(subnet_id, layout, children_map, nodes_by_id)
            fit_container_bounds(subnet_id, layout, children_map, nodes_by_id)
            tier = node_tier(nodes_by_id[subnet_id])
            tier_heights[tier] = max(tier_heights.get(tier, 0), layout[subnet_id]["h"])

    tier_y: dict[int, int] = {}
    y_cursor = s.container_pad_top
    for tier in sorted(tier_heights):
        tier_y[tier] = y_cursor
        y_cursor += tier_heights[tier] + s.leaf_gap

    x = s.container_pad
    max_az_height = 0
    for az_id in az_ids:
        node = nodes_by_id[az_id]
        az_width = max(node.width or s.az_column_w, s.az_column_w)
        for subnet_id in az_subnets.get(az_id, []):
            tier = node_tier(nodes_by_id[subnet_id])
            layout[subnet_id]["x"] = s.container_pad
            layout[subnet_id]["y"] = tier_y[tier]
            layout[subnet_id]["h"] = tier_heights[tier]

        az_height = y_cursor - s.container_pad_top + s.container_pad
        layout[az_id] = {"x": x, "y": s.container_pad_top, "w": az_width, "h": az_height}
        fit_container_bounds(az_id, layout, children_map, nodes_by_id)
        max_az_height = max(max_az_height, layout[az_id]["h"])
        x += layout[az_id]["w"] + s.container_gap

    for az_id in az_ids:
        layout[az_id]["h"] = max_az_height
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id)


def _edge_then_containers(parent_id, child_ids, nodes_by_id, children_map, layout) -> None:
    s = _s()
    edge_ids = [
        cid for cid in child_ids
        if not nodes_by_id[cid].is_container and not is_band_container_node(nodes_by_id[cid])
    ]
    container_ids = [cid for cid in child_ids if nodes_by_id[cid].is_container and not is_band_container_node(nodes_by_id[cid])]
    edge_height = _horizontal_flow(parent_id, edge_ids, nodes_by_id, children_map, layout) if edge_ids else 0
    container_y = s.container_pad_top + (edge_height + s.leaf_gap * 2 if edge_ids else 0)
    x = s.container_pad
    for child_id in sorted(container_ids, key=lambda key: sort_key(key, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        layout[child_id] = {
            "x": x, "y": container_y,
            "w": node.width or s.default_container_w,
            "h": node.height or s.default_container_h,
        }
        width, _ = _place_children(child_id, layout, children_map, nodes_by_id)
        x += width + s.container_gap
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id)


def _root_canvas(child_ids, nodes_by_id, children_map, layout) -> None:
    s = _s()
    entry_ids = [cid for cid in child_ids if is_entry_actor(nodes_by_id[cid])]
    main_ids = [
        cid for cid in child_ids
        if nodes_by_id[cid].is_container and cid not in entry_ids and not is_band_container_node(nodes_by_id[cid])
    ]
    loose_ids = [cid for cid in child_ids if cid not in entry_ids and cid not in main_ids and not is_band_container_node(nodes_by_id[cid])]

    x = s.root_left
    for child_id in sorted(entry_ids, key=lambda key: sort_key(key, nodes_by_id, children_map)):
        width, height = node_size(nodes_by_id[child_id])
        layout[child_id] = {"x": x, "y": s.root_top, "w": width, "h": height}
        x += width + s.leaf_gap

    for child_id in sorted(main_ids, key=lambda key: sort_key(key, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        width, height = node_size(node)
        layout[child_id] = {"x": x, "y": s.root_top, "w": width, "h": height}
        width, _ = _place_children(child_id, layout, children_map, nodes_by_id)
        x += width + s.container_gap

    for child_id in sorted(loose_ids, key=lambda key: sort_key(key, nodes_by_id, children_map)):
        width, height = node_size(nodes_by_id[child_id])
        layout[child_id] = {"x": x, "y": s.root_top, "w": width, "h": height}
        x += width + s.leaf_gap


def layout_siblings(parent_id, child_ids, nodes_by_id, children_map, layout) -> None:
    if not child_ids:
        return

    if parent_id is None:
        _root_canvas(child_ids, nodes_by_id, children_map, layout)
        return

    parent = nodes_by_id[parent_id]
    children = [nodes_by_id[cid] for cid in child_ids]

    if all(not child.is_container for child in children):
        if is_region_like(parent) or is_cloud_like(parent):
            _horizontal_flow(parent_id, child_ids, nodes_by_id, children_map, layout)
        else:
            _vertical_stack(parent_id, child_ids, nodes_by_id, children_map, layout, sort_fn=service_stack_key)
        return

    if is_az_container(parent):
        _vertical_stack(parent_id, child_ids, nodes_by_id, children_map, layout, sort_fn=subnet_tier_key)
        return

    if is_vpc_like(parent):
        az_children = [cid for cid in child_ids if is_az_container(nodes_by_id[cid])]
        igw_children = [cid for cid in child_ids if is_igw(nodes_by_id[cid])]
        subnet_children = [cid for cid in child_ids if is_subnet_container(nodes_by_id[cid])]
        if az_children:
            _az_columns(parent_id, az_children, nodes_by_id, children_map, layout)
            for igw_id in igw_children:
                _place_igw_on_vpc(parent_id, layout, nodes_by_id, children_map)
            return
        if len(subnet_children) >= 2:
            _tier_columns(parent_id, subnet_children, nodes_by_id, children_map, layout)
            for igw_id in igw_children:
                _place_igw_on_vpc(parent_id, layout, nodes_by_id, children_map)
            return

    if is_region_like(parent) or is_cloud_like(parent):
        _edge_then_containers(parent_id, child_ids, nodes_by_id, children_map, layout)
        return

    s = _s()
    x, y, row_height, column = s.container_pad, s.container_pad_top, 0, 0
    for child_id in sorted(child_ids, key=lambda key: sort_key(key, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        width, height = node_size(node)
        if column and column % s.max_cols == 0:
            x, row_height, column = s.container_pad, 0, 0
            y += row_height + s.leaf_gap
        layout[child_id] = {"x": x, "y": y, "w": width, "h": height}
        if node.is_container:
            width, height = _place_children(child_id, layout, children_map, nodes_by_id)
        step = (width + s.container_gap) if node.is_container else (s.leaf_cell_w + s.leaf_gap)
        x += step
        row_height = max(row_height, height)
        column += 1
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id)


def build_layout(nodes_by_id: dict, children_map: dict[str, list[str]], roots: list[str]) -> dict[str, dict]:
    layout: dict[str, dict] = {}
    main_roots = [r for r in roots if not r.startswith("band_")]
    layout_siblings(None, main_roots, nodes_by_id, children_map, layout)
    s = _s()
    for node_id, node in nodes_by_id.items():
        if node_id not in layout:
            width, height = node_size(node)
            layout[node_id] = {"x": 0, "y": 0, "w": width, "h": height}
    return layout


def absolute_box(node_id: str, layout: dict[str, dict], nodes_by_id: dict) -> tuple[int, int, int, int]:
    x = y = 0
    chain: list[str] = []
    current: str | None = node_id
    while current and current in nodes_by_id:
        chain.append(current)
        parent = nodes_by_id[current].parent_id
        current = parent if parent and parent in nodes_by_id else None
    for nid in reversed(chain):
        nl = layout.get(nid, {})
        x += nl.get("x", 0)
        y += nl.get("y", 0)
    s = _s()
    nl = layout.get(node_id, {})
    return x, y, nl.get("w", s.leaf_w), nl.get("h", s.leaf_h)


def absolute_layout_bounds(layout, nodes_by_id, children_map, roots) -> tuple[int, int, int, int]:
    s = _s()
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


def edge_sort_key(edge, nodes_by_id: dict) -> tuple:
    source = nodes_by_id.get(edge.source_id)
    target = nodes_by_id.get(edge.target_id)
    source_step = source.step_number if source and source.step_number else 999
    target_step = target.step_number if target and target.step_number else 999
    is_adjacent_flow = source_step < 900 and target_step < 900 and abs(source_step - target_step) <= 1
    return (0 if is_adjacent_flow else 1, 1 if edge.style == "dashed" else 0, source_step, target_step)


# Backward-compatible module-level aliases (read from settings at import).
_cfg = layout_settings()
LEAF_W, LEAF_H = _cfg.leaf_w, _cfg.leaf_h
LEAF_CELL_W, LEAF_GAP = _cfg.leaf_cell_w, _cfg.leaf_gap
CONTAINER_PAD, CONTAINER_PAD_TOP, CONTAINER_GAP = _cfg.container_pad, _cfg.container_pad_top, _cfg.container_gap
