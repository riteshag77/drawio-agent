"""Deterministic diagram placement from parent_id hierarchy + step_number.

Server writes absolute coordinates so exported .drawio files render anywhere.
Dimensions come from .env via layout_settings().
"""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import (
    is_az_container,
    is_band_container,
    is_cloud_like,
    is_entry_actor,
    is_igw,
    is_region_like,
    is_subnet_container,
    is_vpc_like,
    node_text,
    node_tier,
)
from app.adapters.outbound.diagram.layout_settings import LayoutSettings, layout_settings

# Re-export classifiers so existing `from layout import is_vpc_like` keeps working.
__all__ = [
    "absolute_box",
    "absolute_layout_bounds",
    "build_children_map",
    "build_layout",
    "edge_sort_key",
    "is_az_container",
    "is_cloud_like",
    "is_entry_actor",
    "is_region_like",
    "is_subnet_container",
    "is_vpc_like",
    "layout_siblings",
    "node_tier",
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
        seen.add(current.parent_id)
        current = nodes_by_id[current.parent_id]
    return seen


def lowest_common_ancestor_parent(source_id: str, target_id: str, nodes_by_id: dict) -> str:
    target_ancestors = ancestor_ids(target_id, nodes_by_id)
    for ancestor in ancestor_ids(source_id, nodes_by_id):
        if ancestor in target_ancestors:
            return ancestor
    return "1"


# ── Sort keys ───────────────────────────────────────────────────────────────

def effective_step(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> int:
    node = nodes_by_id[node_id]
    if node.step_number is not None:
        return node.step_number
    child_steps = [
        effective_step(cid, nodes_by_id, children_map) for cid in children_map.get(node_id, [])
    ]
    return min(child_steps) if child_steps else 10_000


def sort_key(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> tuple:
    return (effective_step(node_id, nodes_by_id, children_map), node_tier(nodes_by_id[node_id]), node_id)


def service_stack_key(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> tuple:
    text = node_text(nodes_by_id[node_id])
    role = 1
    if any(t in text for t in ("gateway", "load balancer", "alb", "nlb", "api gateway")):
        role = 2
    elif any(t in text for t in ("cache", "redis", "elasticache", "memorystore")):
        role = 3
    elif any(t in text for t in ("database", "aurora", "rds", "dynamodb", "sql", "cloud_sql")):
        role = 4
    elif any(t in text for t in ("nat", "internet gateway", "igw")):
        role = 5
    return (role, effective_step(node_id, nodes_by_id, children_map), node_id)


def subnet_tier_key(node_id: str, nodes_by_id: dict, children_map: dict[str, list[str]]) -> tuple:
    return (node_tier(nodes_by_id[node_id]), node_id)


# ── Geometry helpers ────────────────────────────────────────────────────────

def node_size(node, s: LayoutSettings | None = None) -> tuple[int, int]:
    s = s or layout_settings()
    if node.is_container:
        return node.width or s.default_container_w, node.height or s.default_container_h
    return node.width or s.leaf_w, node.height or s.leaf_h


def fit_container_bounds(container_id, layout, children_map, nodes_by_id, s: LayoutSettings | None = None) -> None:
    """Shrink-wrap container to children (scaffold node.width/height ignored)."""
    s = s or layout_settings()
    children = children_map.get(container_id, [])
    if not children or container_id not in layout:
        return
    max_right = s.container_pad
    max_bottom = s.container_pad_top
    for child_id in children:
        child = layout.get(child_id, {})
        # IGW may sit above the top edge (negative y); still count horizontal extent
        max_right = max(max_right, child.get("x", 0) + child.get("w", s.leaf_w) + s.container_pad)
        child_bottom = child.get("y", 0) + child.get("h", s.leaf_h)
        if child_bottom > 0:
            max_bottom = max(max_bottom, child_bottom + s.container_pad)
    layout[container_id]["w"] = max(max_right, s.container_pad * 2)
    layout[container_id]["h"] = max(max_bottom, s.container_pad_top + s.container_pad)


def _equalize_heights(layout: dict, ids: list[str]) -> None:
    if not ids:
        return
    h = max(layout[i]["h"] for i in ids)
    for i in ids:
        layout[i]["h"] = h


def _equalize_widths(layout: dict, ids: list[str]) -> None:
    if not ids:
        return
    w = max(layout[i]["w"] for i in ids)
    for i in ids:
        layout[i]["w"] = w


# ── Placers ─────────────────────────────────────────────────────────────────

def _place_children(child_id, layout, children_map, nodes_by_id, s: LayoutSettings) -> tuple[int, int]:
    if children_map.get(child_id):
        layout_siblings(child_id, children_map[child_id], nodes_by_id, children_map, layout, s)
        fit_container_bounds(child_id, layout, children_map, nodes_by_id, s)
    return layout[child_id]["w"], layout[child_id]["h"]


def _place_igw_on_vpc(vpc_id: str, layout: dict, nodes_by_id: dict, children_map: dict, s: LayoutSettings) -> None:
    """Center IGW on the VPC top edge (AWS reference convention)."""
    if vpc_id not in layout:
        return
    vpc_w = layout[vpc_id]["w"]
    for igw_id in children_map.get(vpc_id, []):
        if not is_igw(nodes_by_id[igw_id]):
            continue
        w, h = node_size(nodes_by_id[igw_id], s)
        layout[igw_id] = {
            "x": max(s.igw_min_x_pad, vpc_w // 2 - w // 2),
            "y": s.igw_vpc_y,
            "w": w,
            "h": h,
        }


def _vertical_stack(parent_id, child_ids, nodes_by_id, children_map, layout, s: LayoutSettings, *, sort_fn=sort_key) -> None:
    x, y = s.container_pad, s.container_pad_top
    for child_id in sorted(child_ids, key=lambda k: sort_fn(k, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        w, h = node_size(node, s)
        layout[child_id] = {"x": x, "y": y, "w": w, "h": h}
        if node.is_container:
            w, h = _place_children(child_id, layout, children_map, nodes_by_id, s)
        y += h + s.leaf_gap
    if parent_id:
        fit_container_bounds(parent_id, layout, children_map, nodes_by_id, s)


def _horizontal_flow(parent_id, child_ids, nodes_by_id, children_map, layout, s: LayoutSettings, *, origin_y=None) -> int:
    x, y = s.container_pad, origin_y if origin_y is not None else s.container_pad_top
    row_height = 0
    for child_id in sorted(child_ids, key=lambda k: sort_key(k, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        w, h = node_size(node, s)
        layout[child_id] = {"x": x, "y": y, "w": w, "h": h}
        if node.is_container:
            w, h = _place_children(child_id, layout, children_map, nodes_by_id, s)
        x += (w + s.container_gap) if node.is_container else (s.leaf_cell_w + s.leaf_gap)
        row_height = max(row_height, h)
    if parent_id:
        fit_container_bounds(parent_id, layout, children_map, nodes_by_id, s)
    return row_height


def _tier_columns(parent_id, child_ids, nodes_by_id, children_map, layout, s: LayoutSettings) -> None:
    """Side-by-side subnet tiers (public | app | data) with equal heights."""
    tier_ids = sorted(child_ids, key=lambda k: (node_tier(nodes_by_id[k]), sort_key(k, nodes_by_id, children_map)))
    x = s.container_pad
    for child_id in tier_ids:
        node = nodes_by_id[child_id]
        layout[child_id] = {
            "x": x, "y": s.container_pad_top,
            "w": node.width or s.tier_column_w,
            "h": node.height or s.default_container_h,
        }
        _place_children(child_id, layout, children_map, nodes_by_id, s)
        # Content width only — no forced tier_column_w floor
        x += layout[child_id]["w"] + s.container_gap
    _equalize_heights(layout, tier_ids)
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id, s)


def _az_columns(parent_id, child_ids, nodes_by_id, children_map, layout, s: LayoutSettings) -> None:
    """AZ columns side-by-side with synchronized subnet tier Y bands."""
    az_ids = sorted(child_ids, key=lambda k: sort_key(k, nodes_by_id, children_map))
    tier_heights: dict[int, int] = {}
    az_subnets: dict[str, list[str]] = {}

    for az_id in az_ids:
        subnets = [c for c in children_map.get(az_id, []) if is_subnet_container(nodes_by_id[c])]
        az_subnets[az_id] = sorted(subnets, key=lambda k: subnet_tier_key(k, nodes_by_id, children_map))
        for subnet_id in az_subnets[az_id]:
            w, h = node_size(nodes_by_id[subnet_id], s)
            layout[subnet_id] = {"x": s.container_pad, "y": 0, "w": w, "h": h}
            _place_children(subnet_id, layout, children_map, nodes_by_id, s)
            fit_container_bounds(subnet_id, layout, children_map, nodes_by_id, s)
            tier = node_tier(nodes_by_id[subnet_id])
            tier_heights[tier] = max(tier_heights.get(tier, 0), layout[subnet_id]["h"])

    tier_y: dict[int, int] = {}
    y_cursor = s.container_pad_top
    for tier in sorted(tier_heights):
        tier_y[tier] = y_cursor
        y_cursor += tier_heights[tier] + s.leaf_gap

    x = s.container_pad
    for az_id in az_ids:
        for subnet_id in az_subnets.get(az_id, []):
            tier = node_tier(nodes_by_id[subnet_id])
            layout[subnet_id].update(x=s.container_pad, y=tier_y[tier], h=tier_heights[tier])
        layout[az_id] = {
            "x": x, "y": s.container_pad_top,
            "w": s.container_pad * 2,
            "h": y_cursor - s.container_pad_top + s.container_pad,
        }
        fit_container_bounds(az_id, layout, children_map, nodes_by_id, s)
        x += layout[az_id]["w"] + s.container_gap

    _equalize_heights(layout, az_ids)
    _equalize_widths(layout, az_ids)
    # Re-pack X after width equalization
    x = s.container_pad
    for az_id in az_ids:
        layout[az_id]["x"] = x
        x += layout[az_id]["w"] + s.container_gap
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id, s)


def _edge_then_containers(parent_id, child_ids, nodes_by_id, children_map, layout, s: LayoutSettings) -> None:
    """Region/cloud: edge services in a top row, containers below."""
    edge_ids = [c for c in child_ids if not nodes_by_id[c].is_container and not is_band_container(nodes_by_id[c])]
    container_ids = [c for c in child_ids if nodes_by_id[c].is_container and not is_band_container(nodes_by_id[c])]
    edge_h = _horizontal_flow(parent_id, edge_ids, nodes_by_id, children_map, layout, s) if edge_ids else 0
    container_y = s.container_pad_top + (edge_h + s.leaf_gap * 2 if edge_ids else 0)
    x = s.container_pad
    for child_id in sorted(container_ids, key=lambda k: sort_key(k, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        layout[child_id] = {
            "x": x, "y": container_y,
            "w": node.width or s.default_container_w,
            "h": node.height or s.default_container_h,
        }
        w, _ = _place_children(child_id, layout, children_map, nodes_by_id, s)
        x += w + s.container_gap
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id, s)


def _root_canvas(child_ids, nodes_by_id, children_map, layout, s: LayoutSettings) -> None:
    """Canvas: entry actors left, main containers, then loose leaves."""
    entry = [c for c in child_ids if is_entry_actor(nodes_by_id[c])]
    main = [c for c in child_ids if nodes_by_id[c].is_container and c not in entry and not is_band_container(nodes_by_id[c])]
    loose = [c for c in child_ids if c not in entry and c not in main and not is_band_container(nodes_by_id[c])]

    x = s.root_left
    for child_id in sorted(entry, key=lambda k: sort_key(k, nodes_by_id, children_map)):
        w, h = node_size(nodes_by_id[child_id], s)
        layout[child_id] = {"x": x, "y": s.root_top, "w": w, "h": h}
        x += w + s.leaf_gap

    for child_id in sorted(main, key=lambda k: sort_key(k, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        w, h = node_size(node, s)
        layout[child_id] = {"x": x, "y": s.root_top, "w": w, "h": h}
        w, _ = _place_children(child_id, layout, children_map, nodes_by_id, s)
        x += w + s.container_gap

    for child_id in sorted(loose, key=lambda k: sort_key(k, nodes_by_id, children_map)):
        w, h = node_size(nodes_by_id[child_id], s)
        layout[child_id] = {"x": x, "y": s.root_top, "w": w, "h": h}
        x += w + s.leaf_gap


def _grid_fallback(parent_id, child_ids, nodes_by_id, children_map, layout, s: LayoutSettings) -> None:
    x, y, row_h, col = s.container_pad, s.container_pad_top, 0, 0
    for child_id in sorted(child_ids, key=lambda k: sort_key(k, nodes_by_id, children_map)):
        node = nodes_by_id[child_id]
        w, h = node_size(node, s)
        if col and col % s.max_cols == 0:
            x, row_h, col = s.container_pad, 0, 0
            y += row_h + s.leaf_gap
        layout[child_id] = {"x": x, "y": y, "w": w, "h": h}
        if node.is_container:
            w, h = _place_children(child_id, layout, children_map, nodes_by_id, s)
        x += (w + s.container_gap) if node.is_container else (s.leaf_cell_w + s.leaf_gap)
        row_h = max(row_h, h)
        col += 1
    fit_container_bounds(parent_id, layout, children_map, nodes_by_id, s)


# ── Dispatch ────────────────────────────────────────────────────────────────

def layout_siblings(parent_id, child_ids, nodes_by_id, children_map, layout, s: LayoutSettings | None = None) -> None:
    if not child_ids:
        return
    s = s or layout_settings()

    if parent_id is None:
        _root_canvas(child_ids, nodes_by_id, children_map, layout, s)
        return

    parent = nodes_by_id[parent_id]
    children = [nodes_by_id[c] for c in child_ids]

    if all(not c.is_container for c in children):
        if is_region_like(parent) or is_cloud_like(parent):
            _horizontal_flow(parent_id, child_ids, nodes_by_id, children_map, layout, s)
        else:
            _vertical_stack(parent_id, child_ids, nodes_by_id, children_map, layout, s, sort_fn=service_stack_key)
        return

    if is_az_container(parent):
        _vertical_stack(parent_id, child_ids, nodes_by_id, children_map, layout, s, sort_fn=subnet_tier_key)
        return

    if is_vpc_like(parent):
        azs = [c for c in child_ids if is_az_container(nodes_by_id[c])]
        subnets = [c for c in child_ids if is_subnet_container(nodes_by_id[c])]
        if azs:
            _az_columns(parent_id, azs, nodes_by_id, children_map, layout, s)
            _place_igw_on_vpc(parent_id, layout, nodes_by_id, children_map, s)
            return
        if len(subnets) >= 2:
            _tier_columns(parent_id, subnets, nodes_by_id, children_map, layout, s)
            _place_igw_on_vpc(parent_id, layout, nodes_by_id, children_map, s)
            return

    if is_region_like(parent) or is_cloud_like(parent):
        _edge_then_containers(parent_id, child_ids, nodes_by_id, children_map, layout, s)
        return

    _grid_fallback(parent_id, child_ids, nodes_by_id, children_map, layout, s)


def build_layout(nodes_by_id: dict, children_map: dict[str, list[str]], roots: list[str]) -> dict[str, dict]:
    s = layout_settings()
    layout: dict[str, dict] = {}
    main_roots = [r for r in roots if not r.startswith("band_")]
    layout_siblings(None, main_roots, nodes_by_id, children_map, layout, s)
    for node_id, node in nodes_by_id.items():
        if node_id not in layout:
            w, h = node_size(node, s)
            layout[node_id] = {"x": 0, "y": 0, "w": w, "h": h}
    return layout


def absolute_box(node_id: str, layout: dict[str, dict], nodes_by_id: dict) -> tuple[int, int, int, int]:
    s = layout_settings()
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
    nl = layout.get(node_id, {})
    return x, y, nl.get("w", s.leaf_w), nl.get("h", s.leaf_h)


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


def edge_sort_key(edge, nodes_by_id: dict) -> tuple:
    source = nodes_by_id.get(edge.source_id)
    target = nodes_by_id.get(edge.target_id)
    source_step = source.step_number if source and source.step_number else 999
    target_step = target.step_number if target and target.step_number else 999
    adjacent = source_step < 900 and target_step < 900 and abs(source_step - target_step) <= 1
    return (0 if adjacent else 1, 1 if edge.style == "dashed" else 0, source_step, target_step)
