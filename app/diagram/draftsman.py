import html as html_module
import re
from typing import TYPE_CHECKING

from app.diagram.edge_router import collect_obstacles, route_edge
from app.diagram.styles import build_style
from app.diagram.xml_tools import validate_and_fix_xml_legacy, wrap_with_mxfile
from app.models import ArchitectureDiagram, Node
from app.patterns.template_merge import apply_three_tier_scaffold

if TYPE_CHECKING:
    from app.core.logging import StepLogger


def _escape_xml(text: str) -> str:
    if not text:
        return ""
    return html_module.escape(str(text), quote=True)


def _edge_label(label: str | None) -> str:
    """Use edge label as-is (draw.io handles placement). Prompt guides brevity."""
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
    """
    Return ordered (number, title, summary) tuples for the Notes panel.

    Prefers the model-provided `flow_steps`; otherwise synthesizes from the
    numbered nodes so a Notes section always appears when steps exist.
    """
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
    """Container ancestors of a node, nearest first (next-ai parent chain)."""
    chain: list[str] = []
    current = nodes_by_id.get(nid)
    while current and current.parent_id and current.parent_id in nodes_by_id:
        chain.append(current.parent_id)
        current = nodes_by_id[current.parent_id]
    return chain


def _lca_parent(source_id: str, target_id: str, nodes_by_id: dict) -> str:
    """Lowest common ancestor container of two nodes, or '1' (root).

    Mirrors next-ai-draw-io: an edge is parented to the container that holds
    both endpoints so draw.io routes it natively within that coordinate space.
    """
    source_anc = _ancestor_chain(source_id, nodes_by_id)
    target_set = set(_ancestor_chain(target_id, nodes_by_id))
    for anc in source_anc:
        if anc in target_set:
            return anc
    return "1"


def _connection_points(
    src_box: tuple[int, int, int, int],
    tgt_box: tuple[int, int, int, int],
) -> tuple[float, float, float, float]:
    """Pick exit/entry connection points from geometry (next-ai style).

    Horizontal flow → right-center to left-center (or reverse).
    Vertical flow → bottom-center to top-center (or reverse).
    """
    scx = src_box[0] + src_box[2] / 2
    scy = src_box[1] + src_box[3] / 2
    tcx = tgt_box[0] + tgt_box[2] / 2
    tcy = tgt_box[1] + tgt_box[3] / 2
    dx = tcx - scx
    dy = tcy - scy

    if abs(dx) >= abs(dy):
        if dx >= 0:
            return 1.0, 0.5, 0.0, 0.5
        return 0.0, 0.5, 1.0, 0.5
    if dy >= 0:
        return 0.5, 1.0, 0.5, 0.0
    return 0.5, 0.0, 0.5, 1.0


def _is_ops_node(node) -> bool:
    """Shared services that belong below the main flow band."""
    if node.step_number is not None:
        return False
    shape = (node.shape_type or "").lower()
    label = (node.label or "").lower()
    ops_tokens = (
        "cloudwatch", "cloudtrail", "monitor", "logging", "log_analytics",
        "secrets", "kms", "key_vault", "sentinel", "config", "guardduty",
        "iam", "audit",
    )
    return any(t in shape or t in label for t in ops_tokens)


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


def _clamp_children_in_container(
    parent_id: str,
    layout: dict,
    nodes_by_id: dict,
    pad: int,
) -> None:
    """Keep child nodes inside their parent container with padding."""
    parent_ld = layout.get(parent_id)
    if not parent_ld:
        return
    children_pos = parent_ld.get("children_pos", {})
    if not children_pos:
        return

    parent_w = parent_ld["w"]
    parent_h = parent_ld["h"]
    max_x = pad
    max_y = pad

    for child_id, (cx, cy) in list(children_pos.items()):
        child_ld = layout.get(child_id, {})
        cw = child_ld.get("w", 48)
        ch = child_ld.get("h", 48)
        min_x = pad
        min_y = CONTAINER_PAD_TOP if parent_id in nodes_by_id and nodes_by_id[parent_id].is_container else pad
        cx = max(min_x, min(cx, parent_w - cw - pad))
        cy = max(min_y, min(cy, parent_h - ch - pad))
        children_pos[child_id] = (cx, cy)
        max_x = max(max_x, cx + cw + pad)
        max_y = max(max_y, cy + ch + pad)

    if max_x + pad > parent_w:
        parent_ld["w"] = max_x + pad
    if max_y + pad > parent_h:
        parent_ld["h"] = max_y + pad


CONTAINER_PAD_TOP = 48  # used by _clamp_children_in_container before generate_xml locals


def _waypoints_to_parent_relative(
    waypoints: list[tuple[int, int]],
    edge_parent: str,
    nodes_by_id: dict,
    layout: dict,
) -> list[tuple[int, int]]:
    """Convert absolute canvas waypoints to coordinates relative to edge parent."""
    if edge_parent == "1" or not waypoints:
        return waypoints
    px, py, _, _ = _get_abs_bounds(edge_parent, nodes_by_id, layout)
    return [(x - px, y - py) for x, y in waypoints]


def sanitize_layout_hints(architecture: ArchitectureDiagram) -> ArchitectureDiagram:
    """Strip LLM coordinate hints so the layout engine owns all placement."""
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

    LEAF_W = 60
    LEAF_H = 60
    LEAF_CELL_W = 148
    LEAF_CELL_H = 100
    LEAF_GAP = 40
    CONTAINER_PAD_TOP = 48
    CONTAINER_PAD = 40
    CONTAINER_GAP = 40
    ROOT_GAP = 72
    MAX_COLS = 6
    ROOT_TOP = 40
    MAX_CANVAS_W = 1200
    MAX_CANVAS_H = 860

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

        # Split main flow (step_number or non-ops) vs ops/support band below
        flow_leaves = [
            k for k in leaf_kids
            if nodes_by_id[k].step_number is not None or not _is_ops_node(nodes_by_id[k])
        ]
        ops_leaves = [k for k in leaf_kids if k not in flow_leaves]
        flow_leaves.sort(key=lambda k: nodes_by_id[k].step_number or 999)
        ops_leaves.sort(key=lambda k: _flow_sort_key(k, nodes_by_id))

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

        def _place_leaf_row(leaves: list[str], start_x: int, start_y: int) -> tuple[int, int, int]:
            """Place leaves left→right (next-ai flow band). Returns (right_x, bottom_y, row_h)."""
            lx = start_x
            ly = start_y
            row_h = 0
            for i, kid in enumerate(leaves):
                kw, kh = child_sizes[kid]
                cell_w = max(LEAF_CELL_W, kw + 10)
                cell_h = max(LEAF_CELL_H, kh + 10)
                if i > 0 and i % MAX_COLS == 0:
                    lx = start_x
                    ly += row_h + LEAF_GAP
                    row_h = 0
                offset_x = (cell_w - kw) // 2
                offset_y = (cell_h - kh) // 2
                children_pos[kid] = (lx + offset_x, ly + offset_y)
                lx += cell_w + LEAF_GAP
                row_h = max(row_h, cell_h)
            return lx, ly + row_h, row_h

        lx = CONTAINER_PAD
        ly = containers_bottom
        if flow_leaves:
            right_x, bottom_y, _ = _place_leaf_row(flow_leaves, lx, ly)
            lx, ly = right_x, bottom_y + LEAF_GAP
        if ops_leaves:
            _, bottom_y, _ = _place_leaf_row(ops_leaves, CONTAINER_PAD, ly)
            ly = bottom_y
        leaf_row_max_h = ly - containers_bottom

        leaf_right = lx if leaf_kids else CONTAINER_PAD
        leaf_bottom = (ly + CONTAINER_PAD) if leaf_kids else containers_bottom

        total_w = max(containers_right, leaf_right) + CONTAINER_PAD
        total_h = max(leaf_bottom, containers_bottom) + CONTAINER_PAD

        if node.width is not None:
            total_w = max(total_w, node.width)
        if node.height is not None:
            total_h = max(total_h, node.height)

        total_w = max(total_w, 220)
        total_h = max(total_h, 140)

        layout[nid] = {"w": total_w, "h": total_h, "children_pos": children_pos}
        _clamp_children_in_container(nid, layout, nodes_by_id, CONTAINER_PAD)
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

    left_leaves = sorted(
        left_leaves,
        key=lambda k: (nodes_by_id[k].step_number or 999, k),
    )
    main_containers = sorted(main_containers, key=lambda k: _flow_sort_key(k, nodes_by_id))

    # Entry nodes: horizontal row by step_number (next-ai left-to-right flow)
    lx = 40
    for i, rid in enumerate(left_leaves):
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y if node.y is not None else ROOT_TOP
            continue
        layout[rid]["global_x"] = lx
        layout[rid]["global_y"] = ROOT_TOP
        lx += LEAF_CELL_W + LEAF_GAP

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
    group_y = ROOT_TOP + max_main_h + 120

    blx = max(left_max_x, 360)
    for rid in bottom_leaves:
        node = nodes_by_id[rid]
        if node.x is not None:
            layout[rid]["global_x"] = node.x
            layout[rid]["global_y"] = node.y if node.y is not None else group_y
            blx = node.x + LEAF_CELL_W + LEAF_GAP
            continue
        layout[rid]["global_x"] = blx
        layout[rid]["global_y"] = group_y
        blx += LEAF_CELL_W + LEAF_GAP

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

    content_right = max((b[2] for b in all_bounds), default=1169)
    content_bottom = max((b[3] for b in all_bounds), default=827)

    canvas_w = max(min(content_right + 60, MAX_CANVAS_W), 900)
    canvas_h = max(min(content_bottom + 60, MAX_CANVAS_H), 600)

    # ── Reserve space for the Notes / Legend panel (below the diagram) ──
    flow_notes = _build_flow_notes(architecture)
    accent = _platform_accent(platform)
    notes_geom = None
    if flow_notes:
        notes_x = 40
        notes_w = max(min(canvas_w - 80, 760), 440)
        notes_y = content_bottom + 24
        # Header + one row per step (rows wrap, so budget generously).
        notes_h = 52 + len(flow_notes) * 30
        notes_geom = (notes_x, notes_y, notes_w, notes_h)
        canvas_h = max(canvas_h, notes_y + notes_h + 40)
        canvas_w = max(canvas_w, notes_x + notes_w + 40)

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

            # Numbered sequence badge on flow nodes (top-left corner of the icon).
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

    node_bounds = {
        nid: _get_abs_bounds(nid, nodes_by_id, layout)
        for nid in nodes_by_id
    }

    container_ids = {n.id for n in architecture.nodes if n.is_container}
    source_slot_counts: dict[str, int] = {}
    target_slot_counts: dict[str, int] = {}

    for edge in sorted_edges:
        if edge.source_id not in nodes_by_id or edge.target_id not in nodes_by_id:
            continue

        cell_id += 1
        eid = f"edge_{cell_id}"

        src_box = node_bounds[edge.source_id]
        tgt_box = node_bounds[edge.target_id]

        obstacles = collect_obstacles(
            node_bounds,
            exclude={edge.source_id, edge.target_id},
            include_containers=False,
            container_ids=container_ids,
        )
        exit_slot = source_slot_counts.get(edge.source_id, 0)
        entry_slot = target_slot_counts.get(edge.target_id, 0)
        source_slot_counts[edge.source_id] = exit_slot + 1
        target_slot_counts[edge.target_id] = entry_slot + 1

        scx = src_box[0] + src_box[2] / 2
        scy = src_box[1] + src_box[3] / 2
        tcx = tgt_box[0] + tgt_box[2] / 2
        tcy = tgt_box[1] + tgt_box[3] / 2
        prefer_horizontal = abs(tcx - scx) >= abs(tcy - scy)

        exit_x, exit_y, entry_x, entry_y, waypoints = route_edge(
            src_box,
            tgt_box,
            obstacles,
            exit_slot=exit_slot,
            entry_slot=entry_slot,
            lane=exit_slot,
            prefer_horizontal=prefer_horizontal,
        )

        edge_parent = _lca_parent(edge.source_id, edge.target_id, nodes_by_id)
        rel_waypoints = _waypoints_to_parent_relative(
            waypoints, edge_parent, nodes_by_id, layout
        )

        edge_style_parts = [
            "edgeStyle=orthogonalEdgeStyle",
            "rounded=0",
            "html=1",
            "endArrow=classic",
            f"exitX={exit_x}",
            f"exitY={exit_y}",
            f"entryX={entry_x}",
            f"entryY={entry_y}",
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
        if rel_waypoints:
            xml.append('      <mxGeometry relative="1" as="geometry">')
            xml.append('        <Array as="points">')
            for wx, wy in rel_waypoints:
                xml.append(f'          <mxPoint x="{wx}" y="{wy}" />')
            xml.append('        </Array>')
            xml.append('      </mxGeometry>')
        else:
            xml.append('      <mxGeometry relative="1" as="geometry" />')
        xml.append('    </mxCell>')

    # ── Notes / Legend panel (numbered, point-wise step summaries) ──
    if notes_geom and flow_notes:
        nx, ny, nw, nh = notes_geom
        diagram_title = (getattr(architecture, "title", None) or "").strip()
        # Text content is HTML-escaped here; the whole value is XML-attribute-escaped below.
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
