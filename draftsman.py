import html as html_module
import re
from typing import TYPE_CHECKING

from style_builder import build_style
from edge_router import collect_obstacles, route_edge
from xml_utils import validate_and_fix_xml_legacy, wrap_with_mxfile

if TYPE_CHECKING:
    from pipeline_log import StepLogger


def _escape_xml(text: str) -> str:
    if not text:
        return ""
    return html_module.escape(str(text), quote=True)


def _normalize_edge_label(label: str | None) -> str:
    """Shorten edge labels to reduce overlap (protocol-only, max ~10 chars)."""
    if not label:
        return ""
    text = str(label).strip()
    if not text:
        return ""

    lower = text.lower()
    protocol_map = {
        "https": "HTTPS",
        "http/s": "HTTPS",
        "http": "HTTP",
        "sql": "SQL",
        "redis": "Redis",
        "grpc": "gRPC",
        "rest": "REST",
        "tcp": "TCP",
        "udp": "UDP",
        "amqp": "AMQP",
        "mqtt": "MQTT",
    }
    for key, short in protocol_map.items():
        if key in lower and len(text) > len(short):
            # Prefer single protocol token when label combines service + protocol
            if any(p in lower for p in ("https", "http", "sql", "redis", "grpc")):
                if "redis" in lower and "https" in lower:
                    return "Redis"
                if "sql" in lower and "https" in lower:
                    return "SQL"
                if "http" in lower:
                    return "HTTPS"
            return short

    if len(text) > 12:
        # "Logs/Metrics" → keep; "Get Secrets from KV" → truncate
        if "/" in text and len(text) <= 14:
            return text
        return text[:10].rstrip()
    return text


# Relative position along edge (0–1) and perpendicular pixel offset for label placement.
_LABEL_OFFSETS: list[tuple[float, int, str]] = [
    (0.22, -22, "top"),
    (0.42, 18, "bottom"),
    (0.55, -26, "top"),
    (0.68, 14, "bottom"),
    (0.32, 10, "bottom"),
    (0.58, -16, "top"),
    (0.75, 22, "bottom"),
    (0.48, -12, "top"),
]


def _edge_label_style_and_geometry(
    label: str,
    label_slot: int,
    *,
    has_waypoints: bool,
) -> tuple[list[str], list[str]]:
    """Return extra style parts and mxGeometry XML lines for non-overlapping edge labels."""
    if not label:
        return [], ['      <mxGeometry relative="1" as="geometry" />']

    rel_pos, perp_offset, v_pos = _LABEL_OFFSETS[label_slot % len(_LABEL_OFFSETS)]
    h_pos = "left" if label_slot % 3 == 0 else ("right" if label_slot % 3 == 1 else "center")

    style_parts = [
        f"verticalLabelPosition={v_pos}",
        f"horizontalLabelPosition={h_pos}",
        "labelBackgroundColor=#ffffff",
        "labelBorderColor=#ffffff",
        "spacingTop=2",
        "spacingBottom=2",
        "fontSize=10",
    ]

    if has_waypoints:
        geom = [
            '      <mxGeometry relative="1" as="geometry">',
            f'        <mxPoint x="{rel_pos}" y="{perp_offset}" as="offset" />',
            '      </mxGeometry>',
        ]
    else:
        geom = [
            f'      <mxGeometry x="{rel_pos}" y="{perp_offset}" relative="1" as="geometry" />',
        ]

    return style_parts, geom


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
        platform_label = "Google Cloud Platform"
    elif platform == "azure":
        default_edge_color = "#0078D4"
        platform_label = "Microsoft Azure"
    else:
        default_edge_color = "#ED7100"
        platform_label = "Amazon Web Services"

    LEAF_W = 60
    LEAF_H = 60
    LEAF_CELL_W = 168
    LEAF_CELL_H = 112
    CONTAINER_PAD_TOP = 48
    CONTAINER_PAD = 40
    CONTAINER_GAP = 36
    ROOT_GAP = 80
    MAX_COLS = 4
    ROOT_TOP = 40

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
            w = node.width if node.width is not None else LEAF_W
            h = node.height if node.height is not None else LEAF_H
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
            lx += cell_w + 12
            leaf_row_max_h = max(leaf_row_max_h, cell_h)

        leaf_right = lx if leaf_kids else CONTAINER_PAD
        leaf_bottom = (ly + leaf_row_max_h + CONTAINER_PAD) if leaf_kids else containers_bottom

        total_w = max(containers_right, leaf_right) + CONTAINER_PAD
        total_h = max(leaf_bottom, containers_bottom) + CONTAINER_PAD

        if node.width is not None:
            total_w = max(total_w, node.width)
        if node.height is not None:
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
            layout[rid]["global_y"] = node.y if node.y is not None else ly
            continue
        if i > 0 and i % 4 == 0:
            lx += LEAF_CELL_W + 20
            ly = ROOT_TOP
        layout[rid]["global_x"] = lx
        layout[rid]["global_y"] = ly
        ly += LEAF_CELL_H + 44

    left_max_x = 40
    if left_leaves:
        left_max_x = max(layout[rid].get("global_x", 0) + layout[rid]["w"] for rid in left_leaves) + 100

    cx = max(left_max_x, 360)
    cy = ROOT_TOP
    for rid in main_containers:
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y if node.y is not None else cy
            cx = node.x + layout[rid]["w"] + ROOT_GAP
            continue
        layout[rid]["global_x"] = cx
        layout[rid]["global_y"] = cy
        cx += layout[rid]["w"] + ROOT_GAP

    main_flow_y: int | None = None
    if main_containers:
        rid0 = main_containers[0]
        main_flow_y = layout[rid0]["global_y"] + layout[rid0]["h"] // 2
        for rid in left_leaves:
            if nodes_by_id[rid].x is None:
                layout[rid]["global_y"] = main_flow_y - layout[rid]["h"] // 2

    rx = cx + 60
    ry = ROOT_TOP
    for i, rid in enumerate(right_leaves):
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y if node.y is not None else ry
            continue
        if main_flow_y is not None:
            ry = main_flow_y - layout[rid]["h"] // 2
        if i > 0 and i % 4 == 0:
            rx += LEAF_CELL_W + 20
            ry = main_flow_y - layout[rid]["h"] // 2 if main_flow_y else ROOT_TOP
        layout[rid]["global_x"] = rx
        layout[rid]["global_y"] = ry
        if main_flow_y is None:
            ry += LEAF_CELL_H + 30

    max_main_h = max([layout[rid]["h"] for rid in main_containers]) if main_containers else 0
    group_y = ROOT_TOP + max_main_h + 200

    blx = max(left_max_x, 360)
    for rid in bottom_leaves:
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y if node.y is not None else group_y
            blx = node.x + LEAF_CELL_W + 50
            continue
        layout[rid]["global_x"] = blx
        layout[rid]["global_y"] = group_y
        blx += LEAF_CELL_W + 72

    gcx = blx + 40 if bottom_leaves else 400
    for rid in group_containers:
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y if node.y is not None else group_y
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

    canvas_w = max((b[2] for b in all_bounds), default=1169) + 60
    canvas_h = max((b[3] for b in all_bounds), default=827) + 60
    canvas_w = max(canvas_w, 900)
    canvas_h = max(canvas_h, 600)

    xml = [
        f'<mxGraphModel dx="1422" dy="794" grid="1" gridSize="10" guides="1" '
        f'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        f'pageWidth="{canvas_w}" pageHeight="{canvas_h}" math="0" shadow="0">',
        '  <root>',
        '    <mxCell id="0" />',
        '    <mxCell id="1" parent="0" />',
    ]

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
            style += ";fontFamily=Helvetica,Arial,sans-serif;"
        if not node.is_container and "perimeter=" not in style:
            style += ";perimeter=orthogonalPerimeter;"
        if not node.is_container:
            style += ";whiteSpace=wrap;html=1;align=center;verticalAlign=top;spacingTop=4;spacingBottom=6;"

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

    sorted_edges = sorted(architecture.edges, key=lambda e: _edge_sort_key(e, nodes_by_id))

    node_bounds = {
        nid: _get_abs_bounds(nid, nodes_by_id, layout)
        for nid in nodes_by_id
    }
    container_ids = {nid for nid, n in nodes_by_id.items() if n.is_container}

    source_slots: dict[str, int] = {}
    target_slots: dict[str, int] = {}
    edge_lane = 0

    source_label_slots: dict[str, int] = {}
    target_label_slots: dict[str, int] = {}

    for edge in sorted_edges:
        if edge.source_id not in nodes_by_id or edge.target_id not in nodes_by_id:
            continue

        cell_id += 1
        eid = f"edge_{cell_id}"

        exit_slot = source_slots.get(edge.source_id, 0)
        source_slots[edge.source_id] = exit_slot + 1
        entry_slot = target_slots.get(edge.target_id, 0)
        target_slots[edge.target_id] = entry_slot + 1

        src_box = node_bounds[edge.source_id]
        tgt_box = node_bounds[edge.target_id]
        obstacles = collect_obstacles(
            node_bounds,
            exclude={edge.source_id, edge.target_id},
            include_containers=True,
            container_ids=container_ids,
        )

        scx = src_box[0] + src_box[2] / 2
        tcx = tgt_box[0] + tgt_box[2] / 2
        prefer_horizontal = abs(tcx - scx) >= abs(
            (src_box[1] + src_box[3] / 2) - (tgt_box[1] + tgt_box[3] / 2)
        )

        exit_x, exit_y, entry_x, entry_y, waypoints = route_edge(
            src_box,
            tgt_box,
            obstacles,
            exit_slot=exit_slot,
            entry_slot=entry_slot,
            lane=edge_lane,
            prefer_horizontal=prefer_horizontal,
        )
        edge_lane += 1

        src = nodes_by_id.get(edge.source_id)
        tgt = nodes_by_id.get(edge.target_id)
        src_step = src.step_number if src and src.step_number else 999
        tgt_step = tgt.step_number if tgt and tgt.step_number else 999
        is_flow_edge = (
            edge.style != "dashed"
            and src_step < 900
            and tgt_step < 900
            and abs(src_step - tgt_step) <= 1
            and not waypoints
        )

        edge_style_parts = [
            "edgeStyle=orthogonalEdgeStyle",
            "rounded=1",
            "orthogonalLoop=1",
            "jettySize=auto",
            "html=1",
            "strokeWidth=2",
            "fontSize=11",
            "fontFamily=Helvetica,Arial,sans-serif",
            "endArrow=classic",
            "startArrow=none",
            "endFill=1",
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

        if edge.style == "dashed":
            edge_style_parts.append("dashed=1")
            edge_style_parts.append("dashPattern=8 4")
        elif edge.style == "bold":
            edge_style_parts.append("strokeWidth=3")
        elif is_flow_edge:
            edge_style_parts.append("flowAnimation=1")

        raw_label = _normalize_edge_label(edge.label)
        src_label_idx = source_label_slots.get(edge.source_id, 0)
        tgt_label_idx = target_label_slots.get(edge.target_id, 0)
        source_label_slots[edge.source_id] = src_label_idx + 1
        target_label_slots[edge.target_id] = tgt_label_idx + 1
        label_slot = edge_lane + exit_slot + entry_slot + src_label_idx + tgt_label_idx

        label_style_parts, label_geom_lines = _edge_label_style_and_geometry(
            raw_label,
            label_slot,
            has_waypoints=bool(waypoints),
        )
        edge_style_parts.extend(label_style_parts)

        edge_style = ";".join(edge_style_parts) + ";"
        label = _escape_xml(raw_label)

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
            if raw_label and label_geom_lines:
                # offset point inside geometry with waypoints
                rel_pos, perp_offset, _ = _LABEL_OFFSETS[label_slot % len(_LABEL_OFFSETS)]
                xml.append(f'        <mxPoint x="{rel_pos}" y="{perp_offset}" as="offset" />')
            xml.append('      </mxGeometry>')
        elif raw_label and label_geom_lines:
            xml.extend(label_geom_lines)
        else:
            xml.append('      <mxGeometry relative="1" as="geometry" />')
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
