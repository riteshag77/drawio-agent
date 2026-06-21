"""Orthogonal edge routing inspired by next-ai-draw-io edge rules."""

from __future__ import annotations

MARGIN = 28
LANE_STEP = 24
CLEARANCE = 32
_SLOT_POSITIONS = (0.25, 0.35, 0.5, 0.65, 0.75)


def _slot_value(slot: int) -> float:
    return _SLOT_POSITIONS[slot % len(_SLOT_POSITIONS)]


def _box_center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


def _expand_rect(box: tuple[int, int, int, int], pad: int) -> tuple[int, int, int, int]:
    x, y, w, h = box
    return x - pad, y - pad, w + 2 * pad, h + 2 * pad


def _rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _segment_intersects_rect(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    rect: tuple[int, int, int, int],
) -> bool:
    rx, ry, rw, rh = rect
    if rw <= 0 or rh <= 0:
        return False

    left, right = min(x1, x2), max(x1, x2)
    top, bottom = min(y1, y2), max(y1, y2)
    if right < rx or left > rx + rw or bottom < ry or top > ry + rh:
        return False

    # Horizontal segment
    if abs(y1 - y2) < 1:
        y = y1
        if rx <= x1 <= rx + rw or rx <= x2 <= rx + rw:
            return ry <= y <= ry + rh
        return False

    # Vertical segment
    if abs(x1 - x2) < 1:
        x = x1
        if ry <= y1 <= ry + rh or ry <= y2 <= ry + rh:
            return rx <= x <= rx + rw
        return False

    return _rects_overlap(
        (int(left), int(top), int(max(1, right - left)), int(max(1, bottom - top))),
        rect,
    )


def _orthogonal_path_blocked(
    points: list[tuple[float, float]],
    obstacles: list[tuple[int, int, int, int]],
) -> bool:
    for i in range(len(points) - 1):
        x1, y1 = points[i]
        x2, y2 = points[i + 1]
        for obs in obstacles:
            if _segment_intersects_rect(x1, y1, x2, y2, obs):
                return True
    return False


def _direct_horizontal_path(
    src_box: tuple[int, int, int, int],
    tgt_box: tuple[int, int, int, int],
    exit_y: float,
    entry_y: float,
) -> list[tuple[float, float]]:
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    return [
        (sx + sw, sy + sh * exit_y),
        (tx, ty + th * entry_y),
    ]


def _direct_vertical_path(
    src_box: tuple[int, int, int, int],
    tgt_box: tuple[int, int, int, int],
    exit_x: float,
    entry_x: float,
) -> list[tuple[float, float]]:
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    return [
        (sx + sw * exit_x, sy + sh),
        (tx + tw * entry_x, ty),
    ]


def route_edge(
    src_box: tuple[int, int, int, int],
    tgt_box: tuple[int, int, int, int],
    obstacles: list[tuple[int, int, int, int]],
    *,
    exit_slot: int = 0,
    entry_slot: int = 0,
    lane: int = 0,
    prefer_horizontal: bool = True,
) -> tuple[float, float, float, float, list[tuple[int, int]]]:
    """
    Return (exitX, exitY, entryX, entryY, waypoints) for a draw.io edge.

    Follows next-ai-draw-io rules:
    - L→R flow uses right-side exit and left-side entry (never corners)
    - Multiple edges stagger attachment points
    - Obstacles trigger perimeter routing with explicit mxPoint waypoints
    """
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    scx, scy = _box_center(src_box)
    tcx, tcy = _box_center(tgt_box)
    dx, dy = tcx - scx, tcy - scy

    horizontal = prefer_horizontal and abs(dx) >= abs(dy)

    if horizontal:
        exit_x, entry_x = 1.0, 0.0
        exit_y = _slot_value(exit_slot)
        entry_y = _slot_value(entry_slot)
        direct = _direct_horizontal_path(src_box, tgt_box, exit_y, entry_y)
        if not _orthogonal_path_blocked(direct, obstacles):
            return exit_x, exit_y, entry_x, entry_y, []

        src_px = sx + sw
        src_py = sy + sh * exit_y
        tgt_px = tx
        tgt_py = ty + th * entry_y
        lane_offset = lane * LANE_STEP

        # Route above the diagram band first (cleaner for cloud arch), else below.
        top_band = min(sy, ty) - CLEARANCE - lane_offset
        bottom_band = max(sy + sh, ty + th) + CLEARANCE + lane_offset
        corridor_y = top_band if top_band >= 20 else bottom_band

        out_x = int(src_px + MARGIN)
        in_x = int(tgt_px - MARGIN)
        waypoints = [
            (out_x, int(src_py)),
            (out_x, int(corridor_y)),
            (in_x, int(corridor_y)),
            (in_x, int(tgt_py)),
        ]
        return exit_x, exit_y, entry_x, entry_y, waypoints

    # Vertical routing (e.g. to bottom-row services / databases)
    exit_x = _slot_value(exit_slot)
    entry_x = _slot_value(entry_slot)
    exit_y, entry_y = 1.0, 0.0
    direct = _direct_vertical_path(src_box, tgt_box, exit_x, entry_x)
    if not _orthogonal_path_blocked(direct, obstacles):
        return exit_x, exit_y, entry_x, entry_y, []

    src_px = sx + sw * exit_x
    src_py = sy + sh
    tgt_px = tx + tw * entry_x
    tgt_py = ty
    lane_offset = lane * LANE_STEP

    left_band = min(sx, tx) - CLEARANCE - lane_offset
    right_band = max(sx + sw, tx + tw) + CLEARANCE + lane_offset
    corridor_x = left_band if left_band >= 20 else right_band

    out_y = int(src_py + MARGIN)
    in_y = int(tgt_py - MARGIN)
    waypoints = [
        (int(src_px), out_y),
        (int(corridor_x), out_y),
        (int(corridor_x), in_y),
        (int(tgt_px), in_y),
    ]
    return exit_x, exit_y, entry_x, entry_y, waypoints


def collect_obstacles(
    node_bounds: dict[str, tuple[int, int, int, int]],
    *,
    exclude: set[str],
    include_containers: bool = True,
    container_ids: set[str] | None = None,
) -> list[tuple[int, int, int, int]]:
    """Build obstacle list for routing (expanded slightly for clearance)."""
    obstacles: list[tuple[int, int, int, int]] = []
    containers = container_ids or set()
    for nid, box in node_bounds.items():
        if nid in exclude:
            continue
        if not include_containers and nid in containers:
            continue
        obstacles.append(_expand_rect(box, 8))
    return obstacles
