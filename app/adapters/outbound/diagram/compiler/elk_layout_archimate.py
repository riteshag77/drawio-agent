"""ELK container geometry for the ArchiMate compiler.

Same hierarchical approach ``elk_layout_network.py`` uses for
Cloud > Region > VPC > Zone > Subnet, applied to ArchiMate's own nesting:

    layer band  (business / application / technology, stacked top to bottom)
      domain grouping   (a row of peer boxes inside a band, wrapping)
        elements        (placed by a grid helper, not by ELK — see below)

and the two flat viewpoints, where the same machinery collapses to a single
band with no layer stacking at all.

Three things carry over verbatim from the Network layout module, for the
reasons documented at length there:

* One direction (``DOWN``) for the whole graph — ``INCLUDE_CHILDREN`` cannot
  mix directions (elkjs#26).
* **Bands** (things that stack: the three layers; wrapped rows of groupings)
  are declared with ``band_transition_edges``; **rows** (peer groupings on one
  rank) carry no edges among themselves and rely on ``MODEL_ORDER_OPTIONS`` to
  land in declaration order.
* Element positions *inside* a band or grouping are NOT ELK's job. The
  container is a pre-sized node and elements are gridded into it afterwards by
  ``compiler/archimate.py``, exactly as Network does for subnets — edge-free
  siblings do not reliably grid under ``layered``, and the grid is trivially
  computable anyway.

No real relationship edges go into this graph either. Giving ELK the
relationships would fight the synthetic band edges (confirmed on the Network
side: one real cross-hierarchy edge overrode a row's ordering), and the local
obstacle router already handles ArchiMate's edges — which run mostly vertically
between bands, the easiest case it has.
"""

from __future__ import annotations

from app.adapters.outbound.diagram import elk_graph
from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError, run_elk
from app.adapters.outbound.diagram.logical_layout import Geo

__all__ = ["ArchimateLayoutPlan", "PlannedBand", "PlannedGroup", "layout_archimate_plan"]


class PlannedGroup:
    """One grouping box inside a band, with the elements gridded into it."""

    __slots__ = ("id", "label", "element_ids")

    def __init__(self, group_id: str, label: str, element_ids: list[str]) -> None:
        self.id = group_id
        self.label = label
        self.element_ids = element_ids


class PlannedBand:
    """One band: either a flat grid of elements, or a set of grouping boxes.

    The two are mutually exclusive by construction (``compiler/archimate.py``
    decides which shape a band takes from its element count), which is what
    keeps the ELK node builder below to two straightforward cases instead of a
    mixed layout with loose elements floating beside grouping boxes.
    """

    __slots__ = ("id", "label", "layer", "groups", "element_ids")

    def __init__(
        self,
        band_id: str,
        label: str,
        layer: str,
        *,
        groups: list[PlannedGroup] | None = None,
        element_ids: list[str] | None = None,
    ) -> None:
        self.id = band_id
        self.label = label
        self.layer = layer
        self.groups = groups or []
        self.element_ids = element_ids or []


class ArchimateLayoutPlan:
    """The band/grouping structure the compiler decided, before any geometry."""

    __slots__ = ("bands",)

    def __init__(self, bands: list[PlannedBand]) -> None:
        self.bands = bands


def grid_size(count: int) -> tuple[float, float, int, int]:
    """``(width, height, cols, rows)`` of a ``count``-element grid's content box.

    ArchiMate's own constants rather than ``geo.grid_size``'s: elements are
    wide labelled boxes, not square icons with a label band underneath, so
    neither the pitch nor the height formula transfers.
    """
    if count <= 0:
        return 0.0, 0.0, 0, 0
    cols = min(geo.ARCHIMATE_MAX_COLS, count)
    rows = (count + cols - 1) // cols
    width = (cols - 1) * geo.ARCHIMATE_ELEMENT_PITCH_X + geo.ARCHIMATE_ELEMENT_W
    height = (rows - 1) * geo.ARCHIMATE_ELEMENT_PITCH_Y + geo.ARCHIMATE_ELEMENT_H
    return width, height, cols, rows


def container_size(count: int) -> tuple[float, float]:
    """Outer size a band or grouping needs to hold ``count`` elements."""
    content_w, content_h, _cols, _rows = grid_size(count)
    return content_w + geo.PAD * 2, geo.TITLE_PAD + content_h + geo.PAD


def _elk_group(group: PlannedGroup) -> dict:
    width, height = container_size(len(group.element_ids))
    return elk_graph.sized_container_node(group.id, width, height)


def _elk_band(band: PlannedBand) -> dict:
    if not band.groups:
        width, height = container_size(len(band.element_ids))
        return elk_graph.sized_container_node(band.id, width, height)

    # Groupings are peers on one rank (a "row"), wrapped into successive ranks
    # once there are more than fit comfortably side by side. Each wrapped row
    # is a band in the elk_layout_network sense, so consecutive rows get
    # transition edges; members within a row get none.
    per_row = geo.ARCHIMATE_GROUPS_PER_ROW
    rows = [
        band.groups[i:i + per_row]
        for i in range(0, len(band.groups), per_row)
    ]
    return {
        "id": band.id,
        "layoutOptions": {
            "elk.spacing.nodeNode": str(geo.ARCHIMATE_GROUP_GAP_X),
            "elk.layered.spacing.nodeNodeBetweenLayers": str(geo.ARCHIMATE_GROUP_GAP_X),
            "elk.padding": f"[top={geo.TITLE_PAD},left={geo.PAD},bottom={geo.PAD},right={geo.PAD}]",
            **elk_graph.MODEL_ORDER_OPTIONS,
        },
        "children": [_elk_group(g) for g in band.groups],
        "edges": elk_graph.band_transition_edges([[g.id for g in row] for row in rows]),
    }


def _build_graph(plan: ArchimateLayoutPlan) -> dict:
    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "DOWN",
            "elk.hierarchyHandling": "INCLUDE_CHILDREN",
            "elk.layered.spacing.nodeNodeBetweenLayers": str(geo.ARCHIMATE_BAND_GAP_Y),
            "elk.padding": f"[top={geo.CLOUD_ORIGIN_Y},left={geo.CLOUD_ORIGIN_X},bottom=40,right=40]",
            **elk_graph.MODEL_ORDER_OPTIONS,
        },
        "children": [_elk_band(b) for b in plan.bands],
        # One band per rank, top to bottom, in the order the compiler decided
        # (business over application over technology for the layered
        # viewpoint; a single band for the flat ones, where this is empty).
        "edges": elk_graph.band_transition_edges([[b.id] for b in plan.bands]),
    }


def layout_archimate_plan(plan: ArchimateLayoutPlan) -> dict[str, Geo]:
    """Parent-relative geometry for every band and grouping in ``plan``.

    Element geometry is not included — the caller grids elements into each
    container once that container's final size is known (see module docstring).
    """
    if not plan.bands:
        return {}
    result = run_elk(_build_graph(plan))
    children = elk_graph.elk_children_by_id(result)

    geos: dict[str, Geo] = {}
    for band in plan.bands:
        node = children.get(band.id)
        if node is None:
            raise ElkLayoutError(f"ELK result missing band {band.id!r}")
        geos[band.id] = Geo(x=node["x"], y=node["y"], w=node["width"], h=node["height"])
        band_children = elk_graph.elk_children_by_id(node)
        for group in band.groups:
            child = band_children.get(group.id)
            if child is None:
                raise ElkLayoutError(f"ELK result missing grouping {group.id!r}")
            geos[group.id] = Geo(x=child["x"], y=child["y"], w=child["width"], h=child["height"])
    return geos
