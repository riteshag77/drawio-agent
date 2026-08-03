"""Compiler building blocks shared by every view's compiler.

Found by comparing ``compiler/network.py`` against ``compiler/corridor.py``
once both existed: ``CompilerError``, the laid-out-box shape, leaf-grid
emission, and flow/actor emission were byte-for-byte or near-identical in
both — the compilers differ in *hierarchy* (Network nests containers three
deep; the corridor views place a flat row), not in how a grid of icons, a set
of edges, or a column of external actors gets written. This module is that
shared part; each view compiler owns only its own hierarchy-walking logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.domain.ir.base import ExternalActor, Flow, Leaf


class CompilerError(ValueError):
    """Raised when an IR cannot be compiled. Should be unreachable for a validated IR."""


@dataclass
class Box:
    """A laid-out container's outer size, before it is positioned in its parent."""

    width: float
    height: float


def leaf_label(leaf: Leaf) -> str:
    """Render a leaf's label, appending its optional annotation as a bracket
    suffix (``"RDS [PII]"``) — the same convention Data-flow edges already use
    for ``[PII]`` tags, applied to leaves. Shared so every leaf-rendering call
    site (the grid helper below, plus Network's gateway/connectivity-hub
    singletons which bypass the grid) stays consistent."""
    if not leaf.annotation:
        return leaf.label
    # Models sometimes nest brackets ("Multi-AZ [PHI]") — flatten so we never
    # emit ``Label [Multi-AZ [PHI]]``.
    ann = re.sub(r"[\[\]]", "", leaf.annotation).strip()
    if not ann:
        return leaf.label
    suffix = f"[{ann}]"
    if leaf.label.rstrip().endswith(suffix):
        return leaf.label
    return f"{leaf.label} [{ann}]"


def grid_box(count: int, *, is_group: bool) -> Box:
    """Outer size a container needs to hold a ``count``-icon grid.

    Split out of ``emit_leaf_grid`` so a caller can *measure* a grid without
    emitting it. Network's zone layout needs that: to align subnet tiers across
    peer availability zones it must know every subnet's required size before it
    can decide where any of them goes (see ``network.py:_emit_vpc``).
    """
    content_w, content_h, _cols, _rows = geo.grid_size(count, is_group=is_group)
    return Box(width=content_w + geo.PAD * 2, height=geo.TITLE_PAD + content_h + geo.PAD)


def emit_leaf_grid(writer: CellWriter, leaves: list[Leaf], parent_id: str, *, is_group: bool) -> Box:
    """Grid ``leaves`` inside ``parent_id`` and return the parent's required outer size.

    The one leaf-gridding algorithm in the codebase — Network's subnets/edge-
    layer/cross-cutting-groups and every corridor view's corridors all place
    their leaves this same way (geometry_spec's canonical grid, capped at
    4/row), so there is exactly one implementation of it.

    Leaf positions depend only on the grid, never on the parent's final size, so
    a caller that stretches its container beyond ``grid_box`` (Network's
    tier-aligned subnets) can emit leaves through this helper and then size the
    container itself.
    """
    _content_w, _content_h, cols, _rows = geo.grid_size(len(leaves), is_group=is_group)
    pitch = geo.pitch_for(is_group)
    for index, leaf in enumerate(leaves):
        col, row = index % cols, index // cols
        writer.vertex(
            id=leaf.id,
            label=leaf_label(leaf),
            style=theme.leaf_style(leaf.criticality),
            x=geo.PAD + col * pitch,
            y=geo.TITLE_PAD + row * geo.ROW_PITCH_Y,
            w=geo.LEAF_W,
            h=geo.LEAF_H,
            parent=parent_id,
            shape_type=leaf.shape_type,
            criticality=leaf.criticality,
        )
    return grid_box(len(leaves), is_group=is_group)


def emit_external_actors(
    writer: CellWriter,
    actors: list[ExternalActor],
    *,
    left_x: float,
    right_x: float,
    top_y: float,
    flows: list[Flow] | None = None,
    endpoint_cy: dict[str, float] | None = None,
    band_hi: float | None = None,
) -> None:
    """Place external actors at the root layer: ingress left, egress right.

    Same algorithm for every view (only the ``left_x``/``right_x``/``top_y``
    origins differ, since Network's origin is the Cloud box and the corridor
    views' is the row) — callers compute those from their own layout and pass
    them in rather than each reimplementing the partition-and-stack loop.

    When ``flows`` + ``endpoint_cy`` are provided, each column is sorted by the
    median Y of connected primary endpoints (clamped to the corridor/cloud band).
    """
    left = [a for a in actors if a.kind in ("users", "saas_ingress", "on_prem")]
    right = [a for a in actors if a.kind == "saas_egress"]
    hi = band_hi if band_hi is not None else top_y + 400.0
    if flows and endpoint_cy:
        from app.adapters.outbound.diagram.logical_layout import actor_sort_key_by_endpoint_y

        def _sort_col(col: list[ExternalActor]) -> list[ExternalActor]:
            keyed = [
                (actor_sort_key_by_endpoint_y(
                    a.id, flows, endpoint_cy, band_lo=top_y, band_hi=hi, fallback_index=i,
                ), a)
                for i, a in enumerate(col)
            ]
            keyed.sort(key=lambda t: t[0])
            return [a for _, a in keyed]

        left = _sort_col(left)
        right = _sort_col(right)

    for column, x in ((left, left_x), (right, right_x)):
        y = top_y
        if flows and endpoint_cy and column:
            # Place each actor near its median port Y when spacing allows.
            from app.adapters.outbound.diagram.logical_layout import actor_sort_key_by_endpoint_y
            placed_y = top_y
            for i, actor in enumerate(column):
                pref, _, _ = actor_sort_key_by_endpoint_y(
                    actor.id, flows, endpoint_cy, band_lo=top_y, band_hi=hi, fallback_index=i,
                )
                y = max(placed_y, pref - geo.LEAF_H / 2.0)
                writer.vertex(
                    id=actor.id,
                    label=actor.label,
                    style=theme.actor_style(actor.kind),
                    x=x, y=y, w=geo.LEAF_W, h=geo.LEAF_H,
                    parent="1",
                )
                placed_y = y + geo.LEAF_H + geo.LABEL_BAND_H + geo.ACTOR_GAP_Y
            continue
        for actor in column:
            writer.vertex(
                id=actor.id,
                label=actor.label,
                style=theme.actor_style(actor.kind),
                x=x, y=y, w=geo.LEAF_W, h=geo.LEAF_H,
                parent="1",
            )
            y += geo.LEAF_H + geo.LABEL_BAND_H + geo.ACTOR_GAP_Y


def flow_id(index: int) -> str:
    """Canonical id for the ``index``-th flow — shared so an ELK edge-route
    lookup (keyed by this same id) and ``emit_flows`` never drift apart."""
    return f"flow_{index}"


def emit_flows(
    writer: CellWriter,
    flows: list[Flow],
    *,
    routes: dict[str, list[tuple[float, float]]] | None = None,
) -> None:
    """Emit every flow as a root-layer edge. Identical for every view.

    ``routes`` (if given) maps a flow's id (see ``flow_id``) to the
    intermediate bend points an external layout pass (ELK) computed for it —
    absent/omitted flows fall back to no waypoints, i.e. draw.io's own
    client-side edge routing, exactly today's behavior.
    """
    for index, flow in enumerate(flows):
        fid = flow_id(index)
        writer.edge(
            id=fid,
            source=flow.source,
            target=flow.target,
            label=flow.label,
            style=theme.flow_style(
                primary=flow.primary, labelled=bool(flow.label), classification=flow.classification,
            ),
            points=(routes or {}).get(fid),
            classification=flow.classification,
            protection=flow.protection,
        )
