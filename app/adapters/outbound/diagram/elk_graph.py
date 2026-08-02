"""Shared ELK graph-building/parsing helpers for the Network and Corridor
compilers.

Two different leaf-container shapes are used, deliberately:

- **Corridor** needs precise per-leaf edge routing (that's the whole point
  of its ELK migration — see ``logical_layout.py``), so its corridors are
  compound nodes with each leaf as a real ELK child (``leaf_grid_container``,
  partitioned into rows of ``LEAF_MAX_COLS``).
- **Network** carries no real flow edges into ELK at all (see
  ``compiler/elk_layout_network.py``'s module docstring), so a leaf-grid
  container (subnet/group/edge-layer/hub-shelf) only needs to be *sized*
  correctly (``sized_container_node`` + ``leaf_grid_size``, no ELK
  children) — leaves are placed by ``compiler/shared.emit_leaf_grid`` once
  the container's final position is known, same math either way.

``band_transition_edges`` and ``MODEL_ORDER_OPTIONS`` are Network's
mechanism for deterministic sibling order — see
``compiler/elk_layout_network.py``'s module docstring for the "band" vs
"row" distinction they implement.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError
from app.domain.ir.base import Leaf

#: Gap (edge-to-edge, not pitch) between leaves in the same row/column — ELK
#: wants a gap, geometry_spec's constants are a pitch (left-edge to left-edge).
#: Cross-cutting shelves use a denser pitch than data-path rows/subnets (see
#: geometry_spec.pitch_for) — same distinction, kept as two named gaps rather
#: than always deriving from the same constant.
LEAF_GAP_X = max(4.0, geo.DATA_CANONICAL_PITCH - geo.LEAF_W)
# Pitch between leaf tops is ROW_PITCH_Y; nodes now include LABEL_BAND_H so the
# inter-layer gap is the residual (not ROW_PITCH_Y - LEAF_H alone).
LEAF_GAP_Y = max(4.0, geo.ROW_PITCH_Y - geo.LEAF_H - geo.LABEL_BAND_H)
GROUP_GAP_X = max(4.0, geo.GROUP_CANONICAL_PITCH - geo.LEAF_W)
LEAF_MAX_COLS = geo.LEAF_GRID_MAX_COLS

#: Graph-wide options that make edge-free same-rank siblings ("rows" — see
#: elk_layout_network.py) land side by side in declaration order instead of
#: ELK's default arbitrary component ordering. Confirmed empirically: without
#: these, 5 edge-free sibling nodes came back in a scrambled order
#: (`l2,l0,l5,l6,l1,l3,l4`) rather than declaration order.
MODEL_ORDER_OPTIONS = {
    "elk.separateConnectedComponents": "false",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    "elk.layered.crossingMinimization.forceNodeModelOrder": "true",
}


def leaf_grid_size(leaves: list[Leaf], *, is_group: bool = False) -> tuple[float, float]:
    """Outer (width, height) a container needs for ``leaves`` — mirrors
    ``compiler/shared.grid_box`` exactly (same constants, same formula),
    duplicated rather than imported to keep this module dependency-free of
    the compiler package (which itself imports this module)."""
    content_w, content_h, _cols, _rows = geo.grid_size(len(leaves), is_group=is_group)
    return content_w + geo.PAD * 2, geo.TITLE_PAD + content_h + geo.PAD


def sized_container_node(node_id: str, width: float, height: float) -> dict:
    """An ELK node sized to hold a leaf grid — no ELK children (Network's
    leaf containers; see module docstring)."""
    return {"id": node_id, "width": width, "height": height}


def elk_leaf_node(leaf_id: str, index: int) -> dict:
    """One leaf as an ELK node, partitioned into rows of ``LEAF_MAX_COLS``.

    Height includes ``LABEL_BAND_H`` because corridor leaves use
    ``verticalLabelPosition=bottom`` — without the band, shelves and sibling
    rows sit under the hanging label and look overlapped.
    """
    return {
        "id": leaf_id,
        "width": geo.LEAF_W,
        "height": geo.LEAF_H + geo.LABEL_BAND_H,
        "layoutOptions": {"elk.partitioning.partition": str(index // LEAF_MAX_COLS)},
    }


def leaf_grid_container(
    node_id: str, leaves: list[Leaf], *,
    is_group: bool = False,
    extra_options: dict[str, str] | None = None,
) -> dict:
    """A compound ELK node holding ``leaves`` as real children (Corridor
    only — see module docstring) — direction DOWN, partitioned rows,
    canonical spacing. ``is_group=True`` for a denser pitch."""
    options = {
        "elk.direction": "DOWN",
        "elk.partitioning.activate": "true",
        "elk.spacing.nodeNode": str(GROUP_GAP_X if is_group else LEAF_GAP_X),
        "elk.layered.spacing.nodeNodeBetweenLayers": str(LEAF_GAP_Y),
    }
    if extra_options:
        options.update(extra_options)
    return {
        "id": node_id,
        "layoutOptions": options,
        "children": [elk_leaf_node(leaf.id, i) for i, leaf in enumerate(leaves)],
    }


_BAND_EDGE_PREFIX = "__band__"


def band_transition_edges(bands: list[list[str]]) -> list[dict]:
    """Synthetic edges from every id in band ``i`` to every id in band
    ``i + 1``, for consecutive bands — forces ELK's ``layered`` algorithm to
    rank each band strictly after the previous one (a real dependency edge
    reliably ranks its endpoints in sequence; this just declares one rather
    than inferring it from a flow). A full fan between bands, not one edge
    per pair, because a same-band sibling with no edge of its own would
    otherwise default to rank 0 along with the first band instead of its
    band's actual rank (confirmed empirically with a single-source fan-out).
    These ids never collide with a real flow id (``compiler/shared.flow_id``
    only ever produces ``flow_<n>``) and are never looked up in
    ``edge_routes``' result, so they're invisible to callers.
    """
    edges: list[dict] = []
    for i, (band_a, band_b) in enumerate(zip(bands, bands[1:])):
        for j, a in enumerate(band_a):
            for k, b in enumerate(band_b):
                edges.append({"id": f"{_BAND_EDGE_PREFIX}{i}_{j}_{k}", "sources": [a], "targets": [b]})
    return edges


def elk_children_by_id(node: dict) -> dict[str, dict]:
    return {c["id"]: c for c in node.get("children", [])}


def require_child(node: dict, child_id: str) -> dict:
    child = elk_children_by_id(node).get(child_id)
    if child is None:
        raise ElkLayoutError(f"ELK result missing node {child_id!r}")
    return child


def edge_routes(result: dict) -> dict[str, list[tuple[float, float]]]:
    """``{edge_id: [intermediate bend points]}`` for every edge ELK actually
    needed to bend — a straight line (no bend points) is a valid route too
    and is simply absent from the returned dict."""
    routes: dict[str, list[tuple[float, float]]] = {}
    for edge in result.get("edges", []):
        sections = edge.get("sections") or []
        if not sections:
            continue
        bend_points = sections[0].get("bendPoints") or []
        if bend_points:
            routes[edge["id"]] = [(p["x"], p["y"]) for p in bend_points]
    return routes
