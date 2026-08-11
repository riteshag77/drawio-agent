"""Shared ELK graph-building/parsing helpers for the Network and Corridor
compilers.

Both compilers use the same leaf-container shape: a container is handed to
ELK *sized but childless* (``sized_container_node`` + ``leaf_grid_size``),
and its leaves are placed separately once the container's own final position
is known — by ``compiler/shared.emit_leaf_grid`` for Network, by
``logical_layout._place_leaves`` for Corridor.

Corridor briefly did the other thing — each leaf as a real ELK child, so
leaf-level cross-corridor edges could be routed by ELK directly. That
required ``elk.hierarchyHandling: INCLUDE_CHILDREN``, which makes one
algorithm instance lay out the whole hierarchy in the root's direction and
therefore silently discarded each corridor's own direction and partitioned
row wrap (measured: a 526×420 grid rendered as a 126×1784 single column).
See ``logical_layout._build_elk_corridor_graph``. Corridor now collapses
those edges onto the corridors themselves, so ELK routes lanes between
boxes and the per-leaf anchoring is finished by
``edge_routing.fix_edge_anchors_from_geometry`` — the same division of
labour Network already used.

``band_transition_edges`` and ``MODEL_ORDER_OPTIONS`` are Network's
mechanism for deterministic sibling order — see
``compiler/elk_layout_network.py``'s module docstring for the "band" vs
"row" distinction they implement.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError
from app.domain.ir.base import Leaf

#: Max leaves per grid row. No ELK option derives from this any more (leaf
#: placement is deterministic on both compilers — see the module docstring);
#: it is re-exported here only because ``logical_layout`` reads it as the
#: canonical column cap.
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
    """An ELK node sized to hold a leaf grid — no ELK children. Used by both
    compilers for every leaf container; see the module docstring."""
    return {"id": node_id, "width": width, "height": height}


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
