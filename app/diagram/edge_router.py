"""Simplified edge routing - no obstacle detection or explicit waypoints.

Draw.io's orthogonal edge routing handles pathfinding natively when waypoints
are omitted. This module only calculates optimal exit/entry connection points
based on relative positions of source and target nodes.
"""

from __future__ import annotations

_SLOT_POSITIONS = (0.25, 0.35, 0.5, 0.65, 0.75)


def _slot_value(slot: int) -> float:
    return _SLOT_POSITIONS[slot % len(_SLOT_POSITIONS)]


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

    Simplified version: calculates exit/entry points from relative positions.
    Always returns empty waypoints so draw.io routes the edge natively.
    """
    scx, scy = _box_center(src_box)
    tcx, tcy = _box_center(tgt_box)
    dx, dy = tcx - scx, tcy - scy

    horizontal = prefer_horizontal and abs(dx) >= abs(dy)

    if horizontal:
        if dx >= 0:
            return 1.0, _slot_value(exit_slot), 0.0, _slot_value(entry_slot), []
        return 0.0, _slot_value(exit_slot), 1.0, _slot_value(entry_slot), []

    if dy >= 0:
        return _slot_value(exit_slot), 1.0, _slot_value(entry_slot), 0.0, []
    return _slot_value(exit_slot), 0.0, _slot_value(entry_slot), 1.0, []


def collect_obstacles(
    node_bounds: dict[str, tuple[int, int, int, int]],
    *,
    exclude: set[str],
    include_containers: bool = True,
    container_ids: set[str] | None = None,
) -> list[tuple[int, int, int, int]]:
    """No-op kept for API compatibility. Returns empty list."""
    return []
