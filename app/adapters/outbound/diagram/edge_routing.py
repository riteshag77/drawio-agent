"""Edge-anchor / label parking helpers for draw.io XML.

Layout geometry for corridor views is owned by the IR compiler
(``logical_layout.layout_corridor_ir``). This module only adjusts edge
exit/entry ports, orthogonal detours, and label offsets after compile/repair,
plus a few cell classifiers still shared with ``xml_diagnostics``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.xml_diagnostics import (
    _cell_geometry,
    _is_container_cell,
    _parse_docroot,
    _serialize_docroot,
    absolute_position,
)


def _plain_label(cell: ET.Element) -> str:
    value = (cell.get("value") or "").strip().lower()
    return re.sub(r"<[^>]+>", " ", value).strip()


def _shape(cell: ET.Element) -> str:
    return (cell.get("shape_type") or "").strip().lower()


def _set_style_kv(style: str, updates: dict[str, str]) -> str:
    parts = [p for p in (style or "").split(";") if p]
    keys = {p.split("=", 1)[0] for p in parts if "=" in p}
    out: list[str] = []
    for p in parts:
        if "=" not in p:
            out.append(p)
            continue
        k, _ = p.split("=", 1)
        if k in updates:
            out.append(f"{k}={updates[k]}")
        else:
            out.append(p)
    for k, v in updates.items():
        if k not in keys:
            out.append(f"{k}={v}")
    return ";".join(out) + (";" if out else "")


_PARALLEL_EDGE_OFFSET_PX = 20.0
_LOGICAL_PARALLEL_EDGE_OFFSET_PX = 36.0
# Obstacle clearance used by the Logical edge router — 20px minimum per the
# user's edge-routing requirements (Fix 2d).
_LOGICAL_ROUTE_CLEARANCE = 20.0
_LOGICAL_ROUTE_BAND = 44.0
# Edges only count as "parallel" when they run in the same lane of the canvas.
_CORRIDOR_LANE_PX = 140.0
_CORRIDOR_BAND_STEP = 28.0
# Two runs must share at least this much travel to count as parallel.
_CORRIDOR_MIN_SHARED_PX = 60.0
# Minimum port spacing along a face (physical); capacity = face_len / gap.
_MIN_PORT_GAP_PX = 14.0

_FACE_RIGHT = ("h", "right", "1", "0.5")
_FACE_LEFT = ("h", "left", "0", "0.5")
_FACE_BOTTOM = ("v", "bottom", "0.5", "1")
_FACE_TOP = ("v", "top", "0.5", "0")
_ALL_FACES = (_FACE_RIGHT, _FACE_LEFT, _FACE_BOTTOM, _FACE_TOP)


def _edge_is_primary(edge: ET.Element) -> bool:
    style = (edge.get("style") or "").lower()
    # Secondary flows use dashed stroke in theme.flow_style(primary=False).
    if "dashed=1" in style:
        return False
    return True


def _unit(dx: float, dy: float) -> tuple[float, float]:
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-6:
        return (1.0, 0.0)
    return (dx / length, dy / length)


def _face_outward(face_name: str) -> tuple[float, float]:
    return {
        "right": (1.0, 0.0),
        "left": (-1.0, 0.0),
        "bottom": (0.0, 1.0),
        "top": (0.0, -1.0),
    }[face_name]


def _score_face(
    face_name: str,
    toward: tuple[float, float],
    box: tuple[float, float, float, float],
    peer: tuple[float, float],
    obstacles: list[tuple[float, float, float, float]],
) -> float:
    """Higher is better: alignment of outward normal to travel + first-hop clearance."""
    ox, oy = _face_outward(face_name)
    tx, ty = _unit(toward[0], toward[1])
    align = ox * tx + oy * ty  # -1..1
    x, y, w, h = box
    if face_name == "right":
        start = (x + w, y + h / 2)
    elif face_name == "left":
        start = (x, y + h / 2)
    elif face_name == "bottom":
        start = (x + w / 2, y + h)
    else:
        start = (x + w / 2, y)
    hop = [start, (start[0] + ox * 40.0, start[1] + oy * 40.0), peer]
    blocked = _path_blocked_count(hop, obstacles, _LOGICAL_ROUTE_CLEARANCE)
    return align * 10.0 - blocked * 3.0


def _pick_best_faces(
    meta: dict,
    waypoints: list[tuple[float, float]],
    obstacles: list[tuple[float, float, float, float]],
) -> dict[str, str]:
    """Independently score exit and entry faces; prefer ELK first/last hop dirs."""
    sbox = (meta["sx"], meta["sy"], meta["sw"], meta["sh"])
    tbox = (meta["tx"], meta["ty"], meta["tw"], meta["th"])
    scx, scy, tcx, tcy = meta["scx"], meta["scy"], meta["tcx"], meta["tcy"]
    if waypoints:
        toward_exit = (waypoints[0][0] - scx, waypoints[0][1] - scy)
        # Entry face outward should point toward the final approach waypoint.
        toward_entry = (waypoints[-1][0] - tcx, waypoints[-1][1] - tcy)
    else:
        toward_exit = (tcx - scx, tcy - scy)
        # From target toward source — the side the connector arrives from.
        toward_entry = (scx - tcx, scy - tcy)

    best_exit = max(
        _ALL_FACES,
        key=lambda f: _score_face(f[1], toward_exit, sbox, (tcx, tcy), obstacles),
    )
    best_entry = max(
        _ALL_FACES,
        key=lambda f: _score_face(f[1], toward_entry, tbox, (scx, scy), obstacles),
    )
    return {
        "exit_axis": best_exit[0],
        "exit_face": best_exit[1],
        "exitX": best_exit[2],
        "exitY": best_exit[3],
        "entry_axis": best_entry[0],
        "entry_face": best_entry[1],
        "entryX": best_entry[2],
        "entryY": best_entry[3],
    }


def _face_capacity(meta: dict, face_name: str) -> int:
    span = float(meta.get("sh", 78) if face_name in ("left", "right") else meta.get("sw", 78))
    return max(1, int(span // _MIN_PORT_GAP_PX))


def _even_fractions(n: int) -> list[str]:
    """Even fan-out: (i+1)/(n+1) along the face — no fixed tuple, no wrap."""
    if n <= 0:
        return []
    return [str(round((i + 1) / (n + 1), 4)) for i in range(n)]


def _cross_axis_coord(meta: dict, face_name: str, *, opposite: bool) -> float:
    """Coordinate along the face axis of the opposite endpoint (for sort)."""
    if face_name in ("left", "right"):
        return meta["tcy"] if opposite else meta["scy"]
    return meta["tcx"] if opposite else meta["scx"]


def _assign_face_ports(
    incidents: list[tuple[ET.Element, dict, bool]],
    *,
    end: str,
) -> bool:
    """Assign even fractions on shared faces; spill overflow to next-best face.

    ``incidents`` items are ``(edge, meta, is_primary)``. ``end`` is ``exit`` or ``entry``.
    Primary edges are assigned first so the spine keeps the cleanest faces.
    """
    if not incidents:
        return False
    changed = False
    face_key = "exit_face" if end == "exit" else "entry_face"
    axis_key = "exit_axis" if end == "exit" else "entry_axis"
    ordered = sorted(incidents, key=lambda it: (0 if it[2] else 1))

    by_face: dict[str, list[tuple[ET.Element, dict, bool]]] = {}
    for edge, meta, primary in ordered:
        by_face.setdefault(meta[face_key], []).append((edge, meta, primary))

    final_buckets: dict[str, list[tuple[ET.Element, dict, bool]]] = {}
    for face, group in by_face.items():
        sample = group[0][1]
        if end == "exit":
            capacity = _face_capacity({"sw": sample["sw"], "sh": sample["sh"]}, face)
        else:
            capacity = _face_capacity({"sw": sample["tw"], "sh": sample["th"]}, face)
        keep, overflow = group[:capacity], group[capacity:]
        final_buckets.setdefault(face, []).extend(keep)
        for edge, meta, primary in overflow:
            sbox = (meta["sx"], meta["sy"], meta["sw"], meta["sh"])
            tbox = (meta["tx"], meta["ty"], meta["tw"], meta["th"])
            toward = (meta["tcx"] - meta["scx"], meta["tcy"] - meta["scy"])
            box = sbox if end == "exit" else tbox
            peer = (meta["tcx"], meta["tcy"]) if end == "exit" else (meta["scx"], meta["scy"])
            ranked = sorted(
                _ALL_FACES,
                key=lambda f: _score_face(f[1], toward, box, peer, []),
                reverse=True,
            )
            placed = False
            for alt in ranked:
                if alt[1] == face:
                    continue
                bucket = final_buckets.setdefault(alt[1], [])
                dims = (
                    {"sw": meta["sw"], "sh": meta["sh"]}
                    if end == "exit"
                    else {"sw": meta["tw"], "sh": meta["th"]}
                )
                if len(bucket) >= _face_capacity(dims, alt[1]):
                    continue
                meta[face_key] = alt[1]
                meta[axis_key] = alt[0]
                if end == "exit":
                    meta["exitX"], meta["exitY"] = alt[2], alt[3]
                else:
                    meta["entryX"], meta["entryY"] = alt[2], alt[3]
                bucket.append((edge, meta, primary))
                placed = True
                break
            if not placed:
                final_buckets.setdefault(face, []).append((edge, meta, primary))

    for face, group in final_buckets.items():
        if not group:
            continue
        # Sort by opposite-end cross-axis so fan-out doesn't cross.
        group.sort(key=lambda it: _cross_axis_coord(it[1], face, opposite=(end == "exit")))
        fracs = _even_fractions(len(group))
        axis = group[0][1][axis_key]
        for (edge, meta, _primary), frac in zip(group, fracs):
            style = edge.get("style") or ""
            if end == "exit":
                if axis == "h":
                    updates = {"exitX": meta["exitX"], "exitY": frac}
                else:
                    updates = {"exitX": frac, "exitY": meta["exitY"]}
            else:
                if axis == "h":
                    updates = {"entryX": meta["entryX"], "entryY": frac}
                else:
                    updates = {"entryX": frac, "entryY": meta["entryY"]}
            new_style = _set_style_kv(style, updates)
            if new_style != style:
                edge.set("style", new_style)
                changed = True
    return changed


def _style_map(style: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (style or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _logical_obstacle_rects(
    cells: list[ET.Element],
    by_id: dict[str, ET.Element],
    skip_ids: set[str],
    page_w: float = 0.0,
    page_h: float = 0.0,
) -> list[tuple[float, float, float, float]]:
    """Absolute leaf boxes (incl. under-icon label band) an edge must not cross."""
    from app.adapters.outbound.diagram.xml_diagnostics import _band_height, _is_canvas_background_cell

    rects: list[tuple[float, float, float, float]] = []
    for cell in cells:
        cid = cell.get("id") or ""
        if cid in skip_ids:
            continue
        if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
            continue
        if cid in {"overview_panel", "legend_box"} or cid.startswith("step_badge_"):
            continue
        # Found via real-capture replay: without this, the full-page
        # background rect (e.g. a 3586x1400 opaque backdrop) is treated as a
        # single giant obstacle, forcing every detour band computation
        # (_route_candidates) to the extreme edge of the whole canvas instead
        # of a sane local detour.
        if _is_canvas_background_cell(cell, page_w, page_h):
            continue
        geo = _cell_geometry(cell)
        if geo is None:
            continue
        ax, ay = absolute_position(cell, by_id)
        rects.append((ax, ay, geo[2], geo[3] + _band_height(cell)))
    return rects


def _segment_blocked(
    p: tuple[float, float],
    q: tuple[float, float],
    obstacles: list[tuple[float, float, float, float]],
    clearance: float,
) -> bool:
    x1, x2 = sorted((p[0], q[0]))
    y1, y2 = sorted((p[1], q[1]))
    for ox, oy, ow, oh in obstacles:
        if (
            x1 < ox + ow + clearance
            and ox - clearance < x2
            and y1 < oy + oh + clearance
            and oy - clearance < y2
        ):
            return True
    return False


def _path_blocked_count(
    points: list[tuple[float, float]],
    obstacles: list[tuple[float, float, float, float]],
    clearance: float,
) -> int:
    return sum(
        1 for a, b in zip(points, points[1:])
        if _segment_blocked(a, b, obstacles, clearance)
    )


def _clear_lanes(
    run_lo: float,
    run_hi: float,
    ref: float,
    obstacles: list[tuple[float, float, float, float]],
    clearance: float,
    axis: str,
) -> list[float]:
    """Coordinates of clear lanes crossing the run, nearest ``ref`` first.

    ``axis="h"`` means the connector travels horizontally across x in
    ``[run_lo, run_hi]`` and this picks a y to travel at; ``"v"`` is the
    transpose.

    Preferred over the extreme-edge bands below it. Those take the outermost
    edge of every box sharing the run's span, which on a deeply nested page like
    Network is a whole container — so the lane landed outside the drawing
    entirely (or off-canvas at a negative coordinate) and the connector looped
    around the diagram to reach a neighbour two rows down. The real target is
    the gap the layout already reserves between tiers and rows, which is what
    scanning for gaps between occupied intervals finds.
    """
    if axis == "h":
        pos, size, cross_pos, cross_size = 0, 2, 1, 3
    else:
        pos, size, cross_pos, cross_size = 1, 3, 0, 2

    spanning = [
        r for r in obstacles
        if r[pos] < run_hi + 40.0 and run_lo - 40.0 < r[pos] + r[size]
    ]
    if not spanning:
        return []

    merged: list[list[float]] = []
    for lo, hi in sorted((r[cross_pos], r[cross_pos] + r[cross_size]) for r in spanning):
        if merged and lo <= merged[-1][1] + clearance:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    lanes = [
        (before[1] + after[0]) / 2.0
        for before, after in zip(merged, merged[1:])
        if after[0] - before[1] >= clearance * 2
    ]
    lanes.append(merged[0][0] - clearance - _LOGICAL_ROUTE_BAND / 2.0)
    lanes.append(merged[-1][1] + clearance + _LOGICAL_ROUTE_BAND / 2.0)
    # Negative coordinates route off the top/left of the page: draw.io renders
    # them but export bounds and the diagnostics engine both treat them as a
    # defect, so they are never an acceptable lane.
    return sorted((v for v in lanes if v >= 0.0), key=lambda v: abs(v - ref))


def _route_candidates(
    start: tuple[float, float],
    end: tuple[float, float],
    axis: str,
    obstacles: list[tuple[float, float, float, float]],
    offsets: tuple[float, float],
    frames: list[tuple[float, float, float, float]],
) -> list[list[tuple[float, float]]]:
    """Orthogonal paths to try, cheapest (straightest) first.

    ``frames`` are domain boxes: a detour has to clear the whole box, otherwise
    the connector cuts across a container title. ``offsets`` is
    ``(mid_shift, band_shift)`` — the first spreads parallel elbow columns, the
    second stacks parallel detours into their own lanes.
    """
    mid_shift, band_shift = offsets
    sx, sy = start
    ex, ey = end
    out: list[list[tuple[float, float]]] = []
    if axis == "h":
        # Only offer the trivial straight line when it's actually clear — if it's
        # blocked, letting it into the candidate pool lets it win ties against
        # every detour below (it's evaluated first, and _path_blocked_count's
        # coarse "1 blocked segment" often ties with a detour that's ALSO
        # partially blocked), which is how an edge ends up drawn straight
        # through the center of an icon instead of jogging around it.
        if abs(sy - ey) < 1.0 and not _segment_blocked(start, end, obstacles, _LOGICAL_ROUTE_CLEARANCE):
            out.append([start, end])
        # Same-row hop (source/target level, e.g. two icons in one corridor
        # with a third sitting between them): a mid-shift jog is a zero-height
        # no-op at this y, so it can never dodge anything on this row — the
        # only way over/under a same-row neighbor is an actual vertical
        # offset, tried at increasing distances before the whole-corridor
        # under-band detour below (which computes its band from every
        # obstacle/frame in the x-span and can land far outside the visible
        # area for a wide row, crossing even more unrelated shapes).
        if abs(sy - ey) < 1.0:
            for dy in (60.0, -60.0, 96.0, -96.0, 140.0, -140.0):
                jy = sy + dy
                out.append([start, (sx, jy), (ex, jy), end])
        mid = (sx + ex) / 2.0 + mid_shift
        for shift in (0.0, 40.0, -40.0, 80.0, -80.0):
            mx = mid + shift
            out.append([start, (mx, sy), (mx, ey), end])
        # Reserved-gap lanes, nearest the target first: descend at the source's
        # x, cross in a clear horizontal gap, arrive at the target's face.
        for lane_y in _clear_lanes(
            min(sx, ex), max(sx, ex), ey, obstacles, _LOGICAL_ROUTE_CLEARANCE, "h"
        )[:4]:
            # band_shift keeps two connectors that pick the same reserved gap
            # from being drawn on top of each other.
            out.append([start, (sx, lane_y + band_shift), (ex, lane_y + band_shift), end])
        # Y-proximity window on top of the existing X-range filter: without
        # it, any container that merely shares the edge's X-span — however
        # far away vertically (a whole other zone/row) — drags top/bottom to
        # its own extreme, pushing the detour band far outside the visible
        # area (found via real-capture replay: a same-row cross-zone edge on
        # a wide Security page computed a band at y=1444 on a ~600px-tall
        # canvas, crossing straight through unrelated shapes near that band).
        y_window = 320.0
        span = [
            r for r in obstacles + frames
            if min(sx, ex) - 40 < r[0] + r[2] and r[0] < max(sx, ex) + 40
            and min(sy, ey) - y_window < r[1] + r[3] and r[1] < max(sy, ey) + y_window
        ]
        top = min((r[1] for r in span), default=min(sy, ey))
        bottom = max((r[1] + r[3] for r in span), default=max(sy, ey))
        # Under-corridor first: the space above holds titles and the overview panel.
        for band_y in (
            bottom + _LOGICAL_ROUTE_BAND + band_shift,
            top - _LOGICAL_ROUTE_BAND - band_shift,
        ):
            out.append([
                start, (sx + 24.0, sy), (sx + 24.0, band_y),
                (ex - 24.0, band_y), (ex - 24.0, ey), end,
            ])
    else:
        if abs(sx - ex) < 1.0 and not _segment_blocked(start, end, obstacles, _LOGICAL_ROUTE_CLEARANCE):
            out.append([start, end])
        # Mirrors the "h" branch's same-column fix above.
        if abs(sx - ex) < 1.0:
            for dx in (60.0, -60.0, 96.0, -96.0, 140.0, -140.0):
                jx = sx + dx
                out.append([start, (jx, sy), (jx, ey), end])
        mid = (sy + ey) / 2.0 + mid_shift
        for shift in (0.0, 40.0, -40.0, 80.0, -80.0):
            my = mid + shift
            out.append([start, (sx, my), (ex, my), end])
        # Reserved-gap lanes — mirrors the "h" branch above.
        for lane_x in _clear_lanes(
            min(sy, ey), max(sy, ey), ex, obstacles, _LOGICAL_ROUTE_CLEARANCE, "v"
        )[:4]:
            out.append([start, (lane_x + band_shift, sy), (lane_x + band_shift, ey), end])
        # X-proximity window — mirrors the "h" branch's fix above.
        x_window = 320.0
        span = [
            r for r in obstacles + frames
            if min(sy, ey) - 40 < r[1] + r[3] and r[1] < max(sy, ey) + 40
            and min(sx, ex) - x_window < r[0] + r[2] and r[0] < max(sx, ex) + x_window
        ]
        left = min((r[0] for r in span), default=min(sx, ex))
        right = max((r[0] + r[2] for r in span), default=max(sx, ex))
        for band_x in (
            right + _LOGICAL_ROUTE_BAND + band_shift,
            left - _LOGICAL_ROUTE_BAND - band_shift,
        ):
            out.append([
                start, (sx, sy + 24.0), (band_x, sy + 24.0),
                (band_x, ey - 24.0), (ex, ey - 24.0), end,
            ])
    return out


def _cluster_overlapping_edges(
    bucket: list[ET.Element],
    edge_meta: dict[str, dict],
) -> list[list[ET.Element]]:
    """Split a lane bucket into runs whose travel ranges actually overlap."""
    items = []
    for edge in bucket:
        meta = edge_meta.get(edge.get("id") or "")
        if meta and meta.get("travel"):
            items.append((meta["travel"], edge))
    items.sort(key=lambda it: it[0][0])
    clusters: list[list[ET.Element]] = []
    current: list[ET.Element] = []
    reach = None
    for (lo, hi), edge in items:
        if current and reach is not None and lo < reach - _CORRIDOR_MIN_SHARED_PX:
            current.append(edge)
            reach = max(reach, hi)
            continue
        if current:
            clusters.append(current)
        current, reach = [edge], hi
    if current:
        clusters.append(current)
    return clusters


def _route_logical_edge(
    edge: ET.Element,
    meta: dict,
    obstacles: list[tuple[float, float, float, float]],
    offsets: tuple[float, float],
    frames: list[tuple[float, float, float, float]],
) -> bool:
    """Pick the straightest orthogonal path that clears every other icon.

    Tries the natural entry face (from relative source→target placement)
    first. If that face is structurally blocked along its whole length — a
    sibling sitting directly across it, so every waypoint candidate still
    crosses something — falls back to entering the target from a
    perpendicular face instead, e.g. top/bottom when the natural face was
    left/right. Found via a real fixture (test_edge_overlap_checks.py's
    KnownResidualTests) where a same-row sibling fully occluded the target's
    right face; no waypoint jog at that fixed face could dodge it.
    """
    kv = _style_map(edge.get("style") or "")

    def _num(key: str, default: float) -> float:
        try:
            return float(kv.get(key, default))
        except (TypeError, ValueError):
            return default

    start = (
        meta["sx"] + meta["sw"] * _num("exitX", 0.5) + _num("exitDx", 0.0),
        meta["sy"] + meta["sh"] * _num("exitY", 0.5) + _num("exitDy", 0.0),
    )
    axis = meta["axis"]

    def _best_for_entry(entry_fx: float, entry_fy: float, route_axis: str):
        end = (
            meta["tx"] + meta["tw"] * entry_fx + _num("entryDx", 0.0),
            meta["ty"] + meta["th"] * entry_fy + _num("entryDy", 0.0),
        )
        candidates = _route_candidates(start, end, route_axis, obstacles, offsets, frames)
        best_points: list[tuple[float, float]] | None = None
        best_score = None
        for points in candidates:
            score = _path_blocked_count(points, obstacles, _LOGICAL_ROUTE_CLEARANCE)
            if score == 0:
                return points, 0
            if best_score is None or score < best_score:
                best_points, best_score = points, score
        return best_points, best_score

    natural_fx, natural_fy = _num("entryX", 0.5), _num("entryY", 0.5)
    best_points, best_score = _best_for_entry(natural_fx, natural_fy, axis)
    best_face: tuple[str, str] | None = None

    if best_score:
        alt_faces = (
            [("0.5", "0.0", "v"), ("0.5", "1.0", "v")] if axis == "h"
            else [("0.0", "0.5", "h"), ("1.0", "0.5", "h")]
        )
        for fx_s, fy_s, alt_axis in alt_faces:
            points, score = _best_for_entry(float(fx_s), float(fy_s), alt_axis)
            if points is None:
                continue
            if best_score is None or score < best_score:
                best_points, best_score, best_face = points, score, (fx_s, fy_s)
            if score == 0:
                break

    if best_points is None:
        return False
    if best_face is not None:
        style = edge.get("style") or ""
        edge.set("style", _set_style_kv(style, {"entryX": best_face[0], "entryY": best_face[1]}))
    interior = best_points[1:-1]
    _set_edge_waypoints(edge, interior)
    return True


def _edge_points(edge: ET.Element) -> list[tuple[float, float]]:
    """Existing intermediate waypoints on ``edge``, if any (e.g. ELK's route,
    written by the compiler via ``CellWriter.edge(points=...)``)."""
    geom = edge.find("mxGeometry")
    if geom is None:
        return []
    pts: list[tuple[float, float]] = []
    for arr in geom.findall("Array"):
        if arr.get("as") == "points":
            for pt in arr.findall("mxPoint"):
                try:
                    pts.append((float(pt.get("x") or 0), float(pt.get("y") or 0)))
                except ValueError:
                    pass
    return pts


def _set_edge_waypoints(edge: ET.Element, points: list[tuple[float, float]]) -> None:
    """Replace intermediate routing points on an edge (orthogonal elbows)."""
    geom = edge.find("mxGeometry")
    if geom is None:
        geom = ET.SubElement(edge, "mxGeometry")
        geom.set("relative", "1")
        geom.set("as", "geometry")
    # Drop existing Array points
    for child in list(geom):
        if child.tag == "Array" and child.get("as") == "points":
            geom.remove(child)
    if not points:
        return
    arr = ET.SubElement(geom, "Array")
    arr.set("as", "points")
    for px, py in points:
        pt = ET.SubElement(arr, "mxPoint")
        pt.set("x", str(round(px, 1)))
        pt.set("y", str(round(py, 1)))


def _bundle_under_corridor_trunks(
    edges: list[ET.Element],
    edge_meta: dict[str, dict],
    routed_paths: dict[str, list[tuple[float, float]]],
) -> bool:
    """Collapse 3+ parallel under-corridor detours onto a shared trunk with taps."""
    # Group by shared horizontal under-band (y of longest horizontal mid segment).
    bands: dict[int, list[str]] = {}
    for edge in edges:
        eid = edge.get("id") or ""
        pts = routed_paths.get(eid) or []
        if len(pts) < 2:
            continue
        # Find longest near-horizontal segment among waypoints.
        best = None
        best_len = 0.0
        meta = edge_meta.get(eid)
        if not meta:
            continue
        # Include start/end for length estimate
        full = [
            (meta["sx"] + meta["sw"], meta["scy"]),
            *pts,
            (meta["tx"], meta["tcy"]),
        ]
        for a, b in zip(full, full[1:]):
            if abs(a[1] - b[1]) < 8.0:  # horizontal
                length = abs(b[0] - a[0])
                if length > best_len:
                    best_len = length
                    best = round(a[1] / 14.0) * 14  # snap band key
        if best is not None and best_len > 200:
            bands.setdefault(int(best), []).append(eid)

    changed = False
    for band_y, eids in bands.items():
        if len(eids) < 3:
            continue
        trunk_y = float(band_y)
        for i, eid in enumerate(eids):
            edge = next((e for e in edges if e.get("id") == eid), None)
            meta = edge_meta.get(eid)
            if edge is None or meta is None:
                continue
            tap = (i - (len(eids) - 1) / 2.0) * 18.0
            start = (meta["sx"] + meta["sw"], meta["scy"] + float(
                _style_map(edge.get("style") or "").get("exitDy", 0) or 0
            ))
            end = (meta["tx"], meta["tcy"] + float(
                _style_map(edge.get("style") or "").get("entryDy", 0) or 0
            ))
            interior = [
                (start[0] + 24.0, start[1]),
                (start[0] + 24.0, trunk_y + tap),
                (end[0] - 24.0, trunk_y + tap),
                (end[0] - 24.0, end[1]),
            ]
            _set_edge_waypoints(edge, interior)
            routed_paths[eid] = interior
            changed = True
    return changed


def _park_edge_labels_on_longest_segment(
    edges: list[ET.Element],
    edge_meta: dict[str, dict],
    routed_paths: dict[str, list[tuple[float, float]]],
    obstacles: list[tuple[float, float, float, float]] | None = None,
) -> bool:
    """Park labelled-edge text on the clearest mid-segment candidate.

    Candidates: midpoints of each segment at t ∈ {0.25, 0.5, 0.75}.
    Score = longer segment − penalties for leaf/polyline/label AABB overlap.
    """
    obstacles = obstacles or []
    # Approximate label AABB from text length.
    def _label_box(cx: float, cy: float, text: str) -> tuple[float, float, float, float]:
        w = max(40.0, min(220.0, len(text) * 7.0))
        h = 18.0
        return (cx - w / 2, cy - h / 2, w, h)

    def _rects_overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
        return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]

    # Other edge polylines as soft obstacles (sample as thin boxes).
    poly_obs: list[tuple[float, float, float, float]] = []
    for eid, pts in routed_paths.items():
        meta = edge_meta.get(eid)
        if not meta:
            continue
        full = [(meta["scx"], meta["scy"]), *pts, (meta["tcx"], meta["tcy"])]
        for a, b in zip(full, full[1:]):
            lo_x, hi_x = sorted((a[0], b[0]))
            lo_y, hi_y = sorted((a[1], b[1]))
            poly_obs.append((lo_x - 2, lo_y - 2, max(4.0, hi_x - lo_x + 4), max(4.0, hi_y - lo_y + 4)))

    # Shared-trunk label separation: collect chosen positions along similar trunks.
    placed: list[tuple[float, float, str]] = []  # cx, cy, eid
    changed = False
    labelled = [e for e in edges if (e.get("value") or "").strip()]
    # Process longer labels first so short ones yield space.
    labelled.sort(key=lambda e: -len((e.get("value") or "").strip()))

    for edge in labelled:
        label = (edge.get("value") or "").strip()
        eid = edge.get("id") or ""
        meta = edge_meta.get(eid)
        if not meta:
            continue
        pts = routed_paths.get(eid) or []
        full = [
            (meta["scx"], meta["scy"]),
            *pts,
            (meta["tcx"], meta["tcy"]),
        ]
        n_segs = max(1, len(full) - 1)
        candidates: list[tuple[float, float, float, float]] = []  # score, frac, cx, cy
        for i, (a, b) in enumerate(zip(full, full[1:])):
            length = abs(b[0] - a[0]) + abs(b[1] - a[1])
            for t in (0.25, 0.5, 0.75):
                cx = a[0] + (b[0] - a[0]) * t
                cy = a[1] + (b[1] - a[1]) * t
                frac = (i + t) / n_segs
                box = _label_box(cx, cy, label)
                pen = 0.0
                for obs in obstacles:
                    if _rects_overlap(box, obs):
                        pen += 40.0
                for obs in poly_obs:
                    if _rects_overlap(box, obs):
                        pen += 8.0
                for px, py, other_id in placed:
                    if other_id == eid:
                        continue
                    if abs(px - cx) + abs(py - cy) < 36.0:
                        pen += 25.0
                score = length - pen
                candidates.append((score, frac, cx, cy))

        if not candidates:
            continue
        candidates.sort(key=lambda c: -c[0])
        best_score, best_frac, best_cx, best_cy = candidates[0]
        # If best still heavily penalized, try a small signed offset off the stroke.
        label_x = (best_frac - 0.5) * 2.0
        label_y = -1.0
        if best_score < 0:
            for dy in (-18.0, 18.0, -28.0, 28.0):
                box = _label_box(best_cx, best_cy + dy, label)
                if not any(_rects_overlap(box, obs) for obs in obstacles):
                    label_y = -1.0 if dy < 0 else 1.0
                    best_cy = best_cy + dy
                    break

        geom = edge.find("mxGeometry")
        if geom is None:
            continue
        try:
            cur_x = float(geom.get("x") or 0)
        except ValueError:
            cur_x = 0.0
        try:
            cur_y = float(geom.get("y") or 0)
        except ValueError:
            cur_y = 0.0
        if abs(cur_x - label_x) > 0.05:
            geom.set("x", str(round(label_x, 3)))
            changed = True
        if geom.get("y") in (None, "") or abs(cur_y - label_y) > 0.05:
            geom.set("y", str(label_y))
            changed = True
        placed.append((best_cx, best_cy, eid))
    return changed


def repark_edge_labels_after_external_routing(xml: str) -> str:
    """Re-park label offsets onto the clearest run of an externally-routed page.

    Used after libavoid (MCP ``create_diagram(routing="libavoid")``) rewrites a
    Logical/Security/Data-flow page's edges — that call is now the primary
    router for these pages (see ``xml_pipeline`` libavoid gating), but it has
    no notion of *this app's* label-placement convention. Unlike
    ``fix_edge_anchors_from_geometry``'s focused-view branch, this does **not**
    recompute routing — it only reads the waypoints libavoid already wrote and
    repositions each label onto a clear mid-segment.
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    page_name = _diagram_page_name(tree).lower()
    if page_name not in {"logical", "security", "data flow", "data_flow", "network"}:
        return xml
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    edges = [c for c in cells if c.get("edge") == "1"]
    if not edges:
        return xml

    from app.adapters.outbound.diagram.xml_diagnostics import _page_size
    page_w, page_h = _page_size(tree)

    edge_meta: dict[str, dict] = {}
    routed_paths: dict[str, list[tuple[float, float]]] = {}
    for edge in edges:
        eid = edge.get("id") or ""
        src_id, tgt_id = edge.get("source"), edge.get("target")
        if not eid or not src_id or not tgt_id or src_id not in by_id or tgt_id not in by_id:
            continue
        sx, sy = absolute_position(by_id[src_id], by_id)
        tx, ty = absolute_position(by_id[tgt_id], by_id)
        sgeo = _cell_geometry(by_id[src_id])
        tgeo = _cell_geometry(by_id[tgt_id])
        sw, sh = (sgeo[2], sgeo[3]) if sgeo else (78.0, 78.0)
        tw, th = (tgeo[2], tgeo[3]) if tgeo else (78.0, 78.0)
        edge_meta[eid] = {
            "scx": sx + sw / 2, "scy": sy + sh / 2,
            "tcx": tx + tw / 2, "tcy": ty + th / 2,
        }
        routed_paths[eid] = _edge_points(edge)

    obstacles = _logical_obstacle_rects(cells, by_id, set(), page_w, page_h)
    if _park_edge_labels_on_longest_segment(edges, edge_meta, routed_paths, obstacles):
        return _serialize_docroot(tree, root, is_mxfile, trimmed)
    return xml


def restamp_edge_ports_after_external_routing(xml: str) -> str:
    """Re-stamp exit/entry faces + even fan-out after libavoid without wiping routes.

    Libavoid can leave odd sides; this only updates port style keys and then
    re-parks labels.
    """
    stamped = fix_edge_anchors_from_geometry(xml, ports_only=True)
    return repark_edge_labels_after_external_routing(stamped)


def fix_edge_anchors_from_geometry(xml: str, *, ports_only: bool = False) -> str:
    """Set exit/entry anchors from geometry + direction scoring + even fan-out.

    On Logical/Security/Data-flow/Network pages, also inserts staggered
    waypoints so connectors do not run straight through intervening icons —
    unless ``ports_only`` (post-libavoid restamp).
    """
    parsed = _parse_docroot(xml)
    if parsed is None:
        return xml
    tree, root, is_mxfile, trimmed = parsed
    from app.adapters.outbound.diagram.xml_diagnostics import _page_size

    page_w, page_h = _page_size(tree)
    cells = [c for c in root.findall("mxCell") if c.get("id")]
    by_id = {c.get("id"): c for c in cells if c.get("id")}
    page = _diagram_page_name(tree).lower()
    is_focused = page in {"logical", "security", "data flow", "data_flow"}
    is_ir_layout = any(c.get("id") == "1" and c.get("layout") == "ir" for c in cells)
    enable_obstacle_routing = (is_focused or page == "network") and not ports_only
    parallel_px = _LOGICAL_PARALLEL_EDGE_OFFSET_PX if is_focused else _PARALLEL_EDGE_OFFSET_PX

    edges = [c for c in cells if c.get("edge") == "1"]
    if not edges:
        return xml

    corridor_buckets: dict[tuple, list[ET.Element]] = {}
    edge_meta: dict[str, dict] = {}
    # Per source/target cell: list of (edge, meta, primary) for fan-out.
    exit_incidents: dict[str, list[tuple[ET.Element, dict, bool]]] = {}
    entry_incidents: dict[str, list[tuple[ET.Element, dict, bool]]] = {}
    changed = False

    for edge in edges:
        src_id, tgt_id = edge.get("source"), edge.get("target")
        if not src_id or not tgt_id or src_id not in by_id or tgt_id not in by_id:
            continue
        sx, sy = absolute_position(by_id[src_id], by_id)
        tx, ty = absolute_position(by_id[tgt_id], by_id)
        sgeo = _cell_geometry(by_id[src_id])
        tgeo = _cell_geometry(by_id[tgt_id])
        sw = sgeo[2] if sgeo else 78
        sh = sgeo[3] if sgeo else 78
        tw = tgeo[2] if tgeo else 78
        th = tgeo[3] if tgeo else 78
        scx = sx + sw / 2
        scy = sy + sh / 2
        tcx = tx + tw / 2
        tcy = ty + th / 2

        eid = edge.get("id") or id(edge)
        pts = _edge_points(edge)
        skip_ids = {src_id, tgt_id}
        obstacles = _logical_obstacle_rects(cells, by_id, skip_ids, page_w, page_h)
        meta = {
            "sx": sx, "sy": sy, "sw": sw, "sh": sh,
            "tx": tx, "ty": ty, "tw": tw, "th": th,
            "scx": scx, "scy": scy, "tcx": tcx, "tcy": tcy,
        }
        faces = _pick_best_faces(meta, pts, obstacles)
        meta.update(faces)
        if faces["exit_axis"] == "h":
            meta["travel"] = (min(scx, tcx), max(scx, tcx))
            meta["axis"] = "h"
            lane = (round(scy / _CORRIDOR_LANE_PX), round(tcy / _CORRIDOR_LANE_PX))
            corridor = ("h", faces["exit_face"], faces["entry_face"], *lane)
        else:
            meta["travel"] = (min(scy, tcy), max(scy, tcy))
            meta["axis"] = "v"
            lane = (round(scx / _CORRIDOR_LANE_PX), round(tcx / _CORRIDOR_LANE_PX))
            corridor = ("v", faces["exit_face"], faces["entry_face"], *lane)

        edge_meta[str(eid)] = meta
        primary = _edge_is_primary(edge)

        style = edge.get("style") or ""
        updates = {
            "exitX": faces["exitX"],
            "exitY": faces["exitY"],
            "entryX": faces["entryX"],
            "entryY": faces["entryY"],
            "exitDx": "0",
            "exitDy": "0",
            "entryDx": "0",
            "entryDy": "0",
            "edgeStyle": "orthogonalEdgeStyle",
            "rounded": "1",
            "orthogonalLoop": "1",
            "jettySize": "auto",
            "html": "1",
        }
        new_style = _set_style_kv(style, updates)
        if new_style != style:
            edge.set("style", new_style)
            changed = True

        exit_incidents.setdefault(src_id, []).append((edge, meta, primary))
        entry_incidents.setdefault(tgt_id, []).append((edge, meta, primary))

        if is_ir_layout and pts and not ports_only:
            continue
        corridor_buckets.setdefault(corridor, []).append(edge)

    for _src, incidents in exit_incidents.items():
        if _assign_face_ports(incidents, end="exit"):
            changed = True
    for _tgt, incidents in entry_incidents.items():
        if _assign_face_ports(incidents, end="entry"):
            changed = True

    if ports_only:
        if changed:
            return _serialize_docroot(tree, root, is_mxfile, trimmed)
        return xml

    # CORE parallel-corridor offset: stagger Dx/Dy so stacked orthogonal paths
    # separate. Sequential hops (a→b→c) share a lane but never overlay, so only
    # edges whose travel ranges actually overlap are treated as parallel.
    band_offsets: dict[str, tuple[float, float]] = {}
    for corridor, bucket in corridor_buckets.items():
        axis = corridor[0]
        for group in _cluster_overlapping_edges(bucket, edge_meta):
            n = len(group)
            if n < 2:
                continue
            for i, edge in enumerate(group):
                slot = i - (n - 1) / 2.0
                meta = edge_meta.get(edge.get("id") or "") or {}
                span = min(meta.get("sh", 78.0), meta.get("th", 78.0)) if axis == "h" else \
                    min(meta.get("sw", 78.0), meta.get("tw", 78.0))
                limit = max(0.0, span / 2.0 - 10.0)
                offset = max(-limit, min(limit, slot * parallel_px))
                style = edge.get("style") or ""
                if axis == "h":
                    updates = {"exitDy": str(offset), "entryDy": str(offset)}
                else:
                    updates = {"exitDx": str(offset), "entryDx": str(offset)}
                edge.set("style", _set_style_kv(style, updates))
                band_offsets[edge.get("id") or ""] = (
                    slot * _CORRIDOR_BAND_STEP,
                    i * _CORRIDOR_BAND_STEP,
                )
                changed = True

    # Route each connector around other icons instead of through them.
    # Kept ELK routes are preserved; asymmetrically dropped ones (no points)
    # get a local obstacle route — do not wipe elbows that survived equalize.
    if enable_obstacle_routing:
        frames = [
            (*absolute_position(c, by_id), _cell_geometry(c)[2], _cell_geometry(c)[3])
            for c in cells
            if _is_container_cell(c)
            and _cell_geometry(c) is not None
            and (c.get("id") or "") not in {"overview_panel", "legend_box"}
        ]
        routed_paths: dict[str, list[tuple[float, float]]] = {}
        for edge in edges:
            eid = edge.get("id") or ""
            meta = edge_meta.get(eid)
            if not meta:
                continue
            pts = _edge_points(edge)
            if is_ir_layout and pts:
                routed_paths[eid] = pts
                continue
            obstacles = _logical_obstacle_rects(
                cells, by_id, {edge.get("source") or "", edge.get("target") or ""}, page_w, page_h
            )
            if _route_logical_edge(
                edge, meta, obstacles, band_offsets.get(eid, (0.0, 0.0)), frames
            ):
                changed = True
            routed_paths[eid] = _edge_points(edge)

        if _bundle_under_corridor_trunks(edges, edge_meta, routed_paths):
            changed = True
        all_obs = _logical_obstacle_rects(cells, by_id, set(), page_w, page_h)
        if _park_edge_labels_on_longest_segment(edges, edge_meta, routed_paths, all_obs):
            changed = True

    if not changed:
        return xml
    return _serialize_docroot(tree, root, is_mxfile, trimmed)

def _is_saas_leaf_cell(cell: ET.Element) -> bool:
    """True for curated enterprise SaaS product leaves (not PrivateLink endpoints).

    Excludes the Overview Panel / legend by id — found via real-capture replay
    (Fix plan's final verification step): an LLM occasionally emits
    ``overview_panel`` with a stray leftover ``shape_type="saas_*"`` attribute
    (a copy-paste artifact from a nearby SaaS leaf, not a real classification
    choice), which without this guard gets reparented into a SaaS shelf and
    balloons it — the panel's own text-block geometry (hundreds of px) reads
    as a single oversized "icon" to the grid-sizing math.
    """
    if cell.get("vertex") != "1" or cell.get("edge") == "1" or _is_container_cell(cell):
        return False
    if (cell.get("id") or "") in {"overview_panel", "legend_box"}:
        return False
    st = _shape(cell)
    if st.startswith("saas_"):
        return True
    from app.adapters.outbound.diagram.architecture_docs import match_saas_shape_type
    return match_saas_shape_type(_plain_label(cell)) is not None


def _diagram_page_name(tree: ET.Element) -> str:
    if tree.tag == "mxfile":
        diag = tree.find("diagram")
        if diag is not None:
            return (diag.get("name") or "").strip()
    return ""

def _is_anchored_shelf(page_name: str, cell: ET.Element) -> bool:
    """True for a corridor container anchored *under* another, not part of the L→R row.

    ``domain_saas`` (Logical), ``zone_external_saas`` (Security), and
    ``phase_consume`` / ``phase_saas`` (Data flow) are shelves, not main-row columns.
    Used by ``xml_diagnostics.validate_layout`` tier-height checks.
    """
    cid = (cell.get("id") or "").lower()
    if page_name == "logical":
        return cid.startswith("domain_saas") or ("saas" in cid and cid.startswith("domain_"))
    if page_name == "security":
        return cid.startswith("zone_external_saas") or ("saas" in cid and cid.startswith("zone_"))
    if page_name in {"data flow", "data_flow"}:
        return cid.startswith(("phase_consume", "phase_saas", "phase_control"))
    return False

