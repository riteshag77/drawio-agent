"""Compile a ``GenericDiagramIR`` into draw.io XML.

No ELK, no cloud-platform styling, no multi-view fan-out — a generic (as-is)
diagram is always a single page. Icon resolution follows the same two-phase
convention every other view uses (see ``compiler/emit.py``'s ``vertex``
docstring): this module only stamps a transient ``shape_type`` attribute from
``GenericNode.icon_hint`` plus a plain-shape placeholder style;
``xml_pipeline._run_generic_image_pipeline`` resolves it via MCP afterwards
and merges the result in, exactly like the cloud path.

Leaf size and position deliberately deviate from every other view's fixed
``geometry_spec.LEAF_W``×``LEAF_H`` grid — see ``_SIZE_SCALE``/
``_place_nodes`` below. This view's entire purpose is matching a source
image's relative sizing/position, not this app's cloud design-system
consistency, so it is the one deliberate exception to "leaf size is always
LEAF_W/H" (see CLAUDE.md).
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import generic_theme as theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import CompilerError
from app.domain.ir.generic import GenericDiagramIR, GenericEdge, GenericNode

_ORIGIN_X = 40.0
_ORIGIN_Y = 40.0
_BLOCK_GAP_X = 60.0
_PITCH = geo.DATA_CANONICAL_PITCH

#: Relative-only size buckets (vision LLMs are unreliable at absolute pixel
#: measurement but reasonably good at "this is bigger/smaller than that"
#: comparisons) — scaled evenly on both axes, no per-shape special-casing.
_SIZE_SCALE: dict[str, float] = {"small": 0.72, "normal": 1.0, "large": 1.35}


def compile_generic_ir(ir: GenericDiagramIR, *, page_name: str = "Diagram") -> str:
    """Render ``ir`` to a complete draw.io document.

    The IR is expected to have passed ``semantic_errors()`` already; this
    raises rather than silently compiling something invalid.
    """
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    return _GenericCompiler(ir, page_name).run()


def _node_size(node: GenericNode) -> tuple[float, float]:
    scale = _SIZE_SCALE.get(node.size_hint, 1.0)
    return geo.LEAF_W * scale, geo.LEAF_H * scale


def _overlaps(ax: float, ay: float, aw: float, ah: float, bx: float, by: float, bw: float, bh: float) -> bool:
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def _resolve_collision(
    px: float, py: float, w: float, h: float, placed: list[tuple[float, float, float, float]],
) -> tuple[float, float]:
    """Nudge ``(px, py)`` right until its box clears every already-placed box.

    Same "push along one axis until clear" idea the ordinal row/col grid
    already used (see the old ``_place`` occupied-cell loop this replaces),
    adapted to continuous coordinates and variable per-node sizes.
    """
    moved = True
    while moved:
        moved = False
        for ox, oy, ow, oh in placed:
            if _overlaps(px, py, w, h, ox, oy, ow, oh):
                px = ox + ow + geo.LEAF_MIN_GAP
                moved = True
    return px, py


def _place_proportional(
    nodes: list[GenericNode], sizes: dict[str, tuple[float, float]],
) -> dict[str, tuple[float, float]]:
    """Map ``x``/``y`` fractional hints onto a pixel canvas sized to roughly
    the same density the deterministic grid fallback would use, so as-is
    diagrams stay visually consistent with the rest of the app rather than
    introducing a new scale. A node missing just one axis (rare — the model
    tends to set both or neither) falls back to a plain ``<=4``-per-row grid
    position for that axis only; this is a narrower, per-axis patch and
    deliberately does not invoke the full topological layout ``_place_nodes``
    uses when *no* node in the set has any hint at all (see
    ``_place_topological``).
    """
    canvas_w, canvas_h, _cols, _rows = geo.grid_size(len(nodes), is_group=False)
    canvas_w = max(canvas_w, geo.LEAF_W)
    canvas_h = max(canvas_h, geo.LEAF_H)

    placed: list[tuple[float, float, float, float]] = []
    positions: dict[str, tuple[float, float]] = {}
    fallback_index = 0
    for node in nodes:
        w, h = sizes[node.id]
        if node.x is not None:
            cx = node.x * canvas_w
        else:
            cx = (fallback_index % geo.LEAF_GRID_MAX_COLS) * _PITCH + geo.LEAF_W / 2.0
        if node.y is not None:
            cy = node.y * canvas_h
        else:
            cy = (fallback_index // geo.LEAF_GRID_MAX_COLS) * geo.ROW_PITCH_Y + geo.LEAF_H / 2.0
        if node.x is None and node.y is None:
            fallback_index += 1
        px = max(0.0, cx - w / 2.0)
        py = max(0.0, cy - h / 2.0)
        px, py = _resolve_collision(px, py, w, h, placed)
        placed.append((px, py, w, h))
        positions[node.id] = (px, py)
    return positions


def _place_topological(
    nodes: list[GenericNode], edges: list[GenericEdge],
) -> dict[str, tuple[float, float]]:
    """Layered layout read off the edge graph — nodes with no incoming edge
    start at row 0, every edge increases row by one, siblings at the same
    row keep list order left-to-right.

    Used whenever no node in this group carries an ``x``/``y`` hint. In
    practice that's the *common* case, not the exception: across real
    generations, the model leaves x/y null roughly 3 times out of 4 (it's
    reliably good at these coordinates on the rare occasion it commits to
    them, just cautious about doing so — see ``GenericNode.x``'s "leave
    blank when unsure" instruction). The previous fallback packed nodes in
    raw list order into a fixed ``<=4``-per-row grid, which flattens any
    non-grid-shaped structure — a linear flowchart with branches landed
    four unrelated nodes on one row regardless of which pointed to which.
    Reading the edges instead reconstructs a sensible top-to-bottom flow
    without needing any position hint from the model at all.
    """
    ids = {n.id for n in nodes}
    indegree: dict[str, int] = {n.id: 0 for n in nodes}
    adjacency: dict[str, list[str]] = {n.id: [] for n in nodes}
    for edge in edges:
        if edge.source in ids and edge.target in ids and edge.source != edge.target:
            adjacency[edge.source].append(edge.target)
            indegree[edge.target] += 1

    depth: dict[str, int] = {n.id: 0 for n in nodes if indegree[n.id] == 0}
    queue = list(depth.keys())
    head = 0
    while head < len(queue):
        nid = queue[head]
        head += 1
        for nxt in adjacency[nid]:
            candidate = depth[nid] + 1
            if candidate > depth.get(nxt, -1):
                depth[nxt] = candidate
            if nxt not in queue:
                queue.append(nxt)
    for node in nodes:
        depth.setdefault(node.id, 0)

    rows: dict[int, list[GenericNode]] = {}
    for node in nodes:
        rows.setdefault(depth[node.id], []).append(node)

    # Each depth level still wraps at <=4-per-row (same as the plain grid) —
    # otherwise a level with many siblings (or, at the extreme, zero edges at
    # all so every node sits at depth 0) would form one arbitrarily wide row
    # instead of the compact block the grid fallback always produced.
    positions: dict[str, tuple[float, float]] = {}
    y_cursor = 0
    for row_idx in sorted(rows):
        row_nodes = rows[row_idx]
        cols = min(geo.LEAF_GRID_MAX_COLS, len(row_nodes)) or 1
        sub_rows = -(-len(row_nodes) // cols)  # ceil division
        for i, node in enumerate(row_nodes):
            sub_row, col = divmod(i, cols)
            positions[node.id] = (col * _PITCH, (y_cursor + sub_row) * geo.ROW_PITCH_Y)
        y_cursor += sub_rows
    return positions


def _place_nodes(
    nodes: list[GenericNode], edges: list[GenericEdge],
) -> tuple[dict[str, tuple[float, float]], float, float]:
    """Return ``{node_id: (x, y)}`` relative to a (0, 0) content origin, plus
    the content box's ``(width, height)``.
    """
    if not nodes:
        return {}, 0.0, 0.0
    sizes = {n.id: _node_size(n) for n in nodes}
    hinted = any(n.x is not None or n.y is not None for n in nodes)
    positions = _place_proportional(nodes, sizes) if hinted else _place_topological(nodes, edges)

    content_w = max(positions[n.id][0] + sizes[n.id][0] for n in nodes)
    content_h = max(positions[n.id][1] + sizes[n.id][1] for n in nodes) + geo.LABEL_BAND_H
    return positions, content_w, content_h


class _GenericCompiler:
    def __init__(self, ir: GenericDiagramIR, page_name: str) -> None:
        self.ir = ir
        self.w = CellWriter(page_name)

    def run(self) -> str:
        by_group: dict[str, list[GenericNode]] = {}
        ungrouped: list[GenericNode] = []
        for node in self.ir.nodes:
            if node.group:
                by_group.setdefault(node.group, []).append(node)
            else:
                ungrouped.append(node)

        cursor_x = _ORIGIN_X
        max_bottom = _ORIGIN_Y

        if ungrouped:
            positions, content_w, content_h = _place_nodes(ungrouped, self.ir.edges)
            for node in ungrouped:
                rel_x, rel_y = positions[node.id]
                w, h = _node_size(node)
                self.w.vertex(
                    id=node.id, label=node.label,
                    style=theme.node_style(node.shape, fill_color=node.fill_color, stroke_color=node.stroke_color),
                    x=cursor_x + rel_x, y=_ORIGIN_Y + rel_y,
                    w=w, h=h, parent="1",
                    shape_type=node.icon_hint,
                )
            cursor_x += content_w + _BLOCK_GAP_X
            max_bottom = max(max_bottom, _ORIGIN_Y + content_h)

        for group in self.ir.groups:
            nodes = by_group.get(group.id, [])
            positions, content_w, content_h = _place_nodes(nodes, self.ir.edges)
            box_w = max(content_w + geo.PAD * 2, 160.0)
            box_h = max(geo.TITLE_PAD + content_h + geo.PAD, 120.0)
            self.w.vertex(
                id=group.id, label=group.label, style=theme.group_style(group.boundary),
                x=cursor_x, y=_ORIGIN_Y, w=box_w, h=box_h, parent="1",
            )
            for node in nodes:
                rel_x, rel_y = positions[node.id]
                w, h = _node_size(node)
                self.w.vertex(
                    id=node.id, label=node.label,
                    style=theme.node_style(node.shape, fill_color=node.fill_color, stroke_color=node.stroke_color),
                    x=geo.PAD + rel_x, y=geo.TITLE_PAD + rel_y,
                    w=w, h=h, parent=group.id,
                    shape_type=node.icon_hint,
                )
            cursor_x += box_w + _BLOCK_GAP_X
            max_bottom = max(max_bottom, _ORIGIN_Y + box_h)

        for index, edge in enumerate(self.ir.edges):
            self.w.edge(
                id=f"edge_{index}", source=edge.source, target=edge.target, label=edge.label,
                style=theme.edge_style(
                    line_style=edge.line_style, dashed=edge.dashed, arrow=edge.arrow, color=edge.color,
                ),
            )

        self.w.mark_layout_authoritative()
        page_w = max(cursor_x - _BLOCK_GAP_X + 200.0, 400.0)
        page_h = max_bottom + 200.0
        return self.w.to_xml(page_width=page_w, page_height=page_h)
