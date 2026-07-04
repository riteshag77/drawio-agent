"""Layout and XML helpers for cross-cutting service bands."""

from __future__ import annotations

from app.adapters.outbound.diagram.diagram_classify import BAND_COLORS, BAND_ORDER
from app.adapters.outbound.diagram.layout_settings import layout_settings


def is_band_container(node) -> bool:
    return bool(getattr(node, "id", "").startswith("band_"))


def build_band_style(band_key: str, *, empty: bool = False) -> str:
    stroke, fill = BAND_COLORS.get(band_key, ("#545B64", "#F4F4F4"))
    dashed = "1" if empty else "0"
    return (
        "points=[[0,0],[0.25,0],[0.5,0],[0.75,0],[1,0],[1,0.25],[1,0.5],[1,0.75],[1,1],"
        "[0.75,1],[0.5,1],[0.25,1],[0,1],[0,0.75],[0,0.5],[0,0.25]];"
        "outlineConnect=0;gradientColor=none;html=1;whiteSpace=wrap;fontSize=11;fontStyle=1;"
        f"container=1;pointerEvents=0;collapsible=0;recursiveResize=0;rounded=1;"
        f"shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_aws_cloud_alt;"
        f"strokeColor={stroke};fillColor={fill};verticalAlign=top;align=left;spacingLeft=20;"
        f"fontColor={stroke};dashed={dashed};strokeWidth=1.5;fontFamily=Helvetica,Arial,sans-serif;"
    )


def layout_bands(
    layout: dict[str, dict],
    nodes_by_id: dict,
    children_map: dict[str, list[str]],
    *,
    start_y: int,
    diagram_width: int,
) -> int:
    """Place band containers below main content; return total height consumed."""
    s = layout_settings()
    y = start_y + s.band_gap
    band_ids = [f"band_{b}" for b in BAND_ORDER if f"band_{b}" in nodes_by_id]
    if not band_ids:
        return start_y

    for band_id in band_ids:
        members = children_map.get(band_id, [])
        x = s.flow_notes_x
        inner_y = s.container_pad_top
        max_right = s.container_pad
        for mid in sorted(members):
            node = nodes_by_id[mid]
            w = s.leaf_w
            h = s.leaf_h
            layout[mid] = {"x": x, "y": inner_y, "w": w, "h": h}
            max_right = max(max_right, x + w + s.container_pad)
            x += s.leaf_cell_w + s.leaf_gap

        band_h = max(s.band_min_h, inner_y + s.leaf_h + s.band_pad)
        band_w = max(diagram_width - s.flow_notes_x * 2, max_right)
        layout[band_id] = {"x": s.flow_notes_x, "y": y, "w": band_w, "h": band_h}
        y += band_h + s.band_gap

    return y
