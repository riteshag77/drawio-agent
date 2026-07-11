"""Edge anchors — face slots offset fan-out; Draw.io / MCP libavoid owns the path."""

from __future__ import annotations

from app.adapters.outbound.diagram.layout_settings import layout_settings


def _slot_t(slot: int) -> float:
    slots = layout_settings().face_slots or (0.5, 0.35, 0.65, 0.25, 0.75)
    if not slots:
        return 0.5
    return float(slots[slot % len(slots)])


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
    """Return face anchors (exitX, exitY, entryX, entryY, []).

    Slot indices pick offsets along the chosen face so fan-out edges diverge.
    ``obstacles`` / ``lane`` kept for call-site compatibility; unused.
    """
    _ = obstacles, lane
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    scx, scy = sx + sw / 2, sy + sh / 2
    tcx, tcy = tx + tw / 2, ty + th / 2
    dx, dy = tcx - scx, tcy - scy

    exit_t = _slot_t(exit_slot)
    entry_t = _slot_t(entry_slot)

    use_horizontal = abs(dx) >= abs(dy) if prefer_horizontal else abs(dx) > abs(dy) * 1.25
    if use_horizontal:
        if dx >= 0:
            return 1.0, exit_t, 0.0, entry_t, []
        return 0.0, exit_t, 1.0, entry_t, []
    if dy >= 0:
        return exit_t, 1.0, entry_t, 0.0, []
    return exit_t, 0.0, entry_t, 1.0, []


def collect_obstacles(node_bounds, *, exclude, include_containers=True, container_ids=None):
    """No-op kept for API compatibility with older tests/callers."""
    return []
