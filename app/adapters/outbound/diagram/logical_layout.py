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
    gap: float = DOMAIN_GAP,
    pitch_x: float = APP_PITCH_X,
    pitch_y: float = APP_PITCH_Y,
    mark_layout: bool = True,
) -> bool:
    """Rank corridors by leaf edges, lay tiles by barycenter, write geometry.

    ``domain_saas`` / SaaS shelves are excluded from the L→R rank and returned
    so the caller can anchor them under the Data domain.
    """
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    ranked = [c for c in corridors if not _is_saas_domain(c)]
    saas_shelves = [c for c in corridors if _is_saas_domain(c)]
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
        ranks = longest_path_ranks(container_ids, c_edges)
        ranked.sort(key=lambda c: (ranks.get(c.get("id") or "", 99), keyword_key(c)))
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

    # Anchor SaaS shelf under Data (or rightmost ranked domain).
    if saas_shelves:
        anchor = next((c for c in ranked if _is_data_domain(c)), None)
        if anchor is None and ranked:
            anchor = ranked[-1]
        for shelf in saas_shelves:
            if _anchor_shelf_under(shelf, anchor, cells):
                changed = True

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
    if _is_saas_domain(container):
        return False

    kid_ids = [c.get("id") or "" for c in kids if c.get("id")]
    id_set = set(kid_ids)
    local_edges = [(s, t) for s, t in all_edges if s in id_set and t in id_set]

    if local_edges and len(kids) > 1:
        ranks = longest_path_ranks(kid_ids, local_edges)
        order = barycenter_order(ranks, local_edges)
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
) -> bool:
    """Place External SaaS shelf under the Data domain (same column)."""
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
    ty = a_geo[1] + a_geo[3] + SHELF_GAP
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
