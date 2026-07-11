"""Edge anchor points from absolute node positions. Draw.io routes the path natively."""

from __future__ import annotations

from app.adapters.outbound.diagram.layout_settings import layout_settings


def _slot_value(slot: int) -> float:
    slots = layout_settings().face_slots
    return slots[slot % len(slots)]


def _box_center(box: tuple[int, int, int, int]) -> tuple[float, float]:
    x, y, w, h = box
    return x + w / 2, y + h / 2


def route_edge(
    src_box: tuple[int, int, int, int],
    tgt_box: tuple[int, int, int, int],
    obstacles: list[tuple[int, int, int, int]] | None = None,
    *,
    exit_slot: int = 0,
    entry_slot: int = 0,
    lane: int = 0,
    prefer_horizontal: bool = True,
) -> tuple[float, float, float, float, list[tuple[int, int]]]:
    """Return (exitX, exitY, entryX, entryY, waypoints).

    Face-to-face anchors with slot spreading so parallel edges do not stack.
    """
    scx, scy = _box_center(src_box)
    tcx, tcy = _box_center(tgt_box)
    dx, dy = tcx - scx, tcy - scy
    exit_off = _slot_value(exit_slot)
    entry_off = _slot_value(entry_slot)
    # Slight lane bias keeps parallel orthogonal segments from coinciding
    lane_nudge = (lane % 5) * 0.04

    use_horizontal = abs(dx) >= abs(dy) if prefer_horizontal else abs(dx) > abs(dy) * 1.25

    if use_horizontal:
        ey = min(0.85, max(0.15, exit_off + (lane_nudge if dx >= 0 else -lane_nudge)))
        iy = min(0.85, max(0.15, entry_off - (lane_nudge if dx >= 0 else -lane_nudge)))
        if dx >= 0:
            return 1.0, ey, 0.0, iy, []
        return 0.0, ey, 1.0, iy, []

    ex = min(0.85, max(0.15, exit_off + lane_nudge))
    ix = min(0.85, max(0.15, entry_off - lane_nudge))
    if dy >= 0:
        return ex, 1.0, ix, 0.0, []
    return ex, 0.0, ix, 1.0, []


def collect_obstacles(node_bounds, *, exclude, include_containers=True, container_ids=None):
    """No-op kept for API compatibility with older tests/callers."""
    return []
