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
    return f"{leaf.label} [{leaf.annotation}]" if leaf.annotation else leaf.label


def emit_leaf_grid(writer: CellWriter, leaves: list[Leaf], parent_id: str, *, is_group: bool) -> Box:
    """Grid ``leaves`` inside ``parent_id`` and return the parent's required outer size.

    The one leaf-gridding algorithm in the codebase — Network's subnets/edge-
    layer/cross-cutting-groups and every corridor view's corridors all place
    their leaves this same way (geometry_spec's canonical grid, capped at
    4/row), so there is exactly one implementation of it.
    """
    content_w, content_h, cols, _rows = geo.grid_size(len(leaves), is_group=is_group)
    pitch = geo.pitch_for(is_group)
    for index, leaf in enumerate(leaves):
        col, row = index % cols, index // cols
        writer.vertex(
            id=leaf.id,
            label=leaf_label(leaf),
            style=theme.leaf_style(),
            x=geo.PAD + col * pitch,
            y=geo.TITLE_PAD + row * geo.ROW_PITCH_Y,
            w=geo.LEAF_W,
            h=geo.LEAF_H,
            parent=parent_id,
            shape_type=leaf.shape_type,
        )
    return Box(
        width=content_w + geo.PAD * 2,
        height=geo.TITLE_PAD + content_h + geo.PAD,
    )


def emit_external_actors(
    writer: CellWriter,
    actors: list[ExternalActor],
    *,
    left_x: float,
    right_x: float,
    top_y: float,
) -> None:
    """Place external actors at the root layer: ingress left, egress right.

    Same algorithm for every view (only the ``left_x``/``right_x``/``top_y``
    origins differ, since Network's origin is the Cloud box and the corridor
    views' is the row) — callers compute those from their own layout and pass
    them in rather than each reimplementing the partition-and-stack loop.
    """
    left = [a for a in actors if a.kind in ("users", "saas_ingress", "on_prem")]
    right = [a for a in actors if a.kind == "saas_egress"]

    for column, x in ((left, left_x), (right, right_x)):
        y = top_y
        for actor in column:
            writer.vertex(
                id=actor.id,
                label=actor.label,
                style=theme.actor_style(),
                x=x, y=y, w=geo.LEAF_W, h=geo.LEAF_H,
                parent="1",
            )
            y += geo.LEAF_H + geo.LABEL_BAND_H + geo.ACTOR_GAP_Y


def emit_flows(writer: CellWriter, flows: list[Flow]) -> None:
    """Emit every flow as a root-layer edge. Identical for every view."""
    for index, flow in enumerate(flows):
        writer.edge(
            id=f"flow_{index}",
            source=flow.source,
            target=flow.target,
            label=flow.label,
            style=theme.flow_style(primary=flow.primary, labelled=bool(flow.label)),
        )
