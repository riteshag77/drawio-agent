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
from app.domain.ir.generic import GenericDiagramIR, GenericEdge, GenericGroup, GenericNode

_ORIGIN_X = 40.0
_ORIGIN_Y = 40.0
_BLOCK_GAP_X = 60.0
_PITCH = geo.DATA_CANONICAL_PITCH

#: Relative-only size buckets (vision LLMs are unreliable at absolute pixel
#: measurement but reasonably good at "this is bigger/smaller than that"
#: comparisons) — scaled evenly on both axes, no per-shape special-casing.
_SIZE_SCALE: dict[str, float] = {"small": 0.72, "normal": 1.0, "large": 1.35}
#: Ceiling on the proportional canvas, in leaf pitches *per node*. Generous
#: enough that a genuinely sparse layout never hits it, small enough that
#: near-identical hints can't produce a six-figure page — see
#: ``_place_proportional``.
_MAX_CANVAS_PITCHES = 4.0


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


def _free_prefix(preferred: str, taken: set[str]) -> str:
    """A prefix no declared id starts with, so ``f"{prefix}_{i}"`` can never
    collide with a model-authored id. Mirrors
    ``elk_layout_network.connectivity_shelf_id``'s approach for the other
    compiler-invented id in this codebase."""
    if not any(cid.startswith(f"{preferred}_") for cid in taken):
        return preferred
    suffix = 2
    while any(cid.startswith(f"{preferred}{suffix}_") for cid in taken):
        suffix += 1
    return f"{preferred}{suffix}"


def _resolve_collision(
    px: float, py: float, w: float, h: float, placed: list[tuple[float, float, float, float]],
) -> tuple[float, float]:
    """Nudge ``(px, py)`` right until its box clears every already-placed box.

    A "push along one axis until clear" sweep over continuous coordinates
    with variable per-node sizes. Note this only ever pushes right, so a
    source image with genuinely overlapping shapes (nested boxes, corner
    badges, Venn diagrams) cannot be reproduced by this placement model.
    """
    moved = True
    while moved:
        moved = False
        for ox, oy, ow, oh in placed:
            if _overlaps(px, py, w, h, ox, oy, ow, oh):
                px = ox + ow + geo.LEAF_MIN_GAP
                moved = True
    return px, py


def _min_positive_gap(values: list[float]) -> float | None:
    """Smallest gap between two *distinct* values, or ``None`` if fewer than
    two distinct values are present (no gap to protect)."""
    uniq = sorted(set(round(v, 4) for v in values))
    if len(uniq) < 2:
        return None
    return min(b - a for a, b in zip(uniq, uniq[1:]))


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

    The base canvas from ``grid_size`` assumes a dense ``<=4``-column packing,
    which is far too small for a tall/narrow layout (e.g. a 6-node flowchart
    spread across 4 distinct y-levels) — two genuinely different y hints can
    then map to pixel positions closer than one leaf height apart, which
    ``_resolve_collision`` "fixes" by shoving nodes sideways since it only
    ever pushes along x. That cascades into a diagonal, scattered layout that
    no longer resembles the model's intended grid — confirmed by comparing a
    real capture pair on the same source image where the model that *did*
    give hints (thus taking this path) rendered worse than the one that
    abstained entirely (thus using ``_place_topological`` instead). Widening
    the canvas so the smallest real gap between distinct hints clears one
    leaf-plus-gap keeps distinct rows/columns distinct without needing any
    collision push for legitimately different positions.
    """
    base_w, base_h, _cols, _rows = geo.grid_size(len(nodes), is_group=False)
    canvas_w = max(base_w, geo.LEAF_W)
    canvas_h = max(base_h, geo.LEAF_H)

    # Widening is bounded. The gap-derived canvas is 1/gap, so hints that are
    # *nearly* identical (not equal — those already collapse at the 4dp
    # rounding in _min_positive_gap) blow it up without limit: measured,
    # x=0.5 vs x=0.5001 produced a 490,377px-wide page, and a vision model
    # emitting 4-decimal coordinates is entirely ordinary.
    #
    # The ceiling scales with node count rather than with the (dense, <=4-col)
    # base grid, so it stays clear of real layouts: N nodes spread across the
    # image need at most a few pitches each, and the genuinely-distinct-hints
    # case this widening exists for (e.g. 6 nodes over 4 y-levels, gaps ~0.25)
    # lands an order of magnitude below it. When hints really are that close,
    # the correct rendering is two adjacent nodes — which _resolve_collision
    # already produces — not a tiny fraction inflated into a huge pixel gap.
    ceiling = max(len(nodes), 1) * _PITCH * _MAX_CANVAS_PITCHES
    max_w = max(canvas_w, ceiling)
    max_h = max(canvas_h, ceiling)

    xs = [n.x for n in nodes if n.x is not None]
    ys = [n.y for n in nodes if n.y is not None]
    gap_x = _min_positive_gap(xs)
    gap_y = _min_positive_gap(ys)
    if gap_x:
        canvas_w = min(max_w, max(canvas_w, (geo.LEAF_W + geo.LEAF_MIN_GAP) / gap_x))
    if gap_y:
        canvas_h = min(max_h, max(canvas_h, (geo.LEAF_H + geo.LEAF_MIN_GAP) / gap_y))

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
        # Advance whenever *either* axis was unhinted, not only when both
        # were. Incrementing on both-missing alone gave every x-only node the
        # same fallback row and every y-only node the same fallback column,
        # which _resolve_collision then untangled by shoving them sideways
        # into columns no hint asked for — the docstring above already
        # described the per-axis behaviour this now implements.
        if node.x is None or node.y is None:
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

    # Drop back-edges first, so what follows runs on a DAG. A "retry" arrow
    # to an earlier step is ordinary in the flowcharts this view targets, and
    # a cycle used to break layering twice over: a *fully* cyclic graph has no
    # indegree-0 node at all, so nothing seeded the walk and every node fell
    # back to depth 0 (one flat row), and longest-path relaxation around a
    # loop never settles. Removing the edges that close a loop keeps the arrow
    # in the diagram (this only affects layering, not what is drawn) while
    # letting the layering see the underlying forward flow.
    roots = [n.id for n in nodes if indegree[n.id] == 0] or ([nodes[0].id] if nodes else [])
    forward: dict[str, list[str]] = {n.id: [] for n in nodes}
    on_stack: set[str] = set()
    visited: set[str] = set()
    # Iterative DFS — a recursive one would hit Python's stack limit on a long
    # chain, and nothing caps node count on this view (the "<=40 nodes" line in
    # generic.txt is prose only; the node count is the source image's, not a
    # design choice).
    for start in [*roots, *(n.id for n in nodes)]:
        if start in visited:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        visited.add(start)
        on_stack.add(start)
        while stack:
            nid, cursor = stack[-1]
            if cursor >= len(adjacency[nid]):
                stack.pop()
                on_stack.discard(nid)
                continue
            stack[-1] = (nid, cursor + 1)
            nxt = adjacency[nid][cursor]
            if nxt in on_stack:
                continue  # closes a loop — not a layering constraint
            forward[nid].append(nxt)
            if nxt not in visited:
                visited.add(nxt)
                on_stack.add(nxt)
                stack.append((nxt, 0))

    depth: dict[str, int] = {rid: 0 for rid in roots}
    queue = list(depth.keys())
    # Longest-path relaxation over the DAG. A node must be re-visited when its
    # depth *increases*, or a later increase never reaches its descendants —
    # for a->b, a->c, c->b, b->d that put d on the same row as its own parent
    # b. `in_queue` replaces what was an O(n) list scan per edge.
    in_queue = set(queue)
    head = 0
    while head < len(queue):
        nid = queue[head]
        head += 1
        in_queue.discard(nid)
        for nxt in forward[nid]:
            candidate = depth[nid] + 1
            if candidate > depth.get(nxt, -1):
                depth[nxt] = candidate
                if nxt not in in_queue:
                    queue.append(nxt)
                    in_queue.add(nxt)
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


#: Shapes whose base style is a bare draw.io keyword (``rhombus;``,
#: ``ellipse;shape=cloud;``, ``text;``) rather than a ``shape=`` assignment.
#: merge_drawio_styles is a plain dict merge, so an MCP stencil's ``shape=``
#: lands *alongside* the keyword instead of replacing it — the leading keyword
#: typically wins and the icon silently doesn't render, while a borderless
#: ``text`` cell also gains a fill. Resolution is skipped for these.
_ICON_UNSAFE_SHAPES = frozenset({"ellipse", "diamond", "cloud", "text", "actor"})


def _resolvable_icon_hint(node: GenericNode) -> str:
    """``node.icon_hint``, or "" when resolving it would cost more fidelity
    than the icon is worth.

    MCP wins on fill/stroke in ``merge_drawio_styles``, so resolving an icon
    for a node whose colour was transcribed from the source image silently
    replaces that colour with the vendor's — on the one view whose contract is
    to reproduce what the user drew. Fidelity wins here; the icon is a bonus.
    """
    if not node.icon_hint:
        return ""
    if node.fill_color or node.stroke_color:
        return ""
    if node.shape in _ICON_UNSAFE_SHAPES:
        return ""
    return node.icon_hint


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
                    shape_type=_resolvable_icon_hint(node),
                )
            cursor_x += content_w + _BLOCK_GAP_X
            max_bottom = max(max_bottom, _ORIGIN_Y + content_h)

        # Group boxes honour their own x/y hints in one page-global frame, so
        # a boundary drawn at the bottom of the source image renders at the
        # bottom. Without hints they keep the original left-to-right
        # sequencing, so an un-hinted diagram is laid out exactly as before.
        boxes: list[tuple[GenericGroup, list[GenericNode], dict, float, float]] = []
        for group in self.ir.groups:
            nodes = by_group.get(group.id, [])
            positions, content_w, content_h = _place_nodes(nodes, self.ir.edges)
            boxes.append((
                group, nodes, positions,
                max(content_w + geo.PAD * 2, 160.0),
                max(geo.TITLE_PAD + content_h + geo.PAD, 120.0),
            ))

        hinted_groups = [b for b in boxes if b[0].x is not None or b[0].y is not None]
        group_origins: dict[str, tuple[float, float]] = {}
        if hinted_groups:
            # Canvas spans the un-hinted content already placed plus room for
            # every hinted box, so a hint of 1.0 lands inside the page rather
            # than off the right/bottom edge.
            span_w = max(
                sum(b[3] for b in boxes) + _BLOCK_GAP_X * len(boxes),
                cursor_x - _ORIGIN_X,
                1.0,
            )
            span_h = max(
                sum(b[4] for b in boxes) + _BLOCK_GAP_X * len(boxes) + (max_bottom - _ORIGIN_Y),
                1.0,
            )
            placed: list[tuple[float, float, float, float]] = []
            for group, _nodes, _pos, box_w, box_h in boxes:
                if group.x is None and group.y is None:
                    continue
                gx = _ORIGIN_X + (group.x or 0.0) * max(span_w - box_w, 0.0)
                gy = _ORIGIN_Y + (group.y or 0.0) * max(span_h - box_h, 0.0)
                gx, gy = _resolve_collision(gx, gy, box_w, box_h, placed)
                placed.append((gx, gy, box_w, box_h))
                group_origins[group.id] = (gx, gy)

        for group, nodes, positions, box_w, box_h in boxes:
            gx, gy = group_origins.get(group.id, (cursor_x, _ORIGIN_Y))
            if group.id not in group_origins:
                cursor_x += box_w + _BLOCK_GAP_X
            self.w.vertex(
                id=group.id, label=group.label, style=theme.group_style(group.boundary),
                x=gx, y=gy, w=box_w, h=box_h, parent="1",
            )
            for node in nodes:
                rel_x, rel_y = positions[node.id]
                w, h = _node_size(node)
                self.w.vertex(
                    id=node.id, label=node.label,
                    style=theme.node_style(node.shape, fill_color=node.fill_color, stroke_color=node.stroke_color),
                    x=geo.PAD + rel_x, y=geo.TITLE_PAD + rel_y,
                    w=w, h=h, parent=group.id,
                    shape_type=_resolvable_icon_hint(node),
                )
            max_bottom = max(max_bottom, gy + box_h)
            cursor_x = max(cursor_x, gx + box_w + _BLOCK_GAP_X)

        # Edge ids are minted here, so they must not collide with an id the
        # model already used: "edge_0" is a perfectly legal node id (_ID_RE
        # only requires a leading letter), and the collision silently rewired
        # the diagram — the user's node was renamed by resolve_duplicate_ids
        # and the edge ended up pointing at itself. Same collision-safe
        # prefix search elk_layout_network.connectivity_shelf_id already uses
        # for its own invented container id.
        edge_prefix = _free_prefix("edge", self.ir.all_ids())
        for index, edge in enumerate(self.ir.edges):
            self.w.edge(
                id=f"{edge_prefix}_{index}", source=edge.source, target=edge.target, label=edge.label,
                style=theme.edge_style(
                    line_style=edge.line_style, dashed=edge.dashed, arrow=edge.arrow, color=edge.color,
                ),
            )

        self.w.mark_layout_authoritative()
        page_w = max(cursor_x - _BLOCK_GAP_X + 200.0, 400.0)
        page_h = max_bottom + 200.0
        return self.w.to_xml(page_width=page_w, page_height=page_h)
