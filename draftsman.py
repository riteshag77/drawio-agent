import json
import os
import html as html_module


def _escape_xml(text: str) -> str:
    """Escape special characters for XML attribute values."""
    if not text:
        return ""
    return html_module.escape(str(text), quote=True)


def _flow_sort_key(nid: str, nodes_by_id: dict) -> tuple:
    """Order nodes left-to-right following traffic flow and subnet tiers."""
    node = nodes_by_id[nid]
    label = node.label.lower()
    shape = node.shape_type.lower()
    if "public" in label or "public" in shape:
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
    """Return (x, y, w, h) in root canvas coordinates."""
    node = nodes_by_id[nid]
    ld = layout.get(nid, {})
    w, h = ld.get("w", 48), ld.get("h", 48)
    if node.parent_id and node.parent_id in nodes_by_id:
        px, py, _, _ = _get_abs_bounds(node.parent_id, nodes_by_id, layout)
        cx, cy = ld.get("children_pos", {}).get(nid, (0, 0))
        return px + cx, py + cy, w, h
    return ld.get("global_x", 0), ld.get("global_y", 0), w, h


def _anchor_points(src_box: tuple, tgt_box: tuple) -> tuple[float, float, float, float]:
    """Pick edge exit/entry sides so connectors attach at shape perimeters."""
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
    """Add orthogonal corridor waypoints so lines route around shapes, not through them."""
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
    """Render primary traffic-flow edges before secondary/monitoring edges."""
    src = nodes_by_id.get(edge.source_id)
    tgt = nodes_by_id.get(edge.target_id)
    src_step = src.step_number if src and src.step_number else 999
    tgt_step = tgt.step_number if tgt and tgt.step_number else 999
    is_adjacent_flow = src_step < 900 and tgt_step < 900 and abs(src_step - tgt_step) <= 1
    return (0 if is_adjacent_flow else 1, 1 if edge.style == "dashed" else 0, src_step, tgt_step)


def generate_xml(architecture, platform: str = "aws") -> str:
    """Generate Draw.io XML from an ArchitectureDiagram model with smart layout."""

    shapes_path = os.path.join(os.path.dirname(__file__), "shapes.json")
    with open(shapes_path, "r") as f:
        shapes = json.load(f)

    # ── Platform Themes ───────────────────────────────────────────────
    platform = (platform or "aws").lower()
    if platform == "gcp":
        default_edge_color = "#4285F4"  # Google Blue
        badge_color = "#EA4335"        # Google Red
    elif platform == "azure":
        default_edge_color = "#0078D4"  # Azure Blue
        badge_color = "#0078D4"        # Azure Blue
    else:  # aws or multi
        default_edge_color = "#ED7100"  # AWS Orange
        badge_color = "#ED7100"        # AWS Orange

    # ── Layout constants ──────────────────────────────────────────────
    LEAF_W = 48           # icon width for leaf nodes
    LEAF_H = 48           # icon height for leaf nodes
    LEAF_CELL_W = 120     # horizontal cell stride for leaf nodes (icon + label space)
    LEAF_CELL_H = 100     # vertical cell stride for leaf nodes
    CONTAINER_PAD_TOP = 50   # space above children (for header label)
    CONTAINER_PAD = 40       # padding on left/right/bottom inside container
    CONTAINER_GAP = 40       # gap between sibling containers at the same level
    ROOT_GAP = 100           # gap between root-level nodes on the canvas
    MAX_COLS = 4             # max children per row inside a container
    STEP_BADGE_R = 14        # step-number badge radius

    # ── Build lookup structures ───────────────────────────────────────
    nodes_by_id = {n.id: n for n in architecture.nodes}
    children_map: dict[str, list[str]] = {}
    roots: list[str] = []

    for node in architecture.nodes:
        if node.parent_id and node.parent_id in nodes_by_id:
            children_map.setdefault(node.parent_id, []).append(node.id)
        else:
            roots.append(node.id)

    # ── Recursive size calculation ────────────────────────────────────
    # Each entry: { w, h, children_pos: {child_id: (rel_x, rel_y)} }
    layout: dict[str, dict] = {}

    def calc_size(nid: str) -> tuple[int, int]:
        """Return (width, height) needed for *nid*, populating `layout`."""
        node = nodes_by_id[nid]
        kids = children_map.get(nid, [])

        # ---- Leaf node (no children or not a container) ----
        if not node.is_container or not kids:
            w = node.width or LEAF_W
            h = node.height or LEAF_H
            layout[nid] = {"w": w, "h": h, "children_pos": {}}
            return w, h

        # ---- Container node ----
        # Separate children into sub-containers vs leaf nodes, ordered for clean L→R flow
        kids = sorted(kids, key=lambda k: _flow_sort_key(k, nodes_by_id))
        sub_containers = [k for k in kids if nodes_by_id[k].is_container]
        leaf_kids = [k for k in kids if not nodes_by_id[k].is_container]

        # Recursively size all children first
        child_sizes = {}
        for kid in kids:
            child_sizes[kid] = calc_size(kid)

        children_pos: dict[str, tuple[int, int]] = {}

        # ---- Place sub-containers first (horizontally) ----
        cx = CONTAINER_PAD
        cy = CONTAINER_PAD_TOP + 10
        row_max_h = 0
        container_cols = max(2, MAX_COLS - 1)

        for i, kid in enumerate(sub_containers):
            kw, kh = child_sizes[kid]
            if i > 0 and i % container_cols == 0:
                cx = CONTAINER_PAD
                cy += row_max_h + CONTAINER_GAP
                row_max_h = 0
            children_pos[kid] = (cx, cy)
            cx += kw + CONTAINER_GAP
            row_max_h = max(row_max_h, kh)

        # Track the bottom of sub-containers section
        containers_bottom = cy + row_max_h + CONTAINER_GAP if sub_containers else CONTAINER_PAD_TOP + 10
        containers_right = cx if sub_containers else CONTAINER_PAD

        # ---- Place leaf nodes below sub-containers (grid) ----
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

            # Center the icon in its cell
            offset_x = (cell_w - kw) // 2
            offset_y = (cell_h - kh) // 2
            children_pos[kid] = (lx + offset_x, ly + offset_y)
            lx += cell_w
            leaf_row_max_h = max(leaf_row_max_h, cell_h)

        leaf_right = lx if leaf_kids else CONTAINER_PAD
        leaf_bottom = (ly + leaf_row_max_h + CONTAINER_PAD) if leaf_kids else containers_bottom

        # ---- Container total size ----
        total_w = max(containers_right, leaf_right) + CONTAINER_PAD
        total_h = max(leaf_bottom, containers_bottom) + CONTAINER_PAD

        # Apply explicit size hints if given
        if node.width:
            total_w = max(total_w, node.width)
        if node.height:
            total_h = max(total_h, node.height)

        # Ensure a minimum size with breathing room
        total_w = max(total_w, 220)
        total_h = max(total_h, 140)

        layout[nid] = {"w": total_w, "h": total_h, "children_pos": children_pos}
        return total_w, total_h

    # ── Size all roots ────────────────────────────────────────────────
    for rid in roots:
        calc_size(rid)

    # ── Position roots on canvas ──────────────────────────────────────
    # Separate into: containers (big, center), standalone leaves (periphery)
    root_containers = [r for r in roots if nodes_by_id[r].is_container]
    root_leaves = [r for r in roots if not nodes_by_id[r].is_container]

    # Split root containers into main (VPC, AZ, Region) and group/utility containers (Monitoring, Security, etc.)
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
        # Fallback if no main containers are present
        main_containers = group_containers
        group_containers = []

    # Partition root leaf nodes based on their roles
    left_leaves = []
    right_leaves = []
    bottom_leaves = []

    for rid in root_leaves:
        node = nodes_by_id[rid]
        label = node.label.lower()
        shape = node.shape_type.lower()

        # Check if it's an external third-party service/processor
        if any(w in label for w in ["payment", "processor", "third party", "external", "saas", "api key", "merchant"]):
            right_leaves.append(rid)
        # Check if it's a standalone DB/storage at the bottom
        elif any(w in label for w in ["dynamodb", "s3", "database", "bucket", "storage", "blob", "gcs", "sql", "spanner", "cosmos"]) or any(s in shape for s in ["dynamodb", "s3", "blob", "gcs", "sql", "spanner", "cosmos"]):
            bottom_leaves.append(rid)
        # Default to left side (Route 53, users, CloudFront, WAF)
        else:
            left_leaves.append(rid)

    # Place left leaves first so main VPC can start after them
    lx = 40
    ly = 80
    for i, rid in enumerate(left_leaves):
        if i > 0 and i % 4 == 0:
            lx += 140
            ly = 80
        layout[rid]["global_x"] = lx
        layout[rid]["global_y"] = ly
        ly += LEAF_CELL_H + 30

    # Place main containers horizontally, after left-side entry nodes
    left_max_x = 40
    if left_leaves:
        left_max_x = max(layout[rid].get("global_x", 0) + layout[rid]["w"] for rid in left_leaves) + 80
    cx = max(left_max_x, 400)
    cy = 80
    for rid in main_containers:
        layout[rid]["global_x"] = cx
        layout[rid]["global_y"] = cy
        cx += layout[rid]["w"] + ROOT_GAP

    # Place right leaves to the right of main containers
    rx = cx + 40
    ry = 80
    for i, rid in enumerate(right_leaves):
        if i > 0 and i % 4 == 0:
            rx += 140
            ry = 80
        layout[rid]["global_x"] = rx
        layout[rid]["global_y"] = ry
        ry += LEAF_CELL_H + 30

    # Determine y offset for bottom elements — keep clear corridor above for edge routing
    max_main_h = max([layout[rid]["h"] for rid in main_containers]) if main_containers else 0
    group_y = cy + max_main_h + 140

    # Place bottom leaves to the left of group containers
    blx = 400
    for rid in bottom_leaves:
        layout[rid]["global_x"] = blx
        layout[rid]["global_y"] = group_y
        blx += LEAF_CELL_W + 50

    # Place group containers horizontally at the bottom
    gcx = blx + 40 if bottom_leaves else 400
    for rid in group_containers:
        layout[rid]["global_x"] = gcx
        layout[rid]["global_y"] = group_y
        gcx += layout[rid]["w"] + ROOT_GAP

    # ── Build XML ─────────────────────────────────────────────────────
    xml = [
        '<mxGraphModel dx="2500" dy="2500" grid="1" gridSize="10" guides="1" '
        'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
        'pageWidth="3200" pageHeight="2200" math="0" shadow="0">',
        '  <root>',
        '    <mxCell id="0" />',
        '    <mxCell id="1" parent="0" />',
    ]

    cell_id = 100  # auto-increment for step badges and edges

    for node in architecture.nodes:
        nid = node.id
        ld = layout.get(nid, {"w": LEAF_W, "h": LEAF_H, "children_pos": {}})
        w, h = ld["w"], ld["h"]

        # Determine parent cell and position
        if node.parent_id and node.parent_id in nodes_by_id:
            parent_cell = node.parent_id
            parent_ld = layout.get(node.parent_id, {})
            x, y = parent_ld.get("children_pos", {}).get(nid, (0, 0))
        else:
            parent_cell = "1"
            x = ld.get("global_x", 0)
            y = ld.get("global_y", 0)

        # Resolve style
        style = shapes.get(node.shape_type, shapes.get("generic"))

        # Inject standard font family and perimeter routing for clean connectors
        if "fontFamily=" not in style:
            style += ";fontFamily=SF Pro Text,Segoe UI,Helvetica,Arial,sans-serif;"
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

        # ── Step-number badge ─────────────────────────────────────
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

    # ── Edges ─────────────────────────────────────────────────────────
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

        # Base orthogonal style with perimeter attachment and line jumps
        edge_style_parts = [
            "edgeStyle=orthogonalEdgeStyle",
            "rounded=1",
            "orthogonalLoop=1",
            "jettySize=auto",
            "html=1",
            "strokeWidth=2",
            "fontSize=10",
            "fontFamily=SF Pro Text,Segoe UI,Helvetica,Arial,sans-serif",
            "endArrow=classicThin",
            "startArrow=none",
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

        # Color routing
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
    return "\n".join(xml)
