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

from app.adapters.outbound.diagram.xml_diagnostics import (
    _cell_geometry,
    _is_container_cell,
)


# Pitch reused by placement_fixers for Logical; kept here so layout is self-contained.
APP_PITCH_X = 220.0
APP_PITCH_Y = 150.0
APP_BASE_X = 28.0
APP_BASE_Y = 56.0
APP_MAX_COLS = 4
DOMAIN_GAP = 110.0
SHELF_GAP = 110.0


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
    gap: float = DOMAIN_GAP,
    pitch_x: float = APP_PITCH_X,
    pitch_y: float = APP_PITCH_Y,
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
        if _layout_tiles_in_container(
            cell, cells, edges, leaf_filter,
            pitch_x=pitch_x, pitch_y=pitch_y, mark_layout=mark_layout,
        ):
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
    # Multiple shelves (e.g. Data-flow's phase_consume + phase_saas, both under
    # phase_store) stack one below the other via a running cursor, rather than
    # all landing at the same y under the same anchor and overlapping.
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
            if _layout_tiles_in_container(
                shelf, cells, edges, leaf_filter,
                pitch_x=pitch_x, pitch_y=pitch_y, mark_layout=mark_layout,
            ):
                changed = True
            shelf_geo = _cell_geometry(shelf)
            if shelf_geo is not None:
                cursor_y = shelf_geo[1] + shelf_geo[3] + SHELF_GAP

    return changed


def _layout_tiles_in_container(
    container: ET.Element,
    cells: list[ET.Element],
    all_edges: list[tuple[str, str]],
    leaf_filter: Callable[[ET.Element], bool],
    *,
    pitch_x: float,
    pitch_y: float,
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
        tx = APP_BASE_X + col * pitch_x
        ty = APP_BASE_Y + row * pitch_y
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

    rows = (len(kids) - 1) // cols_n + 1
    cols = min(len(kids), cols_n)
    needed_w = APP_BASE_X + cols * pitch_x + 20.0
    needed_h = APP_BASE_Y + rows * pitch_y + 24.0
    shelf_geo = container.find("mxGeometry")
    if shelf_geo is not None:
        try:
            cur_w = float(shelf_geo.get("width") or 0)
            cur_h = float(shelf_geo.get("height") or 0)
        except ValueError:
            cur_w, cur_h = 0.0, 0.0
        if cur_w < needed_w:
            shelf_geo.set("width", str(needed_w))
            changed = True
        if cur_h < needed_h:
            shelf_geo.set("height", str(needed_h))
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
    # Estimate height from SaaS children already present (caller may re-stack).
    kids = [
        c for c in cells
        if c.get("parent") == (shelf.get("id") or "")
        and c.get("vertex") == "1"
        and c.get("edge") != "1"
    ]
    needed_h = max(
        a_geo[3] * 0.5,
        48.0 + max(1, len(kids)) * 110.0 + 24.0,
    )
    s_geo = _cell_geometry(shelf)
    tx = a_geo[0]
    ty = y_override if y_override is not None else a_geo[1] + a_geo[3] + SHELF_GAP
    # A vertical SaaS shelf (domain_saas / phase_saas / zone_external_saas) is a
    # single icon column — never stretch it to the anchor's own width (e.g. a wide
    # phase_store), which is what made the shelf read as an oversized empty box.
    # Anchors like phase_consume (a tile grid) still align to the anchor's width.
    shelf_id = (shelf.get("id") or "").lower()
    is_vertical_saas_shelf = "saas" in shelf_id and (
        shelf_id.startswith("domain_") or shelf_id.startswith("phase_")
        or shelf_id.startswith("zone_")
    )
    if is_vertical_saas_shelf:
        tw = max((s_geo[2] if s_geo else 180.0), 180.0)
    else:
        tw = max(a_geo[2], (s_geo[2] if s_geo else 180.0), 180.0)
    changed = False
    try:
        cur_x = float(s_geom.get("x") or 0)
        cur_y = float(s_geom.get("y") or 0)
        cur_w = float(s_geom.get("width") or 0)
        cur_h = float(s_geom.get("height") or 0)
    except ValueError:
        cur_x, cur_y, cur_w, cur_h = 0.0, 0.0, 0.0, 0.0
    if abs(cur_x - tx) > 1.0 or abs(cur_y - ty) > 1.0:
        s_geom.set("x", str(tx))
        s_geom.set("y", str(ty))
        changed = True
    if cur_w < tw - 1.0:
        s_geom.set("width", str(tw))
        changed = True
    if cur_h < needed_h - 1.0:
        s_geom.set("height", str(needed_h))
        changed = True
    if shelf.get("layout") != "1":
        shelf.set("layout", "1")
        changed = True
    return changed
