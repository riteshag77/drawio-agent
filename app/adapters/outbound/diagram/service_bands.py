"""Layout and XML helpers for cross-cutting service bands.

Bands are compact clusters placed beside the main diagram near their
attachment targets — not full-width strips under the page.
"""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import (
    BAND_COLORS,
    BAND_ORDER,
    find_node_by_suffix,
    is_band_container,
)
from app.adapters.outbound.diagram.layout import absolute_box
from app.adapters.outbound.diagram.layout_settings import layout_settings

__all__ = ["BAND_ANCHORS", "build_band_style", "is_band_container", "layout_bands"]

# Preferred attachment targets per band (first match wins).
BAND_ANCHORS: dict[str, tuple[str, ...]] = {
    "security": ("cloudfront", "route53", "api_gateway"),
    "logging": ("fargate", "alb", "application_load_balancer", "api_gateway"),
    "management": ("fargate", "api_gateway", "alb"),
    "integration": ("api_gateway", "fargate", "alb"),
}


def build_band_style(band_key: str, *, empty: bool = False) -> str:
    """Label-only group: no AWS group shape / icon chrome (box effectively hidden)."""
    stroke, _fill = BAND_COLORS.get(band_key, ("#545B64", "#F4F4F4"))
    # ponytail: invisible container chrome; icons + title carry the grouping
    return (
        "rounded=0;whiteSpace=wrap;html=1;container=1;collapsible=0;recursiveResize=0;"
        "pointerEvents=0;verticalAlign=top;align=left;spacingLeft=2;spacingTop=0;"
        f"strokeColor=none;fillColor=none;fontColor={stroke};fontSize=10;fontStyle=1;"
        "fontFamily=Helvetica,Arial,sans-serif;"
    )


def _band_target_id(band_key: str, nodes_by_id: dict) -> str | None:
    for suffix in BAND_ANCHORS.get(band_key, ()):
        node = find_node_by_suffix(nodes_by_id, suffix)
        if node:
            return node.id
    return None


def _pack_members(
    band_id: str,
    members: list[str],
    layout: dict[str, dict],
    nodes_by_id: dict,
    s,
) -> tuple[int, int]:
    """Place members in a tight row; return (band_w, band_h)."""
    pad_x, pad_top, pad_bot = 4, 18, 4
    gap = max(12, s.leaf_gap // 2)
    x = pad_x
    for mid in sorted(members):
        w = nodes_by_id[mid].width or s.leaf_w
        h = nodes_by_id[mid].height or s.leaf_h
        layout[mid] = {"x": x, "y": pad_top, "w": w, "h": h}
        x += w + gap
    band_w = max(x - gap + pad_x, 80)
    band_h = pad_top + s.leaf_h + pad_bot
    return band_w, band_h


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int], pad: int = 8) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + pad <= bx or bx + bw + pad <= ax or ay + ah + pad <= by or by + bh + pad <= ay)


def layout_bands(
    layout: dict[str, dict],
    nodes_by_id: dict,
    children_map: dict[str, list[str]],
    *,
    main_bounds: tuple[int, int, int, int],
) -> int:
    """Place non-empty bands as compact clusters near their anchors / right rail.

    Returns the bottom Y of the lowest band (absolute).
    """
    s = layout_settings()
    min_x, min_y, max_x, max_y = main_bounds
    band_ids = [
        f"band_{b}" for b in BAND_ORDER
        if f"band_{b}" in nodes_by_id and children_map.get(f"band_{b}")
    ]
    if not band_ids:
        return max_y

    rail_x = max_x + s.band_gap
    # Treat main diagram as occupied so bands don't sit on top of VPC/region
    occupied: list[tuple[int, int, int, int]] = [(min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y))]
    bottom = max_y

    for band_id in band_ids:
        band_key = band_id.replace("band_", "", 1)
        members = children_map.get(band_id, [])
        band_w, band_h = _pack_members(band_id, members, layout, nodes_by_id, s)

        target_id = _band_target_id(band_key, nodes_by_id)
        if target_id and target_id in layout:
            tx, ty, tw, th = absolute_box(target_id, layout, nodes_by_id)
            # Try below anchor first; fall back to right rail at anchor Y
            bx, by = tx, ty + th + max(12, s.band_gap // 2)
            if any(_overlaps((bx, by, band_w, band_h), box) for box in occupied):
                bx, by = rail_x, ty
        else:
            bx, by = rail_x, min_y

        guard = 0
        while guard < 50 and any(_overlaps((bx, by, band_w, band_h), box) for box in occupied):
            if bx < rail_x:
                bx, by = rail_x, by
            else:
                by += band_h + s.band_gap
            guard += 1

        layout[band_id] = {"x": bx, "y": by, "w": band_w, "h": band_h}
        occupied.append((bx, by, band_w, band_h))
        bottom = max(bottom, by + band_h)

    return bottom
