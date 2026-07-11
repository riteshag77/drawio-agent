"""Layout and XML helpers for cross-cutting service bands.

Bands are bordered, titled containers docked beside the Region/VPC inside the
Cloud/Account host — never floating past the outer cloud bound.
"""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import (
    BAND_COLORS,
    BAND_ORDER,
    find_node_by_suffix,
    is_band_container,
    is_cloud_like,
    is_region_like,
    is_vpc_like,
)
from app.adapters.outbound.diagram.layout import fit_container_bounds
from app.adapters.outbound.diagram.layout_settings import layout_settings

__all__ = ["BAND_ANCHORS", "build_band_style", "is_band_container", "layout_bands"]

BAND_ANCHORS: dict[str, tuple[str, ...]] = {
    "security": ("cloudfront", "route53", "api_gateway"),
    "logging": ("fargate", "alb", "application_load_balancer", "api_gateway"),
    "management": ("fargate", "api_gateway", "alb"),
    "integration": ("api_gateway", "fargate", "alb"),
}


def build_band_style(band_key: str, *, empty: bool = False) -> str:
    """Bordered titled group — same visual treatment as a subnet container."""
    stroke, fill = BAND_COLORS.get(band_key, ("#545B64", "#F4F4F4"))
    opacity = "60" if empty else "100"
    return (
        "rounded=1;whiteSpace=wrap;html=1;container=1;collapsible=0;recursiveResize=0;"
        "pointerEvents=0;verticalAlign=top;align=left;spacingLeft=8;spacingTop=4;"
        f"strokeColor={stroke};fillColor={fill};opacity={opacity};dashed=1;dashPattern=4 4;"
        f"fontColor={stroke};fontSize=11;fontStyle=1;"
        "fontFamily=Helvetica,Arial,sans-serif;"
    )


def _pack_members(
    members: list[str],
    layout: dict[str, dict],
    nodes_by_id: dict,
    s,
) -> tuple[int, int]:
    pad_x = max(s.band_pad // 2, s.grid // 2)
    pad_top = max(s.band_pad, s.grid)
    pad_bot = max(s.grid // 2, 4)
    gap = max(s.grid, s.leaf_gap // 2)
    x = pad_x
    for mid in sorted(members):
        w = nodes_by_id[mid].width or s.leaf_w
        h = nodes_by_id[mid].height or s.leaf_h
        layout[mid] = {"x": x, "y": pad_top, "w": w, "h": h}
        x += w + gap
    band_w = max(x - gap + pad_x, 80)
    band_h = max(pad_top + s.leaf_h + pad_bot, s.band_min_h)
    return band_w, band_h


def _find_host_id(nodes_by_id: dict, children_map: dict) -> str | None:
    for node in nodes_by_id.values():
        if is_cloud_like(node):
            return node.id
    for node in nodes_by_id.values():
        if is_region_like(node):
            return node.id
    return None


def _dock_anchor(host_id: str, layout: dict, nodes_by_id: dict, children_map: dict) -> tuple[int, int, int]:
    """Return (dock_x, dock_y, anchor_bottom) in host-relative coords.

    Prefer the right edge of the top edge-service row (Route53/CloudFront) so
    security bands sit near their targets; fall back to Region/VPC right edge.
    """
    s = layout_settings()
    leaf_right = 0
    leaf_top = None
    leaf_bottom = 0
    for cid in children_map.get(host_id, []):
        node = nodes_by_id.get(cid)
        box = layout.get(cid)
        if not node or not box or node.is_container or is_band_container(node):
            continue
        leaf_right = max(leaf_right, box["x"] + box["w"])
        leaf_top = box["y"] if leaf_top is None else min(leaf_top, box["y"])
        leaf_bottom = max(leaf_bottom, box["y"] + box["h"])

    region_right = 0
    region_top = s.container_pad_top
    region_bottom = region_top
    for cid in children_map.get(host_id, []):
        node = nodes_by_id.get(cid)
        box = layout.get(cid)
        if not node or not box or is_band_container(node):
            continue
        if is_region_like(node) or is_vpc_like(node):
            region_right = max(region_right, box["x"] + box["w"])
            region_top = min(region_top, box["y"])
            region_bottom = max(region_bottom, box["y"] + box["h"])

    dock_x = max(leaf_right, region_right) + s.band_gap
    dock_y = leaf_top if leaf_top is not None else region_top
    return dock_x, dock_y, max(leaf_bottom, region_bottom)


def layout_bands(
    layout: dict[str, dict],
    nodes_by_id: dict,
    children_map: dict[str, list[str]],
    *,
    main_bounds: tuple[int, int, int, int],
) -> int:
    """Dock non-empty bands inside the cloud/region, right of Region/VPC.

    Coordinates are host-relative when bands are parented to the host.
    Returns the absolute bottom Y of content (best-effort).
    """
    s = layout_settings()
    min_x, min_y, max_x, max_y = main_bounds
    band_ids = [
        f"band_{b}" for b in BAND_ORDER
        if f"band_{b}" in nodes_by_id and children_map.get(f"band_{b}")
    ]
    if not band_ids:
        return max_y

    host_id = _find_host_id(nodes_by_id, children_map)
    # Prefer the declared parent of the first band
    for bid in band_ids:
        parent = nodes_by_id[bid].parent_id
        if parent and parent in layout:
            host_id = parent
            break

    right_ids = band_ids[:3]
    bottom_ids = band_ids[3:]

    if host_id and host_id in layout:
        dock_x, dock_y, anchor_bottom = _dock_anchor(host_id, layout, nodes_by_id, children_map)
        cursor_y = dock_y
        for band_id in right_ids:
            members = children_map.get(band_id, [])
            band_w, band_h = _pack_members(members, layout, nodes_by_id, s)
            layout[band_id] = {"x": dock_x, "y": cursor_y, "w": band_w, "h": band_h}
            cursor_y += band_h + s.band_gap

        if bottom_ids:
            cursor_x = layout.get(
                next(
                    (c for c in children_map.get(host_id, []) if is_region_like(nodes_by_id[c]) or is_vpc_like(nodes_by_id[c])),
                    host_id,
                ),
                {},
            ).get("x", s.container_pad)
            below_y = anchor_bottom + s.band_gap
            for band_id in bottom_ids:
                members = children_map.get(band_id, [])
                band_w, band_h = _pack_members(members, layout, nodes_by_id, s)
                layout[band_id] = {"x": cursor_x, "y": below_y, "w": band_w, "h": band_h}
                cursor_x += band_w + s.band_gap

        fit_container_bounds(host_id, layout, children_map, nodes_by_id, s)
        # Ensure every band fits inside host (grow host, never push band past bound)
        host = layout[host_id]
        for band_id in band_ids:
            b = layout[band_id]
            need_w = b["x"] + b["w"] + s.grid
            need_h = b["y"] + b["h"] + s.grid
            if need_w > host["w"]:
                host["w"] = need_w
            if need_h > host["h"]:
                host["h"] = need_h
        host_abs_x = layout[host_id].get("x", 0) if nodes_by_id[host_id].parent_id is None else 0
        # Approximate absolute bottom for page sizing
        return max(max_y, layout[host_id]["y"] + layout[host_id]["h"])

    # Fallback: absolute canvas rail (no host)
    rail_x = max_x + s.band_gap
    cursor_y = min_y
    bottom = max_y
    for band_id in band_ids:
        members = children_map.get(band_id, [])
        band_w, band_h = _pack_members(members, layout, nodes_by_id, s)
        layout[band_id] = {"x": rail_x, "y": cursor_y, "w": band_w, "h": band_h}
        cursor_y += band_h + s.band_gap
        bottom = max(bottom, cursor_y)
    return bottom
