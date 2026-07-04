"""Draw.io mxGraph XML fragment builders."""

from __future__ import annotations

import html as html_module

from app.adapters.outbound.diagram.diagram_classify import ATTACHED_LABELS
from app.adapters.outbound.diagram.edge_router import route_edge
from app.adapters.outbound.diagram.layout import absolute_box, lowest_common_ancestor_parent
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.adapters.outbound.diagram.service_bands import build_band_style, is_band_container
from app.adapters.outbound.diagram.styles import build_style


def escape_xml(text: str) -> str:
    return html_module.escape(str(text or ""), quote=True)


def append_vertex(xml: list[str], node, layout: dict, parent_cell: str, accent: str) -> None:
    s = layout_settings()
    node_layout = layout.get(node.id, {"w": s.leaf_w, "h": s.leaf_h, "x": 0, "y": 0})
    x = node_layout.get("x", 0)
    y = node_layout.get("y", 0)
    width = node_layout["w"]
    height = node_layout["h"]

    if is_band_container(node):
        band_key = node.id.replace("band_", "")
        empty = "(not yet in scope)" in (node.label or "")
        style = build_band_style(band_key, empty=empty)
    else:
        style = build_style(node.shape_type)
    if "fontFamily=" not in style:
        style += ";fontFamily=Helvetica,Arial,sans-serif;"

    connectable = ' connectable="0"' if node.is_container else ""
    xml.append(
        f'    <mxCell id="{node.id}" value="{escape_xml(node.label)}" '
        f'style="{style}" vertex="1"{connectable} parent="{parent_cell}">'
    )
    xml.append(f'      <mxGeometry x="{x}" y="{y}" width="{width}" height="{height}" as="geometry" />')
    xml.append("    </mxCell>")

    step_number = getattr(node, "step_number", None)
    if not node.is_container and step_number is not None:
        xml.append(
            f'    <mxCell id="badge_{node.id}" value="{step_number}" '
            f'style="ellipse;whiteSpace=wrap;html=1;fillColor={accent};strokeColor=#ffffff;'
            f'fontColor=#ffffff;fontSize=11;fontStyle=1;align=center;verticalAlign=middle;shadow=1;" '
            f'vertex="1" parent="{parent_cell}">'
        )
        xml.append(
            f'      <mxGeometry x="{x + s.badge_offset}" y="{y + s.badge_offset}" '
            f'width="{s.badge_size}" height="{s.badge_size}" as="geometry" />'
        )
        xml.append("    </mxCell>")


def append_edge(
    xml: list[str],
    edge,
    *,
    cell_id: int,
    layout: dict,
    nodes_by_id: dict,
    default_edge_color: str,
    platform_key: str,
    exit_slot: int = 0,
    entry_slot: int = 0,
) -> None:
    src_box = absolute_box(edge.source_id, layout, nodes_by_id)
    tgt_box = absolute_box(edge.target_id, layout, nodes_by_id)
    exit_x, exit_y, entry_x, entry_y, _ = route_edge(
        src_box,
        tgt_box,
        exit_slot=exit_slot,
        entry_slot=entry_slot,
    )

    s = layout_settings()
    color = edge.color
    if color == s.edge_color_aws and platform_key != "aws":
        color = default_edge_color

    label = (edge.label or "").strip()
    is_attached = edge.style == "dashed" or label.lower() in ATTACHED_LABELS
    style_parts = [
        "edgeStyle=orthogonalEdgeStyle",
        "rounded=0",
        "html=1",
        "endArrow=none" if is_attached else "endArrow=classic",
        "startArrow=none",
        f"exitX={exit_x}",
        f"exitY={exit_y}",
        f"entryX={entry_x}",
        f"entryY={entry_y}",
        "jumpStyle=arc",
        "jumpSize=6",
        f"strokeColor={color}",
        f"fontColor={color}",
    ]
    if edge.style == "dashed" or is_attached:
        style_parts.append("dashed=1")
    if edge.style == "bold":
        style_parts.append("strokeWidth=3")
    if label:
        style_parts.append("labelBackgroundColor=#ffffff")

    edge_style = ";".join(style_parts) + ";"
    parent = lowest_common_ancestor_parent(edge.source_id, edge.target_id, nodes_by_id)
    xml.append(
        f'    <mxCell id="edge_{cell_id}" value="{escape_xml(label)}" style="{edge_style}" edge="1" '
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
    """Append the flow-notes panel and return ``(page_width, page_height)``."""
    s = layout_settings()
    notes_x = s.flow_notes_x if notes_x is None else notes_x
    notes_y = notes_y if notes_y is not None else 680
    notes_width = notes_width if notes_width is not None else s.flow_notes_max_w
    notes_height = 52 + len(flow_notes) * s.flow_notes_row_h
    heading = f"{html_module.escape(title)} \u2014 Flow Notes" if title else "Flow Notes"

    rows = [
        '<div style="font-family:Helvetica,Arial,sans-serif;color:#1A2233;text-align:left;padding:10px 14px;">'
        f'<div style="font-size:14px;font-weight:700;margin-bottom:8px;color:{accent};">{heading}</div>'
    ]
    for number, note_title, summary in flow_notes:
        safe_title = html_module.escape(note_title)
        safe_summary = html_module.escape(summary)
        title_html = f"<b>{safe_title}</b>" if safe_title else ""
        separator = " \u2014 " if safe_title and safe_summary else ""
        rows.append(
            '<div style="font-size:12px;line-height:1.45;margin-bottom:5px;">'
            f'<span style="display:inline-block;min-width:20px;font-weight:700;color:{accent};">'
            f"{number}.</span> {title_html}{separator}{safe_summary}</div>"
        )
    rows.append("</div>")

    xml.append(
        f'    <mxCell id="flow_notes" value="{escape_xml("".join(rows))}" '
        f'style="rounded=1;arcSize=4;whiteSpace=wrap;html=1;fillColor=#FBFCFE;'
        f'strokeColor={accent};strokeWidth=1.5;align=left;verticalAlign=top;shadow=1;spacing=2;" '
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
