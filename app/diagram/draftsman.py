import html as html_module
import re
from typing import TYPE_CHECKING

from app.diagram.styles import build_style
from app.diagram.xml_tools import validate_and_fix_xml_legacy, wrap_with_mxfile
from app.models import ArchitectureDiagram
from app.patterns.template_merge import apply_three_tier_scaffold

if TYPE_CHECKING:
    from app.core.logging import StepLogger


LEAF_W = 60
LEAF_H = 60
LEAF_CELL_W = 148
LEAF_CELL_H = 100
LEAF_GAP = 24
CONTAINER_PAD = 24
CONTAINER_PAD_TOP = 44
CONTAINER_GAP = 40
ROOT_TOP = 40
MAX_COLS = 4
DEFAULT_PAGE_W = 1169
DEFAULT_PAGE_H = 827
DEFAULT_CONTAINER_W = 320
DEFAULT_CONTAINER_H = 240

# ponytail: x/y are sibling-index placeholders only; embed horizontalFlow owns final layout


def _escape_xml(text: str) -> str:
    if not text:
        return ""
    return html_module.escape(str(text), quote=True)


def _edge_label(label: str | None) -> str:
    return (label or "").strip()


_PLATFORM_ACCENTS = {
    "aws": "#ED7100",
    "gcp": "#4285F4",
    "azure": "#0078D4",
    "multi": "#8C4FFF",
}


def _platform_accent(platform: str) -> str:
    return _PLATFORM_ACCENTS.get((platform or "aws").lower(), "#ED7100")


def _build_flow_notes(architecture) -> list[tuple[int, str, str]]:
    steps = getattr(architecture, "flow_steps", None) or []
    if steps:
        ordered = sorted(steps, key=lambda s: s.number)
        return [(s.number, (s.title or "").strip(), (s.summary or "").strip()) for s in ordered]
    numbered = [n for n in architecture.nodes if getattr(n, "step_number", None) is not None]
    numbered.sort(key=lambda n: n.step_number)
    notes: list[tuple[int, str, str]] = []
    for node in numbered:
        title = (node.label or "").strip()
        summary = (node.description or "").strip()
        notes.append((node.step_number, title, summary))
    return notes


def _ancestor_chain(nid: str, nodes_by_id: dict) -> list[str]:
    chain: list[str] = []
    current = nodes_by_id.get(nid)
    while current and current.parent_id and current.parent_id in nodes_by_id:
        chain.append(current.parent_id)
        current = nodes_by_id[current.parent_id]
    return chain


def _lca_parent(source_id: str, target_id: str, nodes_by_id: dict) -> str:
    source_anc = _ancestor_chain(source_id, nodes_by_id)
    target_set = set(_ancestor_chain(target_id, nodes_by_id))
    for anc in source_anc:
        if anc in target_set:
            return anc
    return "1"


def _subnet_tier(nid: str, nodes_by_id: dict) -> int:
    node = nodes_by_id[nid]
    label = node.label.lower()
    shape = node.shape_type.lower()
    combined = f"{label} {shape}"
    if any(w in combined for w in ["public", "ingress", "frontend", "dmz", "edge"]):
        return 0
    if any(w in combined for w in ["data", "database", "db", "storage", "persistence"]):
        return 2
    if any(w in combined for w in ["private", "compute", "app", "backend", "worker"]):
        return 1
    return 1


def _flow_sort_key(nid: str, nodes_by_id: dict) -> tuple:
    node = nodes_by_id[nid]
    label = node.label.lower()
    shape = node.shape_type.lower()
    if node.is_container:
        tier = _subnet_tier(nid, nodes_by_id)
    elif "public" in label or "public" in shape:
        tier = 0
    elif "private" in label or "private" in shape:
        tier = 1
    elif any(w in label for w in ["monitoring", "logging", "security", "ops", "compliance"]):
        tier = 3
    else:
        tier = 2
    step = node.step_number if node.step_number is not None else 500
    return (tier, step, nid)


def _default_node_size(node) -> tuple[int, int]:
    if node.is_container:
        return node.width or DEFAULT_CONTAINER_W, node.height or DEFAULT_CONTAINER_H
    return node.width or LEAF_W, node.height or LEAF_H


def _expand_container_to_children(
    nid: str,
    layout: dict[str, dict],
    children_map: dict[str, list[str]],
    nodes_by_id: dict,
) -> None:
    kids = children_map.get(nid, [])
    if not kids or nid not in layout:
        return
    max_right = CONTAINER_PAD
    max_bottom = CONTAINER_PAD_TOP
    for kid in kids:
        ld = layout.get(kid, {})
        max_right = max(max_right, ld.get("x", 0) + ld.get("w", LEAF_W) + CONTAINER_PAD)
        max_bottom = max(max_bottom, ld.get("y", 0) + ld.get("h", LEAF_H) + CONTAINER_PAD)
    node = nodes_by_id[nid]
    layout[nid]["w"] = max(layout[nid].get("w", 0), max_right, node.width or 0)
    layout[nid]["h"] = max(layout[nid].get("h", 0), max_bottom, node.height or 0)


def _layout_children_in_parent(
    parent_id: str | None,
    child_ids: list[str],
    nodes_by_id: dict,
    children_map: dict[str, list[str]],
    layout: dict[str, dict],
) -> None:
    """Place siblings in a simple grid relative to their parent; ELK reflows on embed load."""
    sorted_ids = sorted(child_ids, key=lambda k: _flow_sort_key(k, nodes_by_id))
    x = CONTAINER_PAD if parent_id else 40
    y = CONTAINER_PAD_TOP if parent_id else ROOT_TOP
    row_h = 0
    col = 0

    for cid in sorted_ids:
        node = nodes_by_id[cid]
        w, h = _default_node_size(node)
        if col > 0 and col % MAX_COLS == 0:
            x = CONTAINER_PAD if parent_id else 40
            y += row_h + LEAF_GAP
            row_h = 0
            col = 0

        layout[cid] = {"x": x, "y": y, "w": w, "h": h}
        if node.is_container:
            sub = children_map.get(cid, [])
            if sub:
                _layout_children_in_parent(cid, sub, nodes_by_id, children_map, layout)
                _expand_container_to_children(cid, layout, children_map, nodes_by_id)
                w, h = layout[cid]["w"], layout[cid]["h"]

        step_x = (w + CONTAINER_GAP) if node.is_container else (LEAF_CELL_W + LEAF_GAP)
        x += step_x
        row_h = max(row_h, h)
        col += 1

    if parent_id:
        _expand_container_to_children(parent_id, layout, children_map, nodes_by_id)


def _build_topology_layout(
    nodes_by_id: dict,
    children_map: dict[str, list[str]],
    roots: list[str],
) -> dict[str, dict]:
    layout: dict[str, dict] = {}
    _layout_children_in_parent(None, roots, nodes_by_id, children_map, layout)
    for nid in nodes_by_id:
        if nid not in layout:
            node = nodes_by_id[nid]
            w, h = _default_node_size(node)
            layout[nid] = {"x": 0, "y": 0, "w": w, "h": h}
    return layout


def _edge_sort_key(edge, nodes_by_id: dict) -> tuple:
    src = nodes_by_id.get(edge.source_id)
    tgt = nodes_by_id.get(edge.target_id)
    src_step = src.step_number if src and src.step_number else 999
    tgt_step = tgt.step_number if tgt and tgt.step_number else 999
    is_adjacent_flow = src_step < 900 and tgt_step < 900 and abs(src_step - tgt_step) <= 1
    return (0 if is_adjacent_flow else 1, 1 if edge.style == "dashed" else 0, src_step, tgt_step)


def sanitize_layout_hints(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    cleaned = [
        node.model_copy(update={"x": None, "y": None, "width": None, "height": None})
        for node in architecture.nodes
    ]
    return architecture.model_copy(update={"nodes": cleaned})


def generate_xml(architecture, platform: str = "aws", logger: "StepLogger | None" = None) -> str:
    if isinstance(architecture, ArchitectureDiagram):
        architecture = sanitize_layout_hints(architecture)
        architecture = apply_three_tier_scaffold(architecture)
        if logger and architecture.pattern_id == "three_tier_web":
            logger.log("template", "Applied three_tier_web container scaffold", level="info")

    platform = (platform or "aws").lower()
    if platform == "gcp":
        default_edge_color = "#4285F4"
        platform_label = "Google Cloud Platform"
    elif platform == "azure":
        default_edge_color = "#0078D4"
        platform_label = "Microsoft Azure"
    else:
        default_edge_color = "#ED7100"
        platform_label = "Amazon Web Services"

    nodes_by_id = {n.id: n for n in architecture.nodes}
    children_map: dict[str, list[str]] = {}
    roots: list[str] = []

    for node in architecture.nodes:
        if node.parent_id and node.parent_id in nodes_by_id:
            children_map.setdefault(node.parent_id, []).append(node.id)
        else:
            roots.append(node.id)

    layout = _build_topology_layout(nodes_by_id, children_map, roots)

    canvas_w = DEFAULT_PAGE_W
    canvas_h = DEFAULT_PAGE_H

    flow_notes = _build_flow_notes(architecture)
    accent = _platform_accent(platform)
    notes_geom = None
    if flow_notes:
        notes_x = 40
        notes_w = 760
        notes_y = 680
        notes_h = 52 + len(flow_notes) * 30
        notes_geom = (notes_x, notes_y, notes_w, notes_h)
        canvas_h = max(canvas_h, notes_y + notes_h + 40)

    xml = [
        f'<mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{canvas_w}" pageHeight="{canvas_h}" math="0" shadow="0">',
        '  <root>',
        '    <mxCell id="0" />',
        '    <mxCell id="1" parent="0" />',
    ]

    for node in architecture.nodes:
        nid = node.id
        ld = layout.get(nid, {"w": LEAF_W, "h": LEAF_H, "x": 0, "y": 0})
        w, h = ld["w"], ld["h"]
        x, y = ld.get("x", 0), ld.get("y", 0)
        parent_cell = node.parent_id if node.parent_id and node.parent_id in nodes_by_id else "1"

        style = build_style(node.shape_type)

        if "fontFamily=" not in style:
            style += ";fontFamily=Helvetica,Arial,sans-serif;"

        label = _escape_xml(node.label)

        if node.is_container:
            xml.append(
                f'    <mxCell id="{nid}" value="{label}" '
                f'style="{style}" vertex="1" connectable="0" parent="{parent_cell}">'
            )
            xml.append(
                f'      <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />'
            )
            xml.append('    </mxCell>')
        else:
            xml.append(
                f'    <mxCell id="{nid}" value="{label}" '
                f'style="{style}" vertex="1" parent="{parent_cell}">'
            )
            xml.append(
                f'      <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry" />'
            )
            xml.append('    </mxCell>')

            step = getattr(node, "step_number", None)
            if step is not None:
                badge_x = x - 6
                badge_y = y - 6
                xml.append(
                    f'    <mxCell id="badge_{nid}" value="{step}" '
                    f'style="ellipse;whiteSpace=wrap;html=1;fillColor={accent};strokeColor=#ffffff;'
                    f'fontColor=#ffffff;fontSize=11;fontStyle=1;align=center;verticalAlign=middle;'
                    f'shadow=1;" vertex="1" parent="{parent_cell}">'
                )
                xml.append(
                    f'      <mxGeometry x="{badge_x}" y="{badge_y}" width="22" height="22" as="geometry" />'
                )
                xml.append('    </mxCell>')

    sorted_edges = sorted(architecture.edges, key=lambda e: _edge_sort_key(e, nodes_by_id))

    cell_id = 100
    for edge in sorted_edges:
        if edge.source_id not in nodes_by_id or edge.target_id not in nodes_by_id:
            continue

        cell_id += 1
        eid = f"edge_{cell_id}"

        edge_parent = _lca_parent(edge.source_id, edge.target_id, nodes_by_id)

        edge_style_parts = [
            "edgeStyle=orthogonalEdgeStyle",
            "rounded=0",
            "html=1",
            "endArrow=classic",
            "exitX=1",
            "exitY=0.5",
            "entryX=0",
            "entryY=0.5",
        ]

        color = edge.color
        if color == "#ED7100" and platform != "aws":
            color = default_edge_color
        edge_style_parts.append(f"strokeColor={color}")
        edge_style_parts.append(f"fontColor={color}")

        if edge.style == "dashed":
            edge_style_parts.append("dashed=1")
        elif edge.style == "bold":
            edge_style_parts.append("strokeWidth=3")

        label_text = _edge_label(edge.label)
        label = _escape_xml(label_text)
        if label_text:
            edge_style_parts.append("labelBackgroundColor=#ffffff")

        edge_style = ";".join(edge_style_parts) + ";"

        xml.append(
            f'    <mxCell id="{eid}" value="{label}" '
            f'style="{edge_style}" edge="1" parent="{edge_parent}" '
            f'source="{edge.source_id}" target="{edge.target_id}">'
        )
        xml.append('      <mxGeometry relative="1" as="geometry" />')
        xml.append('    </mxCell>')

    if notes_geom and flow_notes:
        nx, ny, nw, nh = notes_geom
        diagram_title = (getattr(architecture, "title", None) or "").strip()
        heading_text = html_module.escape(diagram_title) if diagram_title else ""
        heading = f"{heading_text} — Flow Notes" if heading_text else "Flow Notes"
        rows = [
            (
                '<div style="font-family:Helvetica,Arial,sans-serif;color:#1A2233;'
                'text-align:left;padding:10px 14px;">'
                f'<div style="font-size:14px;font-weight:700;margin-bottom:8px;'
                f'color:{accent};">{heading}</div>'
            )
        ]
        for number, title, summary in flow_notes:
            safe_title = html_module.escape(title)
            safe_summary = html_module.escape(summary)
            title_html = f"<b>{safe_title}</b>" if safe_title else ""
            sep = " — " if safe_title and safe_summary else ""
            rows.append(
                '<div style="font-size:12px;line-height:1.45;margin-bottom:5px;">'
                f'<span style="display:inline-block;min-width:20px;font-weight:700;'
                f'color:{accent};">{number}.</span> {title_html}{sep}{safe_summary}'
                "</div>"
            )
        rows.append("</div>")
        notes_value = html_module.escape("".join(rows), quote=True)

        xml.append(
            f'    <mxCell id="flow_notes" value="{notes_value}" '
            f'style="rounded=1;arcSize=4;whiteSpace=wrap;html=1;fillColor=#FBFCFE;'
            f'strokeColor={accent};strokeWidth=1.5;align=left;verticalAlign=top;'
            f'shadow=1;spacing=2;" vertex="1" parent="1">'
        )
        xml.append(
            f'      <mxGeometry x="{nx}" y="{ny}" width="{nw}" height="{nh}" as="geometry" />'
        )
        xml.append('    </mxCell>')

    xml.extend(['  </root>', '</mxGraphModel>'])
    graph_xml = "\n".join(xml)
    graph_xml, fixes = validate_and_fix_xml_legacy(graph_xml)
    wrapped = wrap_with_mxfile(graph_xml, f"{platform_label} Architecture")

    if logger:
        page_match = re.search(r'pageWidth="(\d+)"\s+pageHeight="(\d+)"', graph_xml)
        canvas = {}
        if page_match:
            canvas = {
                "page_width": int(page_match.group(1)),
                "page_height": int(page_match.group(2)),
            }
        logger.log(
            "xml",
            "Draw.io XML generated",
            level="success",
            details={
                "platform": platform,
                "mx_cells": graph_xml.count("<mxCell"),
                "edges": len(architecture.edges),
                "xml_chars": len(wrapped),
                "xml_fixes": fixes,
                **canvas,
            },
        )

    return wrapped
