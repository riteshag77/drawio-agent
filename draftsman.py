import html as html_module
import re
import uuid
from typing import TYPE_CHECKING

from style_builder import build_style

if TYPE_CHECKING:
    from pipeline_log import StepLogger


def _wrap_mxfile(graph_xml: str, page_name: str = "Architecture") -> str:
    """Wrap bare mxGraphModel in mxfile — required for reliable draw.io embed loading."""
    trimmed = graph_xml.strip()
    if "<mxfile" in trimmed:
        return trimmed
    page_id = uuid.uuid4().hex[:12]
    return (
        f'<mxfile host="app.diagrams.net" agent="drawio-agent" version="24.7.7">\n'
        f'  <diagram id="{page_id}" name="{_escape_xml(page_name)}">\n'
        f"    {trimmed}\n"
        f"  </diagram>\n"
        f"</mxfile>"
    )


def _escape_xml(text: str) -> str:
    if not text:
        return ""
    return html_module.escape(str(text), quote=True)


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


def _get_abs_bounds(nid: str, nodes_by_id: dict, layout: dict) -> tuple[int, int, int, int]:
    node = nodes_by_id[nid]
    ld = layout.get(nid, {})
    w, h = ld.get("w", 48), ld.get("h", 48)
    if node.parent_id and node.parent_id in nodes_by_id:
        px, py, _, _ = _get_abs_bounds(node.parent_id, nodes_by_id, layout)
        cx, cy = ld.get("children_pos", {}).get(nid, (0, 0))
        return px + cx, py + cy, w, h
    return ld.get("global_x", 0), ld.get("global_y", 0), w, h


def _anchor_points(src_box: tuple, tgt_box: tuple) -> tuple[float, float, float, float]:
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    scx, scy = sx + sw / 2, sy + sh / 2
    tcx, tcy = tx + tw / 2, ty + th / 2
    dx, dy = tcx - scx, tcy - scy
    if abs(dx) >= abs(dy):
        if dx >= 0:
            return 1, 0.5, 0, 0.5
        return 0, 0.5, 1, 0.5
    if dy >= 0:
        return 0.5, 1, 0.5, 0
    return 0.5, 0, 0.5, 1


def _edge_corridor_waypoints(
    src_box: tuple,
    tgt_box: tuple,
    exit_x: float,
    exit_y: float,
    entry_x: float,
    entry_y: float,
) -> list[tuple[int, int]]:
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    src_px = int(sx + sw * exit_x)
    src_py = int(sy + sh * exit_y)
    tgt_px = int(tx + tw * entry_x)
    tgt_py = int(ty + th * entry_y)

    gap = 50
    overlap_x = sx < tx + tw and tx < sx + sw
    overlap_y = sy < ty + th and ty < sy + sh

    if overlap_x and overlap_y:
        corridor_x = int(min(sx, tx) - gap) if exit_x <= 0.5 else int(max(sx + sw, tx + tw) + gap)
        return [(corridor_x, src_py), (corridor_x, tgt_py)]

    if abs(src_px - tgt_px) < 40 and abs(src_py - tgt_py) > 50:
        mid_y = int((src_py + tgt_py) / 2)
        return [(src_px, mid_y), (tgt_px, mid_y)]

    if abs(src_py - tgt_py) < 40 and abs(src_px - tgt_px) > 50:
        mid_x = int((src_px + tgt_px) / 2)
        return [(mid_x, src_py), (mid_x, tgt_py)]

    if abs(src_px - tgt_px) > 80 and abs(src_py - tgt_py) > 80:
        mid_x = int((src_px + tgt_px) / 2)
        return [(mid_x, src_py), (mid_x, tgt_py)]

    return []


def _edge_sort_key(edge, nodes_by_id: dict) -> tuple:
    src = nodes_by_id.get(edge.source_id)
    tgt = nodes_by_id.get(edge.target_id)
    src_step = src.step_number if src and src.step_number else 999
    tgt_step = tgt.step_number if tgt and tgt.step_number else 999
    is_adjacent_flow = src_step < 900 and tgt_step < 900 and abs(src_step - tgt_step) <= 1
    return (0 if is_adjacent_flow else 1, 1 if edge.style == "dashed" else 0, src_step, tgt_step)


def generate_xml(architecture, platform: str = "aws", logger: "StepLogger | None" = None) -> str:
    platform = (platform or "aws").lower()
    if platform == "gcp":
        default_edge_color = "#4285F4"
        badge_color = "#EA4335"
        platform_accent = "#4285F4"
        platform_label = "Google Cloud Platform"
    elif platform == "azure":
        default_edge_color = "#0078D4"
        badge_color = "#0078D4"
        platform_accent = "#0078D4"
        platform_label = "Microsoft Azure"
    else:
        default_edge_color = "#ED7100"
        badge_color = "#ED7100"
        platform_accent = "#ED7100"
        platform_label = "Amazon Web Services"

    LEAF_W = 64
    LEAF_H = 64
    LEAF_CELL_W = 150
    LEAF_CELL_H = 120
    CONTAINER_PAD_TOP = 56
    CONTAINER_PAD = 48
    CONTAINER_GAP = 48
    ROOT_GAP = 120
    MAX_COLS = 4
    ROOT_TOP = 100
    STEP_BADGE_R = 14

    nodes_by_id = {n.id: n for n in architecture.nodes}
    children_map: dict[str, list[str]] = {}
    roots: list[str] = []

    for node in architecture.nodes:
        if node.parent_id and node.parent_id in nodes_by_id:
            children_map.setdefault(node.parent_id, []).append(node.id)
        else:
            roots.append(node.id)

    layout: dict[str, dict] = {}

    def calc_size(nid: str) -> tuple[int, int]:
        node = nodes_by_id[nid]
        kids = children_map.get(nid, [])

        if not node.is_container or not kids:
            w = node.width or LEAF_W
            h = node.height or LEAF_H
            layout[nid] = {"w": w, "h": h, "children_pos": {}}
            return w, h

        kids = sorted(kids, key=lambda k: _flow_sort_key(k, nodes_by_id))
        sub_containers = sorted(
            [k for k in kids if nodes_by_id[k].is_container],
            key=lambda k: _flow_sort_key(k, nodes_by_id),
        )
        leaf_kids = sorted(
            [k for k in kids if not nodes_by_id[k].is_container],
            key=lambda k: _flow_sort_key(k, nodes_by_id),
        )

        child_sizes = {}
        for kid in kids:
            child_sizes[kid] = calc_size(kid)

        children_pos: dict[str, tuple[int, int]] = {}

        cx = CONTAINER_PAD
        cy = CONTAINER_PAD_TOP + 10
        row_max_h = 0
        parent_shape = node.shape_type.lower()
        network_parent = any(
            token in parent_shape
            for token in ("vpc", "vnet", "subnet", "region", "project", "resource_group")
        )
        # Subnets / AZs inside a network boundary flow left → right in one row.
        container_cols = len(sub_containers) if network_parent and sub_containers else max(2, MAX_COLS - 1)

        for i, kid in enumerate(sub_containers):
            kw, kh = child_sizes[kid]
            if i > 0 and i % container_cols == 0:
                cx = CONTAINER_PAD
                cy += row_max_h + CONTAINER_GAP
                row_max_h = 0
            children_pos[kid] = (cx, cy)
            cx += kw + CONTAINER_GAP
            row_max_h = max(row_max_h, kh)

        containers_bottom = cy + row_max_h + CONTAINER_GAP if sub_containers else CONTAINER_PAD_TOP + 10
        containers_right = cx if sub_containers else CONTAINER_PAD

        lx = CONTAINER_PAD
        ly = containers_bottom
        leaf_row_max_h = 0

        for i, kid in enumerate(leaf_kids):
            kw, kh = child_sizes[kid]
            cell_w = max(LEAF_CELL_W, kw + 10)
            cell_h = max(LEAF_CELL_H, kh + 10)

            if i > 0 and i % MAX_COLS == 0:
                lx = CONTAINER_PAD
                ly += leaf_row_max_h + 20
                leaf_row_max_h = 0

            offset_x = (cell_w - kw) // 2
            offset_y = (cell_h - kh) // 2
            children_pos[kid] = (lx + offset_x, ly + offset_y)
            lx += cell_w
            leaf_row_max_h = max(leaf_row_max_h, cell_h)

        leaf_right = lx if leaf_kids else CONTAINER_PAD
        leaf_bottom = (ly + leaf_row_max_h + CONTAINER_PAD) if leaf_kids else containers_bottom

        total_w = max(containers_right, leaf_right) + CONTAINER_PAD
        total_h = max(leaf_bottom, containers_bottom) + CONTAINER_PAD

        if node.width:
            total_w = max(total_w, node.width)
        if node.height:
            total_h = max(total_h, node.height)

        total_w = max(total_w, 220)
        total_h = max(total_h, 140)

        layout[nid] = {"w": total_w, "h": total_h, "children_pos": children_pos}
        return total_w, total_h

    for rid in roots:
        calc_size(rid)

    root_containers = [r for r in roots if nodes_by_id[r].is_container]
    root_leaves = [r for r in roots if not nodes_by_id[r].is_container]

    main_containers = []
    group_containers = []

    for rid in root_containers:
        node = nodes_by_id[rid]
        label = node.label.lower()
        if any(w in label for w in ["monitoring", "logging", "security", "compliance", "operations", "ops", "database", "storage"]):
            group_containers.append(rid)
        else:
            main_containers.append(rid)

    if not main_containers and group_containers:
        main_containers = group_containers
        group_containers = []

    left_leaves = []
    right_leaves = []
    bottom_leaves = []

    for rid in root_leaves:
        node = nodes_by_id[rid]
        label = node.label.lower()
        shape = node.shape_type.lower()

        if any(w in label for w in ["payment", "processor", "third party", "external", "saas", "api key", "merchant"]):
            right_leaves.append(rid)
        elif any(w in label for w in ["dynamodb", "s3", "database", "bucket", "storage", "blob", "gcs", "sql", "spanner", "cosmos"]) or any(s in shape for s in ["dynamodb", "s3", "blob", "gcs", "sql", "spanner", "cosmos"]):
            bottom_leaves.append(rid)
        else:
            left_leaves.append(rid)

    left_leaves = sorted(left_leaves, key=lambda k: _flow_sort_key(k, nodes_by_id))
    main_containers = sorted(main_containers, key=lambda k: _flow_sort_key(k, nodes_by_id))

    lx = 40
    ly = ROOT_TOP
    for i, rid in enumerate(left_leaves):
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y or ly
            continue
        if i > 0 and i % 4 == 0:
            lx += LEAF_CELL_W + 20
            ly = ROOT_TOP
        layout[rid]["global_x"] = lx
        layout[rid]["global_y"] = ly
        ly += LEAF_CELL_H + 36

    left_max_x = 40
    if left_leaves:
        left_max_x = max(layout[rid].get("global_x", 0) + layout[rid]["w"] for rid in left_leaves) + 100

    cx = max(left_max_x, 360)
    cy = ROOT_TOP
    for rid in main_containers:
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y or cy
            cx = node.x + layout[rid]["w"] + ROOT_GAP
            continue
        layout[rid]["global_x"] = cx
        layout[rid]["global_y"] = cy
        cx += layout[rid]["w"] + ROOT_GAP

    rx = cx + 60
    ry = ROOT_TOP
    for i, rid in enumerate(right_leaves):
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y or ry
            continue
        if i > 0 and i % 4 == 0:
            rx += LEAF_CELL_W + 20
            ry = ROOT_TOP
        layout[rid]["global_x"] = rx
        layout[rid]["global_y"] = ry
        ry += LEAF_CELL_H + 30

    max_main_h = max([layout[rid]["h"] for rid in main_containers]) if main_containers else 0
    group_y = ROOT_TOP + max_main_h + 160

    blx = max(left_max_x, 360)
    for rid in bottom_leaves:
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y or group_y
            blx = node.x + LEAF_CELL_W + 50
            continue
        layout[rid]["global_x"] = blx
        layout[rid]["global_y"] = group_y
        blx += LEAF_CELL_W + 50

    gcx = blx + 40 if bottom_leaves else 400
    for rid in group_containers:
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y or group_y
            gcx = node.x + layout[rid]["w"] + ROOT_GAP
            continue
        layout[rid]["global_x"] = gcx
        layout[rid]["global_y"] = group_y
        gcx += layout[rid]["w"] + ROOT_GAP

    # ── Calculate total canvas extent ──
    all_bounds = []
    for node in architecture.nodes:
        nid = node.id
        ld = layout.get(nid, {"w": LEAF_W, "h": LEAF_H, "children_pos": {}})
        if node.parent_id and node.parent_id in nodes_by_id:
            px, py, _, _ = _get_abs_bounds(node.parent_id, nodes_by_id, layout)
            cx_, cy_ = ld.get("children_pos", {}).get(nid, (0, 0))
            nx, ny = px + cx_, py + cy_
        else:
            nx, ny = ld.get("global_x", 0), ld.get("global_y", 0)
        nw, nh = ld["w"], ld["h"]
        all_bounds.append((nx, ny, nx + nw + 40, ny + nh + 40))

    canvas_w = max((b[2] for b in all_bounds), default=3200) + 80
    canvas_h = max((b[3] for b in all_bounds), default=2200) + 80
    canvas_w = max(canvas_w, 1200)
    canvas_h = max(canvas_h, 800)

    # ── Build XML ──
    xml = [
        f'<mxGraphModel dx="0" dy="0" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{canvas_w}" pageHeight="{canvas_h}" math="0" shadow="1">',
        '  <root>',
        '    <mxCell id="0" />',
        '    <mxCell id="1" parent="0" />',
    ]

    # ── Canvas background ──
    bg_style = (
        f"rounded=0;whiteSpace=wrap;html=1;fillColor=#FAFBFC;"
        f"strokeColor=#E1E5EA;strokeWidth=1;"
        f"gradientColor=none;pointerEvents=0;"
    )
    xml.append(
        f'    <mxCell id="bg" value="" style="{bg_style}" vertex="1" parent="1">'
    )
    xml.append(
        f'      <mxGeometry x="-10" y="-10" width="{canvas_w + 20}" height="{canvas_h + 20}" as="geometry" />'
    )
    xml.append('    </mxCell>')

    # ── Platform accent border ──
    border_style = (
        f"rounded=1;whiteSpace=wrap;html=1;fillColor=none;"
        f"strokeColor={platform_accent};strokeWidth=3;dashed=0;"
        f"pointerEvents=0;"
    )
    xml.append(
        f'    <mxCell id="accent_border" value="" style="{border_style}" vertex="1" parent="1">'
    )
    xml.append(
        f'      <mxGeometry x="10" y="10" width="{canvas_w - 20}" height="{canvas_h - 20}" as="geometry" />'
    )
    xml.append('    </mxCell>')

    # ── Diagram title ──
    title_text = _escape_xml(f"{platform_label} Architecture Diagram")
    title_style = (
        f"text;html=1;strokeColor=none;fillColor=none;"
        f"align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;"
        f"fontSize=20;fontStyle=1;fontColor=#232F3E;"
        f"spacingLeft=10;"
    )
    xml.append(
        f'    <mxCell id="title" value="{title_text}" style="{title_style}" vertex="1" parent="1">'
    )
    xml.append(
        f'      <mxGeometry x="30" y="20" width="{canvas_w - 60}" height="36" as="geometry" />'
    )
    xml.append('    </mxCell>')

    # ── Platform accent underline for title ──
    underline_style = (
        f"line;html=1;strokeColor={platform_accent};strokeWidth=3;"
        f"rounded=0;pointerEvents=0;"
    )
    xml.append(
        f'    <mxCell id="title_underline" value="" style="{underline_style}" vertex="1" parent="1">'
    )
    xml.append(
        f'      <mxGeometry x="30" y="56" width="200" height="8" as="geometry" />'
    )
    xml.append('    </mxCell>')

    cell_id = 100

    for node in architecture.nodes:
        nid = node.id
        ld = layout.get(nid, {"w": LEAF_W, "h": LEAF_H, "children_pos": {}})
        w, h = ld["w"], ld["h"]

        if node.parent_id and node.parent_id in nodes_by_id:
            parent_cell = node.parent_id
            parent_ld = layout.get(node.parent_id, {})
            x, y = parent_ld.get("children_pos", {}).get(nid, (0, 0))
        else:
            parent_cell = "1"
            x = ld.get("global_x", 0)
            y = ld.get("global_y", 0)

        style = build_style(node.shape_type)

        if "fontFamily=" not in style:
            style += ";fontFamily=Inter,SF Pro Text,Segoe UI,Helvetica,Arial,sans-serif;"
        if not node.is_container and "perimeter=" not in style:
            style += ";perimeter=orthogonalPerimeter;"

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

        if node.step_number is not None:
            badge_id = f"badge_{nid}_{cell_id}"
            cell_id += 1
            badge_style = (
                "ellipse;whiteSpace=wrap;html=1;aspect=fixed;"
                f"fillColor={badge_color};fontColor=#FFFFFF;fontStyle=1;"
                "fontSize=11;strokeColor=none;shadow=1;"
            )
            bx = x - 8
            by = y - 8
            bd = STEP_BADGE_R * 2
            xml.append(
                f'    <mxCell id="{badge_id}" value="{node.step_number}" '
                f'style="{badge_style}" vertex="1" parent="{parent_cell}">'
            )
            xml.append(
                f'      <mxGeometry x="{bx}" y="{by}" width="{bd}" height="{bd}" as="geometry" />'
            )
            xml.append('    </mxCell>')

    sorted_edges = sorted(architecture.edges, key=lambda e: _edge_sort_key(e, nodes_by_id))

    for edge in sorted_edges:
        if edge.source_id not in nodes_by_id or edge.target_id not in nodes_by_id:
            continue

        cell_id += 1
        eid = f"edge_{cell_id}"

        src_box = _get_abs_bounds(edge.source_id, nodes_by_id, layout)
        tgt_box = _get_abs_bounds(edge.target_id, nodes_by_id, layout)
        exit_x, exit_y, entry_x, entry_y = _anchor_points(src_box, tgt_box)
        waypoints = _edge_corridor_waypoints(src_box, tgt_box, exit_x, exit_y, entry_x, entry_y)

        edge_style_parts = [
            "edgeStyle=orthogonalEdgeStyle",
            "rounded=1",
            "orthogonalLoop=1",
            "jettySize=auto",
            "html=1",
            "strokeWidth=2",
            "fontSize=11",
            "fontFamily=Inter,SF Pro Text,Segoe UI,Helvetica,Arial,sans-serif",
            "endArrow=classicThin",
            "startArrow=none",
            "endFill=1",
            "jumpStyle=arc",
            "jumpSize=8",
            f"exitX={exit_x}",
            f"exitY={exit_y}",
            "exitDx=0",
            "exitDy=0",
            f"entryX={entry_x}",
            f"entryY={entry_y}",
            "entryDx=0",
            "entryDy=0",
        ]

        color = edge.color
        if color == "#ED7100" and platform != "aws":
            color = default_edge_color

        edge_style_parts.append(f"strokeColor={color}")
        edge_style_parts.append(f"fontColor={color}")
        edge_style_parts.append(f"labelBackgroundColor=#ffffff")

        if edge.style == "dashed":
            edge_style_parts.append("dashed=1")
            edge_style_parts.append("dashPattern=8 4")
        elif edge.style == "bold":
            edge_style_parts.append("strokeWidth=3")

        edge_style = ";".join(edge_style_parts) + ";"
        label = _escape_xml(edge.label or "")

        xml.append(
            f'    <mxCell id="{eid}" value="{label}" '
            f'style="{edge_style}" edge="1" parent="1" '
            f'source="{edge.source_id}" target="{edge.target_id}">'
        )
        if waypoints:
            xml.append('      <mxGeometry relative="1" as="geometry">')
            xml.append('        <Array as="points">')
            for wx, wy in waypoints:
                xml.append(f'          <mxPoint x="{wx}" y="{wy}" />')
            xml.append('        </Array>')
            xml.append('      </mxGeometry>')
        else:
            xml.append('      <mxGeometry relative="1" as="geometry" />')
        xml.append('    </mxCell>')

    xml.extend(['  </root>', '</mxGraphModel>'])
    graph_xml = "\n".join(xml)
    wrapped = _wrap_mxfile(graph_xml, f"{platform_label} Architecture")

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
                **canvas,
            },
        )

    return wrapped
