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

    Anchors connect face-to-face: right→left for L→R flow, bottom→top for T→B, etc.
    """
    scx, scy = _box_center(src_box)
    tcx, tcy = _box_center(tgt_box)
    dx, dy = tcx - scx, tcy - scy
    exit_off = _slot_value(exit_slot)
    entry_off = _slot_value(entry_slot)

    if abs(dx) >= abs(dy):
        if dx >= 0:
            return 1.0, exit_off, 0.0, entry_off, []
        return 0.0, exit_off, 1.0, entry_off, []

    if dy >= 0:
        return exit_off, 1.0, entry_off, 0.0, []
    return exit_off, 0.0, entry_off, 1.0, []


def collect_obstacles(node_bounds, *, exclude, include_containers=True, container_ids=None):
    """No-op kept for API compatibility."""
    return []
