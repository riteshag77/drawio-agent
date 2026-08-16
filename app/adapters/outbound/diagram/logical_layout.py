"""Dependency-driven layered layout for focused architecture views.

Sugiyama-lite: rank containers by contracted leaf edges, order tiles inside
each container to minimise crossings, then write coordinates. Used by Logical
(and the same helpers for Security / Data flow corridors).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass

import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import elk_graph
from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.elk_bridge import run_elk
from app.domain.ir.base import Leaf
from app.domain.ir.corridor import Corridor, CorridorIR


# Canonical constants for the corridor/shelf layout engine (Logical / Security /
# Data-flow). Single source of truth for padding/gap/pitch in this compiler.
# Deliberately separate from geometry_spec.py's Network constants (icon SIZE is
# shared via geo.LEAF_W/LEAF_H; PADDING/PITCH here is corridor-layout-specific).
CONTAINER_PADDING_X = 24.0
CONTAINER_PADDING_Y = 24.0
CONTAINER_LABEL_HEIGHT = 30.0
#: Gap BETWEEN icon boxes, not a left-edge-to-left-edge pitch. Kept as a
#: named constant (matches the padding/gap set this engine was given), but it
#: is NOT what actually drives icon-to-icon pitch below — see _ICON_PITCH_X.
ICON_SPACING = 40.0
INTER_CONTAINER_GAP = 110.0
#: Wider L→R gutter for sparse Logical (process storyboards).
LOGICAL_INTER_CONTAINER_GAP = 140.0
#: Floor so a container never shrinks below room for its own title band —
#: mirrors xml_tools._FIT_MIN_W/_FIT_MIN_H's role for the general-container
#: fitter, kept as a separate constant since these are independent systems.
CONTAINER_MIN_W = 120.0
CONTAINER_MIN_H = 80.0

#: Pitch deliberately equals geometry_spec's own canonical leaf-grid pitch
#: (200 horizontal / 130 vertical row spacing), NOT ICON_SPACING + LEAF_W
#: (118px), so corridor grid sizing matches Network's DATA_CANONICAL_PITCH /
#: ROW_PITCH_Y and diagnose_xml's pitch-range warnings.
_ICON_PITCH_X = geo.DATA_CANONICAL_PITCH
_ICON_PITCH_Y = geo.ROW_PITCH_Y

#: Max icons per row before wrapping — unrelated to the padding/pitch
#: consolidation above (no duplicate elsewhere), stays under its own name.
APP_MAX_COLS = 4


def grid_fill(count: int, *, cols: int | None = None) -> tuple[int, int, float, float]:
    """(cols, rows, content_width, content_height) for `count` icons at the
    canonical ICON_SPACING/icon-size pitch, wrapping at grid_cols(count) columns
    unless `cols` is given explicitly. One formula, used everywhere a
    container needs to size itself around a grid of children — replaces the
    three independent grid/stacking formulas this module and edge_routing.py
    used to have."""
    if count <= 0:
        return 0, 0, 0.0, 0.0
    n_cols = cols if cols is not None else grid_cols(count)
    n_cols = max(1, n_cols)
    n_rows = (count - 1) // n_cols + 1
    used_cols = min(count, n_cols)
    content_w = (used_cols - 1) * _ICON_PITCH_X + geo.LEAF_W
    content_h = (n_rows - 1) * _ICON_PITCH_Y + geo.LEAF_H
    return n_cols, n_rows, content_w, content_h


def resize_container_to_grid(
    geom: ET.Element,
    count: int,
    *,
    cols: int | None = None,
    min_w: float = CONTAINER_MIN_W,
    min_h: float = CONTAINER_MIN_H,
) -> bool:
    """The one place a container/shelf box is sized from its child count.

    Bidirectional — shrinks an oversized box back down to what its `count`
    children actually need, grows an undersized one — unlike the grow-only
    checks (`if cur < needed: set(...)`) this replaces, which could never
    correct a box a *different* sizing pass had already made too large.

    `cols` forces a specific column count instead of `grid_cols(count)`'s
    squarish default — e.g. `cols=1` for a narrow single-column actor band,
    where wrapping into a square grid would defeat the point of a band.
    """
    _cols, _rows, content_w, content_h = grid_fill(count, cols=cols)
    needed_w = max(min_w, content_w + CONTAINER_PADDING_X * 2)
    needed_h = max(min_h, content_h + CONTAINER_LABEL_HEIGHT + CONTAINER_PADDING_Y)
    try:
        cur_w = float(geom.get("width") or 0)
        cur_h = float(geom.get("height") or 0)
    except ValueError:
        cur_w, cur_h = 0.0, 0.0
    changed = False
    if abs(needed_w - cur_w) > 1.0:
        geom.set("width", str(needed_w))
        changed = True
    if abs(needed_h - cur_h) > 1.0:
        geom.set("height", str(needed_h))
        changed = True
    return changed


def break_cycles(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop DFS back-edges so ranking always terminates."""
    adj: dict[str, list[str]] = defaultdict(list)
    for s, t in edges:
        adj[s].append(t)
    nodes = {n for e in edges for n in e}
    color: dict[str, int] = {n: 0 for n in nodes}  # 0=white 1=gray 2=black
    back: set[tuple[str, str]] = set()

    def dfs(u: str) -> None:
        color[u] = 1
        for v in adj.get(u, []):
            if color.get(v, 0) == 0:
                dfs(v)
            elif color.get(v) == 1:
                back.add((u, v))
        color[u] = 2

    for n in nodes:
        if color[n] == 0:
            dfs(n)
    if not back:
        return edges
    return [e for e in edges if e not in back]


def longest_path_ranks(
    nodes: list[str],
    edges: list[tuple[str, str]],
) -> dict[str, int]:
    """Longest-path layering on a DAG (sources at rank 0)."""
    dag = break_cycles(edges)
    preds: dict[str, list[str]] = defaultdict(list)
    succs: dict[str, list[str]] = defaultdict(list)
    for s, t in dag:
        if s in nodes and t in nodes:
            succs[s].append(t)
            preds[t].append(s)
    ranks: dict[str, int] = {n: 0 for n in nodes}
    # Kahn-style topological push of longest path
    indeg = {n: len(preds[n]) for n in nodes}
    queue = [n for n in nodes if indeg[n] == 0]
    seen = 0
    while queue:
        u = queue.pop(0)
        seen += 1
        for v in succs[u]:
            ranks[v] = max(ranks[v], ranks[u] + 1)
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    if seen < len(nodes):
        # Residual cycle — pin remaining at max rank
        mx = max(ranks.values(), default=0)
        for n in nodes:
            if indeg.get(n, 0) > 0:
                ranks[n] = mx
    return ranks


def barycenter_order(
    ranks: dict[str, int],
    edges: list[tuple[str, str]],
    sweeps: int = 3,
) -> dict[str, int]:
    """Return node → row index within its rank, minimising crossings."""
    by_rank: dict[int, list[str]] = defaultdict(list)
    for n, r in ranks.items():
        by_rank[r].append(n)
    for r in by_rank:
        by_rank[r].sort()

    adj_fwd: dict[str, list[str]] = defaultdict(list)
    adj_bwd: dict[str, list[str]] = defaultdict(list)
    for s, t in edges:
        if s in ranks and t in ranks:
            adj_fwd[s].append(t)
            adj_bwd[t].append(s)

    for _ in range(sweeps):
        # L→R
        sorted_ranks = sorted(by_rank.keys())
        pos = {n: i for r in sorted_ranks for i, n in enumerate(by_rank[r])}
        for r in sorted_ranks[1:]:
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_bwd[n], pos, n), n))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i
        # R→L
        for r in reversed(sorted_ranks[:-1] if len(sorted_ranks) > 1 else []):
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_fwd[n], pos, n), n))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i

    order: dict[str, int] = {}
    for _r, nodes in by_rank.items():
        for i, n in enumerate(nodes):
            order[n] = i
    return order


def _ccw(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> bool:
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def _segs_cross(
    p1: tuple[float, float], p2: tuple[float, float],
    p3: tuple[float, float], p4: tuple[float, float],
) -> bool:
    if p1 in (p3, p4) or p2 in (p3, p4):
        return False
    return _ccw(p1, p3, p4) != _ccw(p2, p3, p4) and _ccw(p1, p2, p3) != _ccw(p1, p2, p4)


def _count_rank_order_crossings(
    ranks: dict[str, int],
    order: dict[str, int],
    edges: list[tuple[str, str]],
) -> int:
    """Abstract crossing count treating (rank, order) as an (x, y) layout point.

    Cheap proxy used to pick the least-tangled candidate ordering *before*
    committing to pixel geometry — mirrors ``xml_diagnostics.count_edge_crossings``
    (same proper-crossing test) but works on the pre-layout rank/order graph so
    it can be evaluated many times per container without round-tripping XML.
    """
    pts: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for s, t in edges:
        if s in ranks and t in ranks and s in order and t in order:
            pts.append(((float(ranks[s]), float(order[s])), (float(ranks[t]), float(order[t]))))
    crossings = 0
    for i in range(len(pts)):
        a1, a2 = pts[i]
        for j in range(i + 1, len(pts)):
            b1, b2 = pts[j]
            if _segs_cross(a1, a2, b1, b2):
                crossings += 1
    return crossings


def barycenter_order_best(
    ranks: dict[str, int],
    edges: list[tuple[str, str]],
    *,
    sweeps: int = 4,
    restarts: int = 3,
    preferred: list[str] | None = None,
) -> dict[str, int]:
    """Run ``barycenter_order`` from a few different initial tie-breaks, keep the

    lowest-crossing result. ``barycenter_order`` alone always starts from the
    same id-ascending tie-break, so its sweeps converge to one local optimum;
    trying a couple of different starting permutations and scoring each with
    ``_count_rank_order_crossings`` cheaply escapes bad local optima without
    needing a real (and much slower) global crossing-minimisation algorithm.

    ``preferred`` is the caller's own meaningful order — for a corridor's
    leaves, the order the model declared them in. It replaces the id-ascending
    permutation as the first seed. Alphabetical order carries no information
    about the diagram; declaration order is a statement by the author, so when
    two seeds tie on crossings the sweeps should be converging from that one.
    Every seed is still scored the same way, so this only decides *which*
    equally-good result is kept.
    """
    nodes = list(ranks.keys())
    if not nodes:
        return {}
    first = [n for n in (preferred or []) if n in ranks] or sorted(nodes)
    if len(first) != len(nodes):  # `preferred` was partial — fill the rest
        first = first + [n for n in sorted(nodes) if n not in set(first)]
    seeds = [
        first,
        sorted(nodes, reverse=True),
    ]
    if restarts > 2:
        # Cheap third seed: shuffle deterministically by hash so it differs
        # from both sorted orders without importing `random` for one call.
        seeds.append(sorted(nodes, key=lambda n: (hash(n) & 0xFFFF, n)))
    seeds = seeds[: max(1, restarts)]

    tiebreak = {n: i for i, n in enumerate(preferred)} if preferred else None
    best_order: dict[str, int] | None = None
    best_score: int | None = None
    for seed in seeds:
        # Seed the rank buckets with this tie-break before sweeping.
        by_rank: dict[int, list[str]] = defaultdict(list)
        for n in seed:
            by_rank[ranks[n]].append(n)
        order = _barycenter_order_from_seed(by_rank, edges, sweeps, tiebreak)
        score = _count_rank_order_crossings(ranks, order, edges)
        if best_score is None or score < best_score:
            best_order, best_score = order, score
    return best_order if best_order is not None else barycenter_order(ranks, edges, sweeps)


def _barycenter_order_from_seed(
    by_rank: dict[int, list[str]],
    edges: list[tuple[str, str]],
    sweeps: int,
    tiebreak: dict[str, int] | None = None,
) -> dict[str, int]:
    """Same sweep logic as ``barycenter_order`` but from a caller-seeded bucket order.

    ``tiebreak`` maps a node to its position in the caller's own meaningful
    order (for a corridor, the order the model declared its leaves in). It is
    what two nodes with the same barycenter are separated by. Without it the
    separator is the node **id**, which is how a leaf could be placed by the
    spelling of its identifier.
    """
    adj_fwd: dict[str, list[str]] = defaultdict(list)
    adj_bwd: dict[str, list[str]] = defaultdict(list)
    ranked_nodes = {n for nodes in by_rank.values() for n in nodes}
    for s, t in edges:
        if s in ranked_nodes and t in ranked_nodes:
            adj_fwd[s].append(t)
            adj_bwd[t].append(s)
    keyed = _tiebreak_key(tiebreak)

    for _ in range(sweeps):
        sorted_ranks = sorted(by_rank.keys())
        pos = {n: i for r in sorted_ranks for i, n in enumerate(by_rank[r])}
        for r in sorted_ranks[1:]:
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_bwd[n], pos, n), keyed(n)))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i
        for r in reversed(sorted_ranks[:-1] if len(sorted_ranks) > 1 else []):
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_fwd[n], pos, n), keyed(n)))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i

    order: dict[str, int] = {}
    for _r, nodes in by_rank.items():
        for i, n in enumerate(nodes):
            order[n] = i
    return order


def _mean_pos(nbrs: list[str], pos: dict[str, int], node: str) -> float:
    """Mean row of ``node``'s neighbours — the barycenter it wants to sit at.

    A node with no neighbours in the adjacent rank keeps *its own* position
    rather than taking 0.0. Returning 0.0 pins every edgeless node to the top
    of its rank, which is how an unconnected leaf ended up leading a corridor
    ahead of the leaf that started the flow through it. Nothing is known about
    where such a node belongs, so the honest answer is "where it already is".
    """
    vals = [pos[n] for n in nbrs if n in pos]
    if vals:
        return sum(vals) / len(vals)
    return float(pos.get(node, 0))


def _tiebreak_key(tiebreak: dict[str, int] | None):
    """Separator for equal barycenters: the caller's order, else the id."""
    if tiebreak is None:
        return lambda n: n
    return lambda n: (tiebreak.get(n, len(tiebreak)), n)


def grid_cols(count: int) -> int:
    if count <= 3:
        return max(1, count)
    return min(APP_MAX_COLS, math.ceil(math.sqrt(count)))


# ── Typed-IR layout (compile-time, no XML round trip) ──────────────────────
#
# Everything below computes final geometry directly from a CorridorIR's own
# typed graph, reusing the same rank/order/grid-fill primitives above.
# Called once at compile time from compiler/corridor.py — no XML round-trip.

_ROW_START_X = 160.0
_ROW_START_Y = 140.0


@dataclass(frozen=True)
class Geo:
    x: float
    y: float
    w: float
    h: float


# Mirrors _domain_corridor_key/_zone_corridor_key/_phase_corridor_key's
# keyword-matching *outcomes*, but as an exact id lookup: every corridor id in
# these three views is a fixed, Pydantic-validated vocabulary (see
# domain/ir/{logical,security,data_flow}.py's `_ROW_IDS`), so there is no
# fuzzy label/id blob to match against — the exact-id table below already IS
# the classification for every id that can actually appear.
_ROW_ORDER: dict[str, dict[str, int]] = {
    "logical": {
        # L→R journey: Experience → Core/Commerce → Data → Platform
        # (SaaS is an anchored shelf under Data, not a row column).
        "domain_experience": 0, "domain_core": 1, "domain_commerce": 1,
        "domain_data": 2, "domain_platform": 3,
    },
    "security": {
        "zone_public": 0, "zone_dmz": 1, "zone_identity": 2, "zone_partner": 3,
        "zone_private": 4, "zone_restricted": 5, "zone_detect": 6,
    },
    "data_flow": {"phase_ingest": 0, "phase_process": 1, "phase_store": 2},
}


def _row_rank_key(view: str, corridor_id: str, label: str) -> tuple[int, str]:
    return (_ROW_ORDER.get(view, {}).get(corridor_id, 99), label)


def _tile_order(leaf_ids: list[str], local_edges: list[tuple[str, str]]) -> tuple[list[str], int]:
    """Reading-order list of `leaf_ids` + the column count to wrap them at —
    rank/barycenter logic for leaf grids inside a corridor.

    Declaration order (`leaf_ids` as given, i.e. the IR's own
    `corridor.leaves` list order) is the fallback in *both* branches. It used
    to be the fallback only when a corridor had no internal edges; with even
    one, ordering fell through to the leaf's **id text** — both as the
    barycenter seed and as the final sort tie-break. Measured on a corridor
    declared `[checkout, auth, cart]` with a single `checkout -> auth` edge,
    that put `cart` first: a leaf with no edges at all led the row because
    "ca" sorts before "ch". Alphabetical order says nothing about the
    diagram, and it silently overrode the one ordering statement the author
    actually made.

    Ranks and barycenter still lead, so genuine crossing reduction is
    unaffected — only ties change, and they now resolve to what the model
    said.
    """
    if local_edges and len(leaf_ids) > 1:
        declared = {lid: i for i, lid in enumerate(leaf_ids)}
        ranks = longest_path_ranks(leaf_ids, local_edges)
        order = barycenter_order_best(ranks, local_edges, preferred=leaf_ids)
        max_rank = max(ranks.values(), default=0) + 1
        cols_n = min(grid_cols(len(leaf_ids)), max(1, max_rank))
        ordered = sorted(
            leaf_ids,
            key=lambda lid: (ranks.get(lid, 0), order.get(lid, 0), declared[lid]),
        )
    else:
        ordered = list(leaf_ids)
        cols_n = grid_cols(len(leaf_ids))
    return ordered, cols_n


def _place_leaves(
    leaves: list[Leaf],
    local_edges: list[tuple[str, str]],
    geos: dict[str, Geo],
) -> tuple[float, float]:
    """Position `leaves` relative to their own (not-yet-placed) container —
    mxCell child geometry is always parent-relative, so this needs no
    knowledge of where the container itself ends up. Returns the container's
    own (width, height), the same grid_fill/resize_container_to_grid math
    Part A gave every corridor/shelf container."""
    leaf_ids = [leaf.id for leaf in leaves]
    ordered, cols_n = _tile_order(leaf_ids, local_edges)
    for i, lid in enumerate(ordered):
        col, row = i % cols_n, i // cols_n
        geos[lid] = Geo(
            x=CONTAINER_PADDING_X + col * _ICON_PITCH_X,
            y=CONTAINER_LABEL_HEIGHT + row * _ICON_PITCH_Y,
            w=geo.LEAF_W, h=geo.LEAF_H,
        )
    _cols, _rows, content_w, content_h = grid_fill(len(leaves), cols=cols_n)
    width = max(CONTAINER_MIN_W, content_w + CONTAINER_PADDING_X * 2)
    # + geo.LABEL_BAND_H: leaves render with verticalLabelPosition=bottom
    # (theme.leaf_style()), so their rendered footprint is icon + a 28px
    # label band beneath it — resize_container_to_grid's own formula doesn't
    # include this — leaves render with an under-icon label band, so container
    # height must cover icon + label, not icon alone.
    height = max(CONTAINER_MIN_H, content_h + geo.LABEL_BAND_H + CONTAINER_LABEL_HEIGHT + CONTAINER_PADDING_Y)
    return width, height


def _corridor_row_order(ir: CorridorIR) -> list[Corridor]:
    """Row corridors in canonical left-to-right order (``_ROW_ORDER``, edges
    from ``ir.flows`` only breaking ties within the same canonical slot)."""
    leaf_to_corridor: dict[str, str] = {
        leaf.id: corridor.id for corridor in ir.corridors for leaf in corridor.leaves
    }
    row_ids = {c.id for c in ir.corridors}
    seen_ce: set[tuple[str, str]] = set()
    c_edges: list[tuple[str, str]] = []
    for flow in ir.flows:
        cs, ct = leaf_to_corridor.get(flow.source), leaf_to_corridor.get(flow.target)
        if cs and ct and cs != ct and cs in row_ids and ct in row_ids:
            pair = (cs, ct)
            if pair not in seen_ce:
                seen_ce.add(pair)
                c_edges.append(pair)

    row = list(ir.corridors)
    if c_edges:
        ranks = longest_path_ranks(list(row_ids), c_edges)
        row.sort(key=lambda c: (
            _row_rank_key(ir.view, c.id, c.label)[0],
            ranks.get(c.id, 99),
            _row_rank_key(ir.view, c.id, c.label)[1],
        ))
    else:
        row.sort(key=lambda c: _row_rank_key(ir.view, c.id, c.label))
    return row


def _anchor_shelves(
    ir: CorridorIR, geos: dict[str, Geo], *, row_right: float, row_bottom: float,
) -> None:
    """Place every anchored shelf directly below its anchor (or the whole
    row), stacking shelves that share an anchor via a running cursor.

    A fixed spatial relationship ("this shelf sits below that corridor"), not
    a layout search problem — kept as this explicit step rather than folded
    into the ELK graph (see the module's ELK section docstring).

    Two passes, not one: named-anchor shelves (stacked under one real
    corridor) are placed first; "below the whole row" shelves (``anchor_id
    is None`` — only Data-flow's ``phase_control`` uses this today) are
    placed second, starting below the taller of the main row or any
    named-anchor shelf stack. A single pass keyed by ``shelf.anchor_id``
    used to place the ``None`` bucket from ``row_bottom`` alone, with zero
    awareness of how tall a same-run named-anchor stack (e.g. Data-flow's
    three ``phase_store`` shelves) had grown — confirmed to produce a real,
    reproducible sibling-container overlap once that stack out-grew the main
    row. "The whole row" has to mean the complete visual row including its
    own shelves, not just the main corridor boxes.
    """
    all_flows = [(f.source, f.target) for f in ir.flows]

    def _place(shelf, base_x: float, base_top: float) -> float:
        if not ir.flows_are_synthesized():
            shelf.leaves = order_leaves_by_flow(list(shelf.leaves), ir.flows)
        leaf_ids = {leaf.id for leaf in shelf.leaves}
        local_edges = [(s, t) for s, t in all_flows if s in leaf_ids and t in leaf_ids]
        width, height = _place_leaves(shelf.leaves, local_edges, geos)
        geos[shelf.id] = Geo(x=base_x, y=base_top, w=width, h=height)
        return base_top + height + INTER_CONTAINER_GAP

    cursor_by_anchor: dict[str, float] = {}
    whole_row_shelves: list = []
    for shelf in ir.anchored:
        if shelf.anchor_id is None:
            whole_row_shelves.append(shelf)
            continue
        anchor_geo = geos.get(shelf.anchor_id)
        base_x = anchor_geo.x if anchor_geo is not None else row_right
        default_top = (anchor_geo.y + anchor_geo.h if anchor_geo is not None else row_bottom) + INTER_CONTAINER_GAP
        base_top = cursor_by_anchor.get(shelf.anchor_id, default_top)
        cursor_by_anchor[shelf.anchor_id] = _place(shelf, base_x, base_top)

    floor = max([row_bottom, *(v - INTER_CONTAINER_GAP for v in cursor_by_anchor.values())], default=row_bottom)
    cursor = floor + INTER_CONTAINER_GAP
    for shelf in whole_row_shelves:
        cursor = _place(shelf, _ROW_START_X, cursor)


# ── ELK-driven layout (node placement + edge routing in one call) ─────────
#
# ELK owns the row corridors' order/position/size and the routes of flows
# between leaves in DIFFERENT row corridors — the genuine graph-layout/
# routing problem, and the one the old rank/barycenter engine + placement_
# fixers.py's local router kept needing hand patches for. Anchored shelves
# stay on ``_anchor_shelves``'s explicit placement (see its docstring) since
# "below a named corridor" is a fixed relationship, not something ELK needs
# to solve — so flows touching a shelf leaf are not in the ELK graph at all
# and keep falling through to edge_routing.py's existing local router,
# exactly as they do today (see its ``is_ir_layout`` guard: it only skips
# edges that already carry ELK-supplied points).
#
# Two things confirmed empirically before writing this (see the top-level
# migration plan): (1) ELK cannot keep given node positions fixed while only
# routing edges — the whole connected subgraph must be laid out by one
# recursive algorithm instance; (2) leaves with no edges between them (the
# common case *within* one corridor) don't wrap into a neat multi-column
# grid under the `layered` algorithm on their own. Handing that wrap to ELK
# was tried and did not work (see `_build_elk_corridor_graph`): the leaves
# are now gridded deterministically by `_place_leaves` — the same call the
# anchored shelves already use — and each corridor enters ELK as a single
# pre-sized box, so ELK only ever decides corridor placement and the routing
# lanes between corridors.

def _elk_corridor_node(
    corridor: Corridor, partition: int, size: tuple[float, float],
) -> dict:
    return dict(
        elk_graph.sized_container_node(corridor.id, *size),
        layoutOptions={"elk.partitioning.partition": str(partition)},
    )


def _build_elk_corridor_graph(
    row: list[Corridor],
    row_edges: list[tuple[str, str, str]],
    sizes: dict[str, tuple[float, float]],
    leaf_to_corridor: dict[str, str],
    *,
    view: str = "",
) -> dict:
    """``row_edges`` are ``(flow_id, source_leaf_id, target_leaf_id)`` for
    flows whose endpoints are both leaves of a row corridor (the only edges
    this graph needs to know about — see the module section docstring).

    Corridors enter as **pre-sized, childless** boxes (``sizes``, computed by
    ``_place_leaves``) and the leaf-level ``row_edges`` are collapsed onto
    their owning corridors. Leaves are deliberately NOT ELK children: a
    corridor's internal grid is a fixed 4-up wrap, not a layout search, and
    handing it to ELK actively broke it. ``elk.hierarchyHandling:
    INCLUDE_CHILDREN`` (needed for leaf-level cross-corridor edges) makes one
    algorithm instance lay out the whole hierarchy in the ROOT's direction,
    so each corridor's own ``elk.direction: DOWN`` + partitioned rows never
    applied — measured: 8 leaves rendered as a 126×1784 single column with
    scrambled order instead of 526×420, and ``_equalize_row_band`` then
    propagated that height to every row (worst on Security's 7 zones).
    Removing the leaf level removes the direction conflict outright, which is
    the same resolution ``compiler/network.py`` already documents for the
    same ELK limitation (kieler/elkjs#26).
    """
    gap = LOGICAL_INTER_CONTAINER_GAP if view == "logical" else INTER_CONTAINER_GAP
    seen: set[tuple[str, str]] = set()
    edges: list[dict] = []
    for fid, src, tgt in row_edges:
        pair = (leaf_to_corridor[src], leaf_to_corridor[tgt])
        # Keep one edge per corridor pair: ELK is now routing lanes between
        # boxes, and N parallel leaf edges between the same two corridors
        # would each claim their own lane and splay the columns apart.
        if pair in seen:
            continue
        seen.add(pair)
        edges.append({"id": fid, "sources": [pair[0]], "targets": [pair[1]]})
    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "RIGHT",
            "elk.edgeRouting": "ORTHOGONAL",
            "elk.partitioning.activate": "true",
            # Between L→R corridor columns (layers), not same-layer siblings.
            "elk.layered.spacing.nodeNodeBetweenLayers": str(gap),
            "elk.spacing.nodeNode": str(gap),
            "elk.padding": f"[top={_ROW_START_Y},left={_ROW_START_X},bottom=40,right=40]",
            **elk_graph.MODEL_ORDER_OPTIONS,
        },
        "children": [_elk_corridor_node(c, i, sizes[c.id]) for i, c in enumerate(row)],
        "edges": edges,
    }


def _parse_elk_corridor_result(
    result: dict, row: list[Corridor], geos: dict[str, Geo],
) -> tuple[dict[str, Geo], dict[str, list[tuple[float, float]]]]:
    """Record each corridor's ELK-decided box into ``geos``.

    Leaf entries are already in ``geos`` — ``_place_leaves`` put them there,
    parent-relative, before the graph was built — so this only fills in the
    corridor boxes themselves and never touches a leaf.
    """
    for corridor in row:
        cnode = elk_graph.require_child(result, corridor.id)
        geos[corridor.id] = Geo(x=cnode["x"], y=cnode["y"], w=cnode["width"], h=cnode["height"])
    return geos, elk_graph.edge_routes(result)


def _equalize_row_band(geos: dict[str, Geo], row: list[Corridor]) -> dict[str, float]:
    """Force every row corridor onto one top-Y / height band.

    ELK's layered placement barycenters compound nodes vertically so ports
    on a tall Core line up with a short Experience/Data — which reads as a
    staircase, not a corridor row. The legacy XML path
    the legacy XML layered pass already forced this band; the IR
    path must do the same or Logical/Security look "not auto-aligned".

    Returns ``{corridor_id: dy}`` for each row corridor whose Y changed
    (empty when already aligned) so callers can shift or drop ELK bend points
    that were computed against the pre-snap coordinates.
    """
    if not row:
        return {}
    band_h = max(geos[c.id].h for c in row)
    dys: dict[str, float] = {}
    for corridor in row:
        g = geos[corridor.id]
        dy = _ROW_START_Y - g.y
        if abs(dy) > 1.0 or g.h < band_h - 1.0:
            if abs(dy) > 1.0:
                dys[corridor.id] = dy
            geos[corridor.id] = Geo(x=g.x, y=_ROW_START_Y, w=g.w, h=band_h)
    return dys


def _shift_routes_after_equalize(
    routes: dict[str, list[tuple[float, float]]],
    dys: dict[str, float],
    row_edges: list[tuple[str, str, str]],
    leaf_to_corridor: dict[str, str],
) -> dict[str, list[tuple[float, float]]]:
    """Keep ELK bend points when both endpoints moved by the same dy.

    Asymmetric moves (short Experience slid up, tall Core stayed) used to
    drop *every* route. That threw away good Core↔Data elbows. Only discard
    a route when its two corridors disagree on dy — those elbows would cut
    through icons if naively shifted.
    """
    if not routes:
        return routes
    if not dys:
        return routes
    edge_corr = {
        fid: (leaf_to_corridor.get(src, ""), leaf_to_corridor.get(tgt, ""))
        for fid, src, tgt in row_edges
    }
    kept: dict[str, list[tuple[float, float]]] = {}
    for fid, points in routes.items():
        cs, ct = edge_corr.get(fid, ("", ""))
        dy_s = dys.get(cs, 0.0)
        dy_t = dys.get(ct, 0.0)
        if abs(dy_s - dy_t) > 1.0:
            continue
        dy = dy_s
        if abs(dy) <= 1.0:
            kept[fid] = points
        else:
            kept[fid] = [(x, y + dy) for x, y in points]
    return kept


def _longest_primary_chain(idset: set[str], flows: list) -> list[str]:
    """Longest ``primary=True`` chain that intersects ``idset`` (ids in set only)."""
    primary = [
        (f.source, f.target) for f in flows
        if getattr(f, "primary", True)
        and f.source in idset and f.target in idset
    ]
    if not primary:
        # Soft: any edge that enters/leaves the set along a primary hop.
        primary = [
            (f.source, f.target) for f in flows
            if getattr(f, "primary", True)
            and (f.source in idset or f.target in idset)
        ]
        primary = [(s, t) for s, t in primary if s in idset and t in idset]
    if not primary:
        return []
    succ: dict[str, list[str]] = defaultdict(list)
    nodes: set[str] = set()
    for s, t in primary:
        succ[s].append(t)
        nodes.add(s)
        nodes.add(t)
    best: list[str] = []

    def dfs(node: str, path: list[str], seen: set[str]) -> None:
        nonlocal best
        if len(path) > len(best):
            best = list(path)
        for nxt in succ.get(node, ()):
            if nxt in seen:
                continue
            seen.add(nxt)
            path.append(nxt)
            dfs(nxt, path, seen)
            path.pop()
            seen.remove(nxt)

    for start in nodes:
        dfs(start, [start], {start})
    return best


def _spine_before_wrap(ordered_ids: list[str], flows: list) -> list[str]:
    """Prefer longest primary path on row 0; secondary leaves fill lower rows.

    Density threshold = ``LEAF_GRID_MAX_COLS``: if the spine fits in one row,
    it occupies the front of the list (ELK partition / emit_leaf_grid wrap).
    """
    idset = set(ordered_ids)
    spine = [lid for lid in _longest_primary_chain(idset, flows) if lid in idset]
    if len(spine) < 2:
        return ordered_ids
    spine_set = set(spine)
    rest = [lid for lid in ordered_ids if lid not in spine_set]
    # Keep spine contiguous at the front so wrap puts them on earlier rows.
    max_cols = elk_graph.LEAF_MAX_COLS
    if len(spine) <= max_cols:
        return spine + rest
    # Over-long spine still leads; wrap naturally after max_cols.
    return spine + rest


def order_leaves_by_flow(leaves: list[Leaf], flows: list) -> list[Leaf]:
    """Reorder leaves for cleaner L→R / top→bottom wiring.

    Uses intra-container edges when present (longest-path + barycenter).
    Otherwise prefers primary-path entry leaves (inbound from outside) then
    exit leaves (outbound), preserving relative declaration order as a tiebreak.
    Then applies spine-before-wrap so the longest primary path prefers row 0.
    Shared by corridor row ELK grids and Network ``emit_leaf_grid``.
    """
    if len(leaves) <= 1:
        return list(leaves)
    ids = [leaf.id for leaf in leaves]
    idset = set(ids)
    local = [
        (f.source, f.target) for f in flows
        if getattr(f, "source", None) in idset and getattr(f, "target", None) in idset
    ]
    if not local:
        # Soft primary-path bias when the container itself has no internal edges.
        primary = [f for f in flows if getattr(f, "primary", True)]
        use = primary or list(flows)
        inbound = {
            f.target for f in use
            if f.target in idset and f.source not in idset
        }
        outbound = {
            f.source for f in use
            if f.source in idset and f.target not in idset
        }
        ordered_ids = sorted(
            ids,
            key=lambda lid: (
                0 if lid in inbound else 1,
                0 if lid in outbound else 1,
                ids.index(lid),
            ),
        )
    else:
        ordered_ids, _ = _tile_order(ids, local)
    ordered_ids = _spine_before_wrap(ordered_ids, flows)
    by_id = {leaf.id: leaf for leaf in leaves}
    return [by_id[i] for i in ordered_ids if i in by_id]


def align_leaves_across_containers(
    containers: list[list[Leaf]],
    flows: list,
    *,
    sweeps: int = 4,
) -> list[list[Leaf]]:
    """Barycenter leaf list positions so primary edges prefer matching row indices.

    ``containers`` is an ordered sequence of adjacent corridors / Network tiers.
    Each list is reordered (and returned) so index // max_cols (row) tends to
    match across a primary hop. Uses alternating forward/backward sweeps so
    the two sides do not oscillate.
    """
    if len(containers) < 2:
        return containers
    max_cols = elk_graph.LEAF_MAX_COLS
    id_to_ci: dict[str, int] = {}
    for ci, leaves in enumerate(containers):
        for leaf in leaves:
            id_to_ci[leaf.id] = ci

    primary = [
        (f.source, f.target) for f in flows
        if getattr(f, "primary", True)
        and f.source in id_to_ci and f.target in id_to_ci
        and id_to_ci[f.source] != id_to_ci[f.target]
    ]
    if not primary:
        return containers

    orders: list[list[str]] = [[leaf.id for leaf in leaves] for leaves in containers]
    by_id = {leaf.id: leaf for leaves in containers for leaf in leaves}

    def row_of(ci: int, lid: str) -> float:
        try:
            return orders[ci].index(lid) / max_cols
        except ValueError:
            return 0.0

    def reorder(ci: int, neighbor: int) -> None:
        pref: dict[str, list[float]] = defaultdict(list)
        for s, t in primary:
            cs, ct = id_to_ci[s], id_to_ci[t]
            if cs == ci and ct == neighbor:
                pref[s].append(row_of(neighbor, t))
            elif ct == ci and cs == neighbor:
                pref[t].append(row_of(neighbor, s))
        if not pref:
            return
        order = orders[ci]

        def key(lid: str) -> tuple:
            if lid in pref:
                return (sum(pref[lid]) / len(pref[lid]), order.index(lid))
            return (row_of(ci, lid), order.index(lid))

        orders[ci] = sorted(order, key=key)

    for _ in range(sweeps):
        for ci in range(1, len(orders)):
            reorder(ci, ci - 1)
        for ci in range(len(orders) - 2, -1, -1):
            reorder(ci, ci + 1)

    return [[by_id[i] for i in order if i in by_id] for order in orders]


def actor_sort_key_by_endpoint_y(
    actor_id: str,
    flows: list,
    endpoint_cy: dict[str, float],
    *,
    band_lo: float,
    band_hi: float,
    fallback_index: int = 0,
) -> tuple[float, int, str]:
    """Sort key: median absolute Y of connected primary endpoints, clamped to band."""
    primary = [f for f in flows if getattr(f, "primary", True)] or list(flows)
    ys: list[float] = []
    for f in primary:
        if f.source == actor_id and f.target in endpoint_cy:
            ys.append(endpoint_cy[f.target])
        elif f.target == actor_id and f.source in endpoint_cy:
            ys.append(endpoint_cy[f.source])
    if ys:
        mid = sorted(ys)[len(ys) // 2]
        mid = max(band_lo, min(band_hi, mid))
    else:
        mid = band_lo + fallback_index * geo.ROW_PITCH_Y
    return (mid, fallback_index, actor_id)


def _reorder_row_leaves(row: list[Corridor], flows: list) -> None:
    """Mutate each row corridor's ``leaves`` into flow-aware order before ELK."""
    ordered = [order_leaves_by_flow(list(corridor.leaves), flows) for corridor in row]
    aligned = align_leaves_across_containers(ordered, flows)
    for corridor, leaves in zip(row, aligned):
        corridor.leaves = leaves


def layout_corridor_ir(ir: CorridorIR) -> tuple[dict[str, Geo], dict[str, list[tuple[float, float]]]]:
    """Compute final geometry and edge routes for every corridor/shelf/leaf
    in ``ir``, via ELK. Returns ``({id: Geo}, {flow_id: [(x,y),...]})`` —
    corridor/shelf entries are absolute (``parent="1"``), leaf entries are
    relative to their own corridor/shelf (mxCell's own parent-relative
    child-geometry convention); routes are intermediate bend points for
    ``compiler/shared.emit_flows``, only present for flows ELK actually
    routed (row-to-row; see the ELK section docstring above). External
    actors are positioned separately by ``compiler/shared.py``.
    """
    row = _corridor_row_order(ir)
    if not row:
        geos: dict[str, Geo] = {}
        _anchor_shelves(ir, geos, row_right=_ROW_START_X, row_bottom=_ROW_START_Y)
        return geos, {}

    # Flow-aware leaf ordering, skipped when the flows are this app's own
    # invention. This is the ordering that Logical actually ships: it rewrites
    # `corridor.leaves` in place, and `_emit_logical_flat` builds the flat
    # storyboard straight off that list order. See IRModel.flows_are_synthesized.
    if not ir.flows_are_synthesized():
        _reorder_row_leaves(row, ir.flows)

    leaf_to_corridor: dict[str, str] = {
        leaf.id: corridor.id for corridor in row for leaf in corridor.leaves
    }
    row_ids = {c.id for c in row}

    row_edges: list[tuple[str, str, str]] = []
    for index, flow in enumerate(ir.flows):
        cs = leaf_to_corridor.get(flow.source)
        ct = leaf_to_corridor.get(flow.target)
        # Only cross-corridor flows are this graph's concern — a same-corridor
        # edge (e.g. two leaves in the same domain) would otherwise become a
        # real dependency edge inside that corridor's own internal layout,
        # skewing its leaf spacing away from the intended partitioned grid. A
        # leaf may still have OTHER edges that fall outside this graph (into a
        # shelf, an actor, or a same-corridor sibling) — that's fine; the two
        # routers meeting at one leaf is handled at the point they actually
        # interact (edge_routing.fix_edge_anchors_from_geometry excludes
        # ELK-routed edges from its own shared-face offset bucketing, so a
        # leftover legacy-routed edge never has its offset computed against a
        # slot the ELK edge doesn't actually occupy). An earlier, more
        # conservative version excluded a leaf's entire edge set from ELK
        # whenever any one of them touched a shelf — correct for a small
        # fixture, but on a real hub leaf (many flows, one of them to a
        # shelf) that meant almost nothing got ELK-routed at all.
        if cs in row_ids and ct in row_ids and cs != ct:
            row_edges.append((f"flow_{index}", flow.source, flow.target))

    # Grid each corridor's leaves first (parent-relative) so ELK receives a
    # correctly-sized box per corridor — see _build_elk_corridor_graph for
    # why the leaves are kept out of the ELK graph entirely.
    geos: dict[str, Geo] = {}
    sizes: dict[str, tuple[float, float]] = {}
    all_flow_pairs = [(f.source, f.target) for f in ir.flows]
    for corridor in row:
        own = {leaf.id for leaf in corridor.leaves}
        local_edges = [(s, t) for s, t in all_flow_pairs if s in own and t in own]
        sizes[corridor.id] = _place_leaves(corridor.leaves, local_edges, geos)

    graph = _build_elk_corridor_graph(
        row, row_edges, sizes, leaf_to_corridor, view=ir.view,
    )
    result = run_elk(graph)
    geos, routes = _parse_elk_corridor_result(result, row, geos)

    # Snap the row to one Y/H band. Shift bend points when both endpoints
    # moved together; drop only asymmetrically moved routes (local router
    # re-anchors those).
    dys = _equalize_row_band(geos, row)
    if dys:
        routes = _shift_routes_after_equalize(routes, dys, row_edges, leaf_to_corridor)

    # Soft absolute-Y nudge so primary cross-corridor hops share a band.
    # Skipped when the flows are this app's own invention: the nudge pulls a
    # leaf toward whatever it connects to in the next corridor, which is a
    # crossing reduction when the model described those calls and a scramble of
    # the declared order when it didn't. See IRModel.flows_are_synthesized.
    if not ir.flows_are_synthesized():
        _nudge_primary_leaf_bands(geos, row, ir.flows)

    row_right = max(g.x + g.w for cid, g in geos.items() if cid in row_ids)
    row_bottom = max(g.y + g.h for cid, g in geos.items() if cid in row_ids)
    _anchor_shelves(ir, geos, row_right=row_right, row_bottom=row_bottom)
    return geos, routes


def _nudge_primary_leaf_bands(
    geos: dict[str, Geo],
    row: list[Corridor],
    flows: list,
    *,
    sweeps: int = 3,
) -> None:
    """Nudge relative leaf Y toward median absolute Y of primary neighbours.

    Keeps discrete row slots inside each corridor (no overlaps): after computing
    preferred absolute centers, leaves are reassigned to existing row Y values
    in preference order.
    """
    leaf_to_c = {leaf.id: c for c in row for leaf in c.leaves}
    primary = [
        f for f in flows
        if getattr(f, "primary", True)
        and f.source in leaf_to_c and f.target in leaf_to_c
        and leaf_to_c[f.source].id != leaf_to_c[f.target].id
    ]
    if not primary:
        return

    def abs_cy(lid: str) -> float:
        c = leaf_to_c[lid]
        g = geos[lid]
        return geos[c.id].y + g.y + g.h / 2.0

    for _ in range(sweeps):
        pref: dict[str, list[float]] = defaultdict(list)
        for f in primary:
            pref[f.source].append(abs_cy(f.target))
            pref[f.target].append(abs_cy(f.source))
        for corridor in row:
            leaves = [leaf for leaf in corridor.leaves if leaf.id in geos]
            if len(leaves) <= 1:
                continue
            slot_ys = sorted({round(geos[leaf.id].y, 1) for leaf in leaves})
            if len(slot_ys) <= 1:
                continue

            def pref_abs(leaf: Leaf) -> float:
                if leaf.id in pref:
                    return sum(pref[leaf.id]) / len(pref[leaf.id])
                return abs_cy(leaf.id)

            # Group by column (x); within each column assign ascending slots by preference.
            by_x: dict[float, list[Leaf]] = defaultdict(list)
            for leaf in leaves:
                by_x[round(geos[leaf.id].x, 1)].append(leaf)
            for col_leaves in by_x.values():
                col_leaves.sort(key=lambda leaf: (pref_abs(leaf), leaf.id))
                for i, leaf in enumerate(col_leaves):
                    slot = slot_ys[min(i, len(slot_ys) - 1)]
                    g = geos[leaf.id]
                    if abs(g.y - slot) > 0.5:
                        geos[leaf.id] = Geo(x=g.x, y=slot, w=g.w, h=g.h)
