"""Draw.io mxGraph XML fragment builders — simplified for MCP routing."""

from __future__ import annotations

import html as html_module

from app.adapters.outbound.diagram.layout import absolute_box, edge_parent_cell
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.adapters.outbound.diagram.styles import build_style


def escape_xml(text: str) -> str:
    return html_module.escape(str(text or ""), quote=True)


def append_vertex(
    xml: list[str],
    node,
    layout: dict,
    parent_cell: str,
    accent: str,
    *,
    style_overrides: dict[str, str] | None = None,
) -> None:
    s = layout_settings()
    node_layout = layout.get(node.id, {"w": s.leaf_w, "h": s.leaf_h, "x": 0, "y": 0})
    x = node_layout.get("x", 0)
    y = node_layout.get("y", 0)
    width = node_layout["w"]
    height = node_layout["h"]

    style = build_style(node.shape_type, overrides=style_overrides)
    if "fontFamily=" not in style:
        style += ";fontFamily=Helvetica,Arial,sans-serif;"

    connectable = ' connectable="0"' if node.is_container else ""
    xml.append(
        f'    <mxCell id="{node.id}" value="{escape_xml(node.label)}" '
        f'style="{style}" vertex="1"{connectable} parent="{parent_cell}">'
    )
    xml.append(f'      <mxGeometry x="{x}" y="{y}" width="{width}" height="{height}" as="geometry" />')
    xml.append("    </mxCell>")


def _edge_anchors(
    src_box: tuple[int, int, int, int],
    tgt_box: tuple[int, int, int, int],
) -> tuple[float, float, float, float]:
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    scx, scy = sx + sw / 2, sy + sh / 2
    tcx, tcy = tx + tw / 2, ty + th / 2
    dx, dy = tcx - scx, tcy - scy
    if abs(dx) >= abs(dy):
        return (1.0, 0.5, 0.0, 0.5) if dx >= 0 else (0.0, 0.5, 1.0, 0.5)
    return (0.5, 1.0, 0.5, 0.0) if dy >= 0 else (0.5, 0.0, 0.5, 1.0)


def append_edge(
    xml: list[str],
    edge,
    *,
    cell_id: int,
    layout: dict,
    nodes_by_id: dict,
    default_edge_color: str,
    platform_key: str,
) -> None:
    _ = platform_key
    src_box = absolute_box(edge.source_id, layout, nodes_by_id)
    tgt_box = absolute_box(edge.target_id, layout, nodes_by_id)
    exit_x, exit_y, entry_x, entry_y = _edge_anchors(src_box, tgt_box)

    is_dlq = (edge.label or "").upper() in ("DLQ", "ERROR", "DEAD-LETTER") or bool(edge.label and "dlq" in edge.label.lower())
    color = "#DD344C" if is_dlq else edge.color or default_edge_color
    s = layout_settings()

    label = (edge.label or "").strip()
    style_parts = [
        "edgeStyle=orthogonalEdgeStyle",
        "rounded=1",
        "orthogonalLoop=1",
        "jettySize=auto",
        "html=1",
        "endArrow=classic",
        "startArrow=none",
        f"exitX={exit_x}",
        f"exitY={exit_y}",
        f"entryX={entry_x}",
        f"entryY={entry_y}",
        "jumpStyle=arc",
        "jumpSize=6",
        f"strokeColor={color}",
        f"fontColor={color}",
        "strokeWidth=2",
    ]
    if edge.style == "dashed":
        style_parts.append("dashed=1")
    if edge.style == "bold":
        style_parts.append("strokeWidth=3")
    if label:
        style_parts.append("labelBackgroundColor=#F5F5F5")
        style_parts.append("fontSize=11")

    edge_style = ";".join(style_parts) + ";"
    parent = edge_parent_cell(edge.source_id, edge.target_id, nodes_by_id)
    value_attr = f' value="{escape_xml(label)}"' if label else ""
    xml.append(
        f'    <mxCell id="edge_{cell_id}"{value_attr} style="{edge_style}" edge="1" '
        f'parent="{parent}" source="{edge.source_id}" target="{edge.target_id}">'
    )
    xml.append('      <mxGeometry relative="1" as="geometry" />')
    xml.append("    </mxCell>")


def append_flow_notes(
    xml: list[str],
    flow_notes: list[tuple[int, str, str]],
    accent: str,
    title: str,
    *,
    notes_x: int | None = None,
    notes_y: int | None = None,
    notes_width: int | None = None,
) -> tuple[int, int]:
    s = layout_settings()
    notes_x = s.flow_notes_x if notes_x is None else notes_x
    notes_y = notes_y if notes_y is not None else 680
    notes_width = notes_width if notes_width is not None else s.flow_notes_max_w
    notes_height = 52 + len(flow_notes) * s.flow_notes_row_h
    heading = f"{html_module.escape(title)} — Flow Notes" if title else "Flow Notes"

    rows = [
        '<div style="font-family:Helvetica,Arial,sans-serif;color:#1A2233;text-align:left;padding:10px 14px;">'
        f'<div style="font-size:14px;font-weight:700;margin-bottom:8px;color:{accent};">{heading}</div>'
    ]
    for number, note_title, summary in flow_notes:
        safe_title = html_module.escape(note_title)
        safe_summary = html_module.escape(summary)
        title_html = f"<b>{safe_title}</b>" if safe_title else ""
        separator = " — " if safe_title and safe_summary else ""
        rows.append(
            '<div style="font-size:12px;line-height:1.45;margin-bottom:5px;">'
            f'<span style="display:inline-block;min-width:20px;font-weight:700;color:{accent};">'
            f"{number}.</span> {title_html}{separator}{safe_summary}</div>"
        )
    rows.append("</div>")

    xml.append(
        f'    <mxCell id="flow_notes" value="{escape_xml("".join(rows))}" '
        f'style="rounded=1;arcSize=4;whiteSpace=wrap;html=1;fillColor=#FBFCFE;'
        f'strokeColor={accent};strokeWidth=1.5;align=left;verticalAlign=top;spacing=2;" '
        f'vertex="1" parent="1" movable="0" resizable="0" deletable="0">'
    )
    xml.append(
        f'      <mxGeometry x="{notes_x}" y="{notes_y}" width="{notes_width}" '
        f'height="{notes_height}" as="geometry" />'
    )
    xml.append("    </mxCell>")
    page_width = max(notes_x + notes_width + s.page_margin, s.default_page_w)
    page_height = notes_y + notes_height + s.page_margin
    return page_width, page_height
