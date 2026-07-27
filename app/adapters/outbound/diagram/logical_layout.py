"""Dependency-driven layered layout for focused architecture views.

Sugiyama-lite: rank containers by contracted leaf edges, order tiles inside
each container to minimise crossings, then write coordinates. Used by Logical
(and the same helpers for Security / Data flow corridors).
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable

import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.xml_diagnostics import (
    _cell_geometry,
    _is_container_cell,
)


# Canonical constants for the corridor/shelf layout engine (Logical / Security /
# Data-flow). Single source of truth — was previously three independent,
# grow-only sizing formulas with three different pitch values (APP_PITCH_Y=150,
# a hardcoded 110 in _anchor_shelf_under, _SAAS_PITCH_Y=110 in
# placement_fixers.py) that never agreed with each other, which is the root
# cause an oversized/inconsistent box (e.g. a 3-icon shelf rendering far
# larger than its content) was possible in the first place. Deliberately
# separate from geometry_spec.py's own constants (icon SIZE is shared — see
# the geo.LEAF_W/LEAF_H reuse below — but PADDING/PITCH is not: that module
# governs the Network IR compiler's load-bearing geometry, this one governs a
# downstream rank-and-order engine that always overwrites whatever geometry it
# receives, a genuinely different concern).
CONTAINER_PADDING_X = 24.0
CONTAINER_PADDING_Y = 24.0
CONTAINER_LABEL_HEIGHT = 30.0
#: Gap BETWEEN icon boxes, not a left-edge-to-left-edge pitch. Kept as a
#: named constant (matches the padding/gap set this engine was given), but it
#: is NOT what actually drives icon-to-icon pitch below — see _ICON_PITCH_X.
ICON_SPACING = 40.0
INTER_CONTAINER_GAP = 110.0
#: Floor so a container never shrinks below room for its own title band —
#: mirrors xml_tools._FIT_MIN_W/_FIT_MIN_H's role for the general-container
#: fitter, kept as a separate constant since these are independent systems.
CONTAINER_MIN_W = 120.0
CONTAINER_MIN_H = 80.0

#: Pitch deliberately equals geometry_spec's own canonical leaf-grid pitch
#: (200 horizontal / 130 vertical row spacing), NOT ICON_SPACING + LEAF_W
#: (118px). xml_diagnostics.py's resolve_pitch_outliers/
#: resolve_moderate_pitch_deviations run unconditionally, for every
#: container in every view (shared with Network), and snap any pitch outside
#: DATA_PITCH_RANGE (170-230) back to DATA_CANONICAL_PITCH (200) — so a
#: bespoke 118px pitch here would just get overwritten downstream anyway,
#: leaving this module's own sizing (which ran first, at 118px) out of sync
#: with what the page actually renders at (200px), which is what produced an
#: oversized/inconsistent shelf box in practice. Matching the canonical pitch
#: here means this module's sizing and the downstream fixers agree by
#: construction — confirmed with the user rather than silently decided.
_ICON_PITCH_X = geo.DATA_CANONICAL_PITCH
_ICON_PITCH_Y = geo.ROW_PITCH_Y

#: Max icons per row before wrapping — unrelated to the padding/pitch
#: consolidation above (no duplicate elsewhere), stays under its own name.
APP_MAX_COLS = 4

#: ``SHELF_GAP`` name kept — still imported by ``placement_fixers.py``'s
#: corridor-gap-consistency fixer; same value as ``INTER_CONTAINER_GAP``.
SHELF_GAP = INTER_CONTAINER_GAP


def grid_fill(count: int, *, cols: int | None = None) -> tuple[int, int, float, float]:
    """(cols, rows, content_width, content_height) for `count` icons at the
    canonical ICON_SPACING/icon-size pitch, wrapping at grid_cols(count) columns
    unless `cols` is given explicitly. One formula, used everywhere a
    container needs to size itself around a grid of children — replaces the
    three independent grid/stacking formulas this module and placement_fixers.py
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


def build_edge_pairs(cells: list[ET.Element]) -> list[tuple[str, str]]:
    """Unique directed edges, no self-loops."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for c in cells:
        if c.get("edge") != "1":
            continue
        src, tgt = c.get("source") or "", c.get("target") or ""
        if not src or not tgt or src == tgt:
            continue
        pair = (src, tgt)
        if pair in seen:
            continue
        seen.add(pair)
        out.append(pair)
    return out


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

    def _mean_pos(nbrs: list[str], pos: dict[str, int]) -> float:
        vals = [pos[n] for n in nbrs if n in pos]
        return sum(vals) / len(vals) if vals else 0.0

    for _ in range(sweeps):
        # L→R
        sorted_ranks = sorted(by_rank.keys())
        pos = {n: i for r in sorted_ranks for i, n in enumerate(by_rank[r])}
        for r in sorted_ranks[1:]:
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_bwd[n], pos), n))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i
        # R→L
        for r in reversed(sorted_ranks[:-1] if len(sorted_ranks) > 1 else []):
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_fwd[n], pos), n))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i

    order: dict[str, int] = {}
    for r, nodes in by_rank.items():
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
) -> dict[str, int]:
    """Run ``barycenter_order`` from a few different initial tie-breaks, keep the

    lowest-crossing result. ``barycenter_order`` alone always starts from the
    same id-ascending tie-break, so its sweeps converge to one local optimum;
    trying a couple of different starting permutations and scoring each with
    ``_count_rank_order_crossings`` cheaply escapes bad local optima without
    needing a real (and much slower) global crossing-minimisation algorithm.
    """
    nodes = list(ranks.keys())
    if not nodes:
        return {}
    seeds = [
        sorted(nodes),
        sorted(nodes, reverse=True),
    ]
    if restarts > 2:
        # Cheap third seed: shuffle deterministically by hash so it differs
        # from both sorted orders without importing `random` for one call.
        seeds.append(sorted(nodes, key=lambda n: (hash(n) & 0xFFFF, n)))
    seeds = seeds[: max(1, restarts)]

    best_order: dict[str, int] | None = None
    best_score: int | None = None
    for seed in seeds:
        # Seed the rank buckets with this tie-break before sweeping.
        by_rank: dict[int, list[str]] = defaultdict(list)
        for n in seed:
            by_rank[ranks[n]].append(n)
        order = _barycenter_order_from_seed(by_rank, edges, sweeps)
        score = _count_rank_order_crossings(ranks, order, edges)
        if best_score is None or score < best_score:
            best_order, best_score = order, score
    return best_order if best_order is not None else barycenter_order(ranks, edges, sweeps)


def _barycenter_order_from_seed(
    by_rank: dict[int, list[str]],
    edges: list[tuple[str, str]],
    sweeps: int,
) -> dict[str, int]:
    """Same sweep logic as ``barycenter_order`` but from a caller-seeded bucket order."""
    adj_fwd: dict[str, list[str]] = defaultdict(list)
    adj_bwd: dict[str, list[str]] = defaultdict(list)
    ranked_nodes = {n for nodes in by_rank.values() for n in nodes}
    for s, t in edges:
        if s in ranked_nodes and t in ranked_nodes:
            adj_fwd[s].append(t)
            adj_bwd[t].append(s)

    def _mean_pos(nbrs: list[str], pos: dict[str, int]) -> float:
        vals = [pos[n] for n in nbrs if n in pos]
        return sum(vals) / len(vals) if vals else 0.0

    for _ in range(sweeps):
        sorted_ranks = sorted(by_rank.keys())
        pos = {n: i for r in sorted_ranks for i, n in enumerate(by_rank[r])}
        for r in sorted_ranks[1:]:
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_bwd[n], pos), n))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i
        for r in reversed(sorted_ranks[:-1] if len(sorted_ranks) > 1 else []):
            by_rank[r].sort(key=lambda n: (_mean_pos(adj_fwd[n], pos), n))
            for i, n in enumerate(by_rank[r]):
                pos[n] = i

    order: dict[str, int] = {}
    for _r, nodes in by_rank.items():
        for i, n in enumerate(nodes):
            order[n] = i
    return order


def grid_cols(count: int) -> int:
    if count <= 3:
        return max(1, count)
    return min(APP_MAX_COLS, math.ceil(math.sqrt(count)))


def _is_saas_domain(cell: ET.Element) -> bool:
    cid = (cell.get("id") or "").lower()
    return cid.startswith("domain_saas") or (
        "saas" in cid and cid.startswith("domain_")
    )


def _is_data_domain(cell: ET.Element) -> bool:
    cid = (cell.get("id") or "").lower()
    label = (cell.get("value") or "").lower()
    blob = f"{cid} {label}"
    return "data" in blob and "saas" not in blob


def _is_consume_phase(cell: ET.Element) -> bool:
    """Data-flow's Consume phase — anchored below Store, not a 4th L→R column."""
    cid = (cell.get("id") or "").lower()
    return cid.startswith("phase_consume")


def _is_saas_phase(cell: ET.Element) -> bool:
    """Data-flow's dedicated SaaS shelf — vertical stack, anchored below Store."""
    cid = (cell.get("id") or "").lower()
    return cid.startswith("phase_saas")


def _is_data_flow_shelf(cell: ET.Element) -> bool:
    """Either Data-flow shelf (Consume or SaaS) — both anchor under phase_store."""
    return _is_consume_phase(cell) or _is_saas_phase(cell)


def _is_store_phase(cell: ET.Element) -> bool:
    cid = (cell.get("id") or "").lower()
    label = (cell.get("value") or "").lower()
    blob = f"{cid} {label}"
    return "store" in blob and "consume" not in blob


def _is_external_saas_zone(cell: ET.Element) -> bool:
    """Security's External SaaS zone — anchored below Restricted, not the rightmost column."""
    cid = (cell.get("id") or "").lower()
    return cid.startswith("zone_external_saas") or (
        "saas" in cid and cid.startswith("zone_")
    )


def _is_restricted_zone(cell: ET.Element) -> bool:
    cid = (cell.get("id") or "").lower()
    label = (cell.get("value") or "").lower()
    blob = f"{cid} {label}"
    return "restricted" in blob or "sensitive" in blob


def leaf_container_id(cell: ET.Element, by_id: dict[str, ET.Element]) -> str | None:
    """Immediate domain/zone/phase parent for a leaf, if any."""
    pid = cell.get("parent") or ""
    parent = by_id.get(pid)
    if parent is None:
        return None
    if _is_container_cell(parent):
        return parent.get("id")
    return None


def apply_layered_corridor_layout(
    cells: list[ET.Element],
    *,
    corridors: list[ET.Element],
    leaf_filter: Callable[[ET.Element], bool],
    keyword_key: Callable[[ET.Element], tuple],
    start_x: float = 160.0,
    start_y: float = 140.0,
    gap: float = INTER_CONTAINER_GAP,
    mark_layout: bool = True,
    shelf_filter: Callable[[ET.Element], bool] = _is_saas_domain,
    shelf_anchor_filter: Callable[[ET.Element], bool] = _is_data_domain,
) -> bool:
    """Rank corridors by leaf edges, lay tiles by barycenter, write geometry.

    ``shelf_filter`` identifies corridors excluded from the L→R rank and instead
    anchored directly below whichever ranked corridor matches ``shelf_anchor_filter``
    (falling back to the rightmost ranked corridor) — e.g. Logical's ``domain_saas``
    under ``domain_data`` (the defaults), or Data-flow's ``phase_consume`` under
    ``phase_store``. Corridor x/height/tile geometry is always **recomputed** here —
    whatever the LLM emitted for these is a discardable hint, not ground truth, since
    a hand-guessed layout is exactly what produces the crossing/misalignment defects
    this engine fixes.
    """
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    ranked = [c for c in corridors if not shelf_filter(c)]
    saas_shelves = [c for c in corridors if shelf_filter(c)]
    if not ranked and not saas_shelves:
        return False

    leaves = [c for c in cells if leaf_filter(c)]
    leaf_ids = {c.get("id") for c in leaves if c.get("id")}
    edges = [
        (s, t) for s, t in build_edge_pairs(cells)
        if s in leaf_ids and t in leaf_ids
    ]

    # Contract leaf edges → container edges
    leaf_to_container: dict[str, str] = {}
    for leaf in leaves:
        lid = leaf.get("id") or ""
        cid = leaf_container_id(leaf, by_id)
        if cid:
            leaf_to_container[lid] = cid

    container_ids = [c.get("id") or "" for c in ranked if c.get("id")]
    c_edges: list[tuple[str, str]] = []
    seen_ce: set[tuple[str, str]] = set()
    for s, t in edges:
        cs, ct = leaf_to_container.get(s), leaf_to_container.get(t)
        if cs and ct and cs != ct and cs in container_ids and ct in container_ids:
            pair = (cs, ct)
            if pair not in seen_ce:
                seen_ce.add(pair)
                c_edges.append(pair)

    if c_edges:
        # Canonical trust/domain/phase order (keyword_key) is a fixed domain invariant —
        # public→dmz→identity→partner→private→restricted→detect→external_saas (and the
        # Logical/Data-flow equivalents) must never move because of one incidental edge.
        # A misplaced leaf (e.g. Secrets Manager drawn under Identity instead of Private)
        # previously could flip the whole corridor row via edge-derived rank; dependency
        # data is now only a tiebreaker within the same canonical slot.
        ranks = longest_path_ranks(container_ids, c_edges)
        ranked.sort(key=lambda c: (
            keyword_key(c)[0], ranks.get(c.get("id") or "", 99), keyword_key(c)[1],
        ))
    else:
        ranked.sort(key=keyword_key)

    changed = False
    x = start_x
    for cell in ranked:
        geo = _cell_geometry(cell)
        geom = cell.find("mxGeometry")
        if geo is None or geom is None:
            continue
        if abs(geo[0] - x) > 1.0:
            geom.set("x", str(x))
            changed = True
        if _layout_tiles_in_container(cell, cells, edges, leaf_filter, mark_layout=mark_layout):
            changed = True
            geo = _cell_geometry(cell) or geo
        x += geo[2] + gap

    # Uniform corridor band: every ranked corridor shares one top y and one
    # height (the tallest corridor's content), so the row of corridors reads
    # as a single aligned band instead of a staircase of LLM-guessed heights.
    if ranked:
        band_h = 0.0
        for cell in ranked:
            geo = _cell_geometry(cell)
            if geo:
                band_h = max(band_h, geo[3])
        for cell in ranked:
            geom = cell.find("mxGeometry")
            geo = _cell_geometry(cell)
            if geom is None or geo is None:
                continue
            if abs(geo[1] - start_y) > 1.0:
                geom.set("y", str(start_y))
                changed = True
            if geo[3] < band_h - 1.0:
                geom.set("height", str(band_h))
                changed = True

    # Anchor shelf corridors under their target (or rightmost ranked corridor).
    # Multiple shelves (e.g. Data-flow's phase_consume + phase_saas_producer +
    # phase_saas_consumer, all under phase_store) stack one below the other via
    # a running cursor, rather than all landing at the same y under the same
    # anchor and overlapping.
    if saas_shelves:
        anchor = next((c for c in ranked if shelf_anchor_filter(c)), None)
        if anchor is None and ranked:
            anchor = ranked[-1]
        cursor_y: float | None = None
        for shelf in saas_shelves:
            if _anchor_shelf_under(shelf, anchor, cells, y_override=cursor_y):
                changed = True
            # Non-SaaS shelves (e.g. Data-flow's phase_consume) get the normal
            # tile grid, not the SaaS-specific vertical stack — SaaS shelves are
            # skipped inside _layout_tiles_in_container itself (_is_saas_domain).
            if _layout_tiles_in_container(shelf, cells, edges, leaf_filter, mark_layout=mark_layout):
                changed = True
            shelf_geo = _cell_geometry(shelf)
            if shelf_geo is not None:
                cursor_y = shelf_geo[1] + shelf_geo[3] + INTER_CONTAINER_GAP

    return changed


def _layout_tiles_in_container(
    container: ET.Element,
    cells: list[ET.Element],
    all_edges: list[tuple[str, str]],
    leaf_filter: Callable[[ET.Element], bool],
    *,
    mark_layout: bool,
) -> bool:
    cid = container.get("id") or ""
    kids = [
        c for c in cells
        if c.get("parent") == cid and leaf_filter(c)
    ]
    if not kids:
        return False
    # SaaS shelves stay vertical — caller handles via _sort_saas_children_in_shelf.
    cid_lower = cid.lower()
    if _is_saas_domain(container) or cid_lower.startswith("phase_saas"):
        return False

    kid_ids = [c.get("id") or "" for c in kids if c.get("id")]
    id_set = set(kid_ids)
    local_edges = [(s, t) for s, t in all_edges if s in id_set and t in id_set]

    if local_edges and len(kids) > 1:
        ranks = longest_path_ranks(kid_ids, local_edges)
        order = barycenter_order_best(ranks, local_edges)
        # Place by rank as column, order as row — capped to balanced grid.
        max_rank = max(ranks.values(), default=0) + 1
        cols_n = min(grid_cols(len(kids)), max(1, max_rank))
        # Flatten barycenter order into a reading list, then grid.
        kids.sort(key=lambda c: (
            ranks.get(c.get("id") or "", 0),
            order.get(c.get("id") or "", 0),
            c.get("id") or "",
        ))
    else:
        kids.sort(key=lambda c: (
            (_cell_geometry(c) or (0, 0, 0, 0))[1],
            (_cell_geometry(c) or (0, 0, 0, 0))[0],
            c.get("id") or "",
        ))
        cols_n = grid_cols(len(kids))

    changed = False
    for i, kid in enumerate(kids):
        col = i % cols_n
        row = i // cols_n
        tx = CONTAINER_PADDING_X + col * _ICON_PITCH_X
        ty = CONTAINER_LABEL_HEIGHT + row * _ICON_PITCH_Y
        geom = kid.find("mxGeometry")
        if geom is None:
            continue
        try:
            cur_x = float(geom.get("x") or 0)
            cur_y = float(geom.get("y") or 0)
        except ValueError:
            cur_x, cur_y = 0.0, 0.0
        if abs(cur_x - tx) > 0.5 or abs(cur_y - ty) > 0.5:
            geom.set("x", str(tx))
            geom.set("y", str(ty))
            changed = True
        if mark_layout and kid.get("layout") != "1":
            kid.set("layout", "1")
            changed = True

    shelf_geo = container.find("mxGeometry")
    if shelf_geo is not None and resize_container_to_grid(shelf_geo, len(kids)):
        changed = True
    return changed


def _anchor_shelf_under(
    shelf: ET.Element,
    anchor: ET.Element | None,
    cells: list[ET.Element],
    *,
    y_override: float | None = None,
) -> bool:
    """Place a shelf container under its anchor (same column).

    ``y_override`` lets a caller stack a *second* shelf under the same anchor
    (e.g. Data-flow's ``phase_consume`` + ``phase_saas`` both anchor under
    ``phase_store``) without recomputing from the anchor's bottom edge, which
    would otherwise place both shelves at the exact same y and overlap them.
    """
    if anchor is None:
        return False
    a_geo = _cell_geometry(anchor)
    s_geom = shelf.find("mxGeometry")
    if a_geo is None or s_geom is None:
        return False
    kids = [
        c for c in cells
        if c.get("parent") == (shelf.get("id") or "")
        and c.get("vertex") == "1"
        and c.get("edge") != "1"
    ]
    tx = a_geo[0]
    ty = y_override if y_override is not None else a_geo[1] + a_geo[3] + INTER_CONTAINER_GAP
    changed = False
    try:
        cur_x = float(s_geom.get("x") or 0)
        cur_y = float(s_geom.get("y") or 0)
    except ValueError:
        cur_x, cur_y = 0.0, 0.0
    if abs(cur_x - tx) > 1.0 or abs(cur_y - ty) > 1.0:
        s_geom.set("x", str(tx))
        s_geom.set("y", str(ty))
        changed = True
    # This is a provisional size — for a genuine SaaS shelf (domain_saas /
    # phase_saas_* / zone_external_saas), the caller's subsequent
    # _sort_saas_children_in_shelf pass grid-fills the children and resizes
    # authoritatively via this same resize_container_to_grid function; for a
    # non-SaaS shelf (phase_consume), _layout_tiles_in_container does the
    # same immediately after this call returns in apply_layered_corridor_layout.
    # No shelf-type branch needed here — one canonical resize either way.
    if resize_container_to_grid(s_geom, len(kids)):
        changed = True
    if shelf.get("layout") != "1":
        shelf.set("layout", "1")
        changed = True
    return changed
