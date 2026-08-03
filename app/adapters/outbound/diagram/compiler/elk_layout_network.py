"""ELK-driven container/box geometry for the Network compiler.

One ELK graph mirroring the IR's real hierarchy (Cloud -> Region -> VPC ->
Zone -> Subnet -> leaf, plus the edge layer / connectivity hubs / gateway at
their documented levels), laid out in a single ``run_elk`` call — replacing
``network.py``'s inline cursor arithmetic (``_zone_grid``/``_emit_vpc``/etc.).

The whole graph uses ONE direction (`DOWN`) throughout — nesting a
`RIGHT`-direction wrapper inside a `DOWN` graph does not work under
`elk.hierarchyHandling: INCLUDE_CHILDREN` (confirmed against ELK's own
issue tracker: kieler/elkjs#26 — a documented limitation, one hierarchical
layout run has exactly one direction, not a misconfiguration on this side).
So instead of direction changes, every level uses one of two patterns:

- **A band** (stacks vertically: a VPC's gateway/zones/groups; Cloud's
  edge-layer/regions/hub-shelf; a zone's subnet tiers): a synthetic
  "comes before" edge from every node in band *i* to every node in band
  *i+1* (``elk_graph.band_transition_edges``) forces them into successive
  ranks — the same longest-path layering ELK already does for real
  dependencies, just declared rather than inferred.
- **A row** (same rank, side by side, in a fixed left-to-right order: peer
  VPCs in a Region, peer zones in a VPC, peer groups in a VPC): no edges at
  all among the row's own members — confirmed empirically that with
  ``elk.separateConnectedComponents: false`` plus
  ``elk.layered.considerModelOrder.strategy: NODES_AND_EDGES`` and
  ``elk.layered.crossingMinimization.forceNodeModelOrder: true``
  (``elk_graph.MODEL_ORDER_OPTIONS``, set on every level's own
  ``layoutOptions`` — confirmed necessary at each level individually, not
  just once at the graph root), edge-free same-rank siblings land side by
  side in declaration order. Without these options they scatter in an
  arbitrary order instead (ELK's default component-ordering behavior, not
  model order).

No row/band ever needs a synthetic wrapper node: a row's members are direct
children of their real parent (same rank, no edges among them), and a
band's members are also direct children of their real parent (a rank
apart, via the transition edges to the next band) — so the ELK result maps
straight onto the IR's own parent/child structure with no unwrapping step.

Leaf-grid containers (subnet/group/edge-layer/hub-shelf) are a single sized
ELK node, not a compound node with each leaf as a child — see
``elk_graph.py``'s module docstring for why (leaves with no edges between
them don't reliably group into rows under ``layered`` either, for the same
underlying reason rows needed the fix above). Leaf positions *within* such a
container come from ``emit_leaf_grid`` once the container's own final
size/position is known.

This graph carries no real flow edges. Edge routing stays entirely with the
existing local router (``edge_routing.py``'s ``enable_obstacle_routing``)
— it already reaches zero residual diagnostics on this compiler's geometry,
unlike Corridor's, it was never the evidenced pain, and giving ELK real
edges here would fight the band/row synthetic edges above (confirmed
empirically: one real cross-hierarchy edge overrode a row's own ordering).
"""

from __future__ import annotations

from app.adapters.outbound.diagram import elk_graph
from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError, run_elk
from app.adapters.outbound.diagram.logical_layout import Geo
from app.domain.ir.base import Leaf
from app.domain.ir.network import (
    TIER_ORDER,
    AvailabilityZone,
    CloudBoundary,
    NetworkIR,
    Region,
    Vpc,
)


def connectivity_shelf_id(ir: NetworkIR) -> str:
    """Id for the connectivity-hubs shelf — a real container the compiler
    invents (the IR doesn't name it), so it's the only id that could collide
    with a model-chosen one."""
    taken = ir.all_ids()
    preferred = "connectivity_layer"
    if preferred not in taken:
        return preferred
    suffix = 2
    while f"{preferred}_{suffix}" in taken:
        suffix += 1
    return f"{preferred}_{suffix}"


def _leaf_grid(leaves: list[Leaf], node_id: str, *, is_group: bool = False) -> dict:
    w, h = elk_graph.leaf_grid_size(leaves, is_group=is_group)
    return elk_graph.sized_container_node(node_id, w, h)


def _elk_zone(zone: AvailabilityZone) -> dict:
    """Returns the zone's ELK node — subnet tiers are bands (public -> app
    -> data), so each consecutive pair gets a transition edge."""
    ordered = sorted(zone.subnets, key=lambda s: TIER_ORDER.index(s.tier))
    subnet_nodes = [_leaf_grid(s.leaves, s.id) for s in ordered]
    return {
        "id": zone.id,
        "layoutOptions": {
            "elk.layered.spacing.nodeNodeBetweenLayers": str(geo.SUBNET_TIER_GAP),
            **elk_graph.MODEL_ORDER_OPTIONS,
        },
        "children": subnet_nodes,
        "edges": elk_graph.band_transition_edges([[s.id] for s in ordered]),
    }


def _elk_vpc(vpc: Vpc) -> dict:
    zone_nodes = [_elk_zone(zone) for zone in vpc.zones]
    zone_ids = [zone.id for zone in vpc.zones]

    bands: list[list[str]] = []
    children: list[dict] = []
    if vpc.gateway:
        children.append({"id": vpc.gateway.id, "width": geo.LEAF_W, "height": geo.LEAF_H})
        bands.append([vpc.gateway.id])
    children.extend(zone_nodes)
    bands.append(zone_ids)
    if vpc.groups:
        group_nodes = [_leaf_grid(g.leaves, g.id, is_group=True) for g in vpc.groups]
        children.extend(group_nodes)
        bands.append([g.id for g in vpc.groups])

    # Gaps differ per band pair (gateway-to-zones vs zones-to-groups); ELK's
    # between-layer spacing is one uniform value — use the larger of the two
    # documented minimums so neither pair ever crowds below its own rule.
    band_gap = max(geo.GATEWAY_GAP_BELOW, geo.GROUP_GAP_BELOW_ZONES)
    return {
        "id": vpc.id,
        "layoutOptions": {
            "elk.layered.spacing.nodeNodeBetweenLayers": str(band_gap),
            "elk.spacing.nodeNode": str(geo.ZONE_GAP_X),
            **elk_graph.MODEL_ORDER_OPTIONS,
        },
        "children": children,
        "edges": elk_graph.band_transition_edges(bands),
    }


def _elk_region(region: Region) -> dict:
    return {
        "id": region.id,
        "layoutOptions": {"elk.spacing.nodeNode": str(geo.VPC_GAP_X), **elk_graph.MODEL_ORDER_OPTIONS},
        "children": [_elk_vpc(v) for v in region.vpcs],
    }


def _elk_cloud(cloud: CloudBoundary, hub_shelf_id: str) -> dict:
    children: list[dict] = []
    bands: list[list[str]] = []

    if cloud.edge_layer:
        children.append(_leaf_grid(cloud.edge_layer.leaves, cloud.edge_layer.id))
        bands.append([cloud.edge_layer.id])

    # Peer regions are a row (same rank, side by side, in declaration order)
    # — same pattern as a VPC's peer zones, not a sequence of separate bands:
    # one entry in `bands` covering every region, not one entry per region.
    region_nodes = [_elk_region(region) for region in cloud.regions]
    children.extend(region_nodes)
    bands.append([region.id for region in cloud.regions])

    if cloud.connectivity_hubs:
        children.append(_leaf_grid(cloud.connectivity_hubs, hub_shelf_id))
        bands.append([hub_shelf_id])

    return {
        "id": cloud.id,
        "layoutOptions": {
            "elk.layered.spacing.nodeNodeBetweenLayers": str(geo.EDGE_LAYER_GAP_Y),
            "elk.spacing.nodeNode": str(geo.REGION_GAP_X),
            **elk_graph.MODEL_ORDER_OPTIONS,
        },
        "children": children,
        "edges": elk_graph.band_transition_edges(bands),
    }


def _build_elk_network_graph(cloud_node: dict) -> dict:
    return {
        "id": "root",
        "layoutOptions": {
            "elk.algorithm": "layered",
            "elk.direction": "DOWN",
            "elk.hierarchyHandling": "INCLUDE_CHILDREN",
            "elk.padding": f"[top={geo.CLOUD_ORIGIN_Y},left={geo.CLOUD_ORIGIN_X},bottom=40,right=40]",
            **elk_graph.MODEL_ORDER_OPTIONS,
        },
        "children": [cloud_node],
    }


def _record_subtree(node: dict, geos: dict[str, Geo]) -> None:
    """Every node maps directly onto the IR's own parent/child structure —
    no wrapper nodes to unwrap (see module docstring) — so this just reads
    each node's geometry, already parent-relative per mxCell's convention."""
    geos[node["id"]] = Geo(x=node["x"], y=node["y"], w=node["width"], h=node["height"])
    for child in node.get("children", []):
        _record_subtree(child, geos)


def layout_network_ir(ir: NetworkIR) -> dict[str, Geo]:
    """Compute geometry (parent-relative, mxCell's own convention — root-
    level cells like Cloud come out page-absolute for free, since the ELK
    root's padding equals ``CLOUD_ORIGIN_X/Y``) for every container and the
    gateway, via one ELK call. Individual leaf positions inside a leaf-grid
    container are NOT part of this return value — the caller
    (``compiler/network.py``) places them with ``emit_leaf_grid`` once it
    has the container's final size (see the module docstring for why).
    """
    hub_shelf_id = connectivity_shelf_id(ir) if ir.cloud.connectivity_hubs else ""
    cloud_node = _elk_cloud(ir.cloud, hub_shelf_id)
    graph = _build_elk_network_graph(cloud_node)
    result = run_elk(graph)

    result_cloud = elk_graph.require_child(result, ir.cloud.id)
    geos: dict[str, Geo] = {}
    _record_subtree(result_cloud, geos)

    gateway_ids = {
        vpc.gateway.id for region in ir.cloud.regions for vpc in region.vpcs if vpc.gateway
    }
    expected = ir.structural_ids() | gateway_ids
    if hub_shelf_id:
        expected = expected | {hub_shelf_id}
    missing = expected - set(geos.keys())
    if missing:
        raise ElkLayoutError(f"ELK result missing node(s): {sorted(missing)[:5]}")
    return geos
