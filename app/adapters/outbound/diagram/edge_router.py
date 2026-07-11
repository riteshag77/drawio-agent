"""Edge anchors — Draw.io / MCP libavoid owns the path; we only pick facing sides."""

from __future__ import annotations


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
    """Return fixed mid-face anchors (exitX, exitY, entryX, entryY, []).

    Slot/lane args kept for call-site compatibility; ignored on purpose.
    """
    sx, sy, sw, sh = src_box
    tx, ty, tw, th = tgt_box
    scx, scy = sx + sw / 2, sy + sh / 2
    tcx, tcy = tx + tw / 2, ty + th / 2
    dx, dy = tcx - scx, tcy - scy

    use_horizontal = abs(dx) >= abs(dy) if prefer_horizontal else abs(dx) > abs(dy) * 1.25
    if use_horizontal:
        if dx >= 0:
            return 1.0, 0.5, 0.0, 0.5, []
        return 0.0, 0.5, 1.0, 0.5, []
    if dy >= 0:
        return 0.5, 1.0, 0.5, 0.0, []
    return 0.5, 0.0, 0.5, 1.0, []


def collect_obstacles(node_bounds, *, exclude, include_containers=True, container_ids=None):
    """No-op kept for API compatibility with older tests/callers."""
    return []
