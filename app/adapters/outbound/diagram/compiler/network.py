"""Compile a NetworkIR into draw.io XML.

Container/box geometry comes from ELK (``elk_layout_network.layout_network_ir``
— see that module's docstring for the graph shape and why it carries no flow
edges). This module just walks the IR hierarchy — deciding which parent a
cell nests under is a semantic/structural choice this compiler owns — and
emits each cell at the position ELK already computed. Edge routing stays
entirely with the existing local router
(``placement_fixers.fix_edge_anchors_from_geometry``'s
``enable_obstacle_routing``), which already reaches zero residual
diagnostics on this compiler's geometry.

Layout order mirrors the canonical Network IR hierarchy
(``config/xml_gen/ir/network.txt``):

    Cloud
      Region
        VPC
          gateway (own row, above zones)
          Zone x N
            Subnet (public -> app -> data, top to bottom)
          cross-cutting shelves (below all zones)
      Edge/Global layer (below region, still inside cloud)
    external actors (root layer, ingress left / egress right)
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import theme
from app.adapters.outbound.diagram.compiler.elk_layout_network import (
    connectivity_shelf_id,
    layout_network_ir,
)
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import (
    CompilerError,
    emit_external_actors,
    emit_flows,
    emit_leaf_grid,
    leaf_label,
)
from app.adapters.outbound.diagram.logical_layout import Geo
from app.domain.ir.network import AvailabilityZone, CrossCuttingGroup, NetworkIR, Subnet, Vpc


def compile_network_ir(ir: NetworkIR, *, page_name: str = "Network") -> str:
    """Render ``ir`` to a complete draw.io document.

    The IR is expected to have passed ``semantic_errors()`` already; this raises
    rather than silently producing a broken diagram if it has not.
    """
    errors = ir.semantic_errors()
    if errors:
        raise CompilerError(f"cannot compile an invalid IR: {'; '.join(errors[:3])}")
    return _NetworkCompiler(ir, page_name).run()


class _NetworkCompiler:
    def __init__(self, ir: NetworkIR, page_name: str) -> None:
        self.ir = ir
        self.platform = ir.platform
        self.w = CellWriter(page_name)

    def run(self) -> str:
        geos = layout_network_ir(self.ir)
        self._emit_cloud(geos)
        g = geos[self.ir.cloud.id]
        emit_external_actors(
            self.w, self.ir.external_actors,
            left_x=g.x - geo.ACTOR_GAP_X - geo.LEAF_W,
            right_x=g.x + g.w + geo.ACTOR_GAP_X,
            top_y=g.y + geo.TITLE_PAD,
        )
        emit_flows(self.w, self.ir.flows)
        # This page's own container geometry is authoritative — mark it so
        # repair_xml skips re-deriving/overwriting it, the same contract
        # compiler/corridor.py's run_layered() gives Logical/Security/
        # Data-flow (see xml_diagnostics.py's layout="ir" skip).
        self.w.mark_layout_authoritative()
        page_w = g.x + g.w + 400
        page_h = g.y + g.h + 120
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    # ── containers, bottom-up — geometry from `geos`, structure from the IR ──

    def _emit_subnet(self, subnet: Subnet, parent_id: str, geos: dict[str, Geo]) -> None:
        g = geos[subnet.id]
        emit_leaf_grid(self.w, subnet.leaves, subnet.id, is_group=False)
        self.w.vertex(
            id=subnet.id, label=f"{subnet.tier.title()} Subnet", style=theme.subnet_style(subnet.tier),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_zone(self, zone: AvailabilityZone, parent_id: str, geos: dict[str, Geo]) -> None:
        for subnet in zone.subnets:
            self._emit_subnet(subnet, zone.id, geos)
        g = geos[zone.id]
        self.w.vertex(
            id=zone.id, label=zone.label, style=theme.zone_style(),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_group(self, group: CrossCuttingGroup, parent_id: str, geos: dict[str, Geo]) -> None:
        g = geos[group.id]
        emit_leaf_grid(self.w, group.leaves, group.id, is_group=True)
        self.w.vertex(
            id=group.id, label=f"{group.kind.title()} Group", style=theme.group_style(group.kind),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_vpc(self, vpc: Vpc, parent_id: str, geos: dict[str, Geo]) -> None:
        if vpc.gateway:
            g = geos[vpc.gateway.id]
            self.w.vertex(
                id=vpc.gateway.id, label=leaf_label(vpc.gateway), style=theme.leaf_style(),
                x=g.x, y=g.y, w=g.w, h=g.h, parent=vpc.id, shape_type=vpc.gateway.shape_type,
            )
        for zone in vpc.zones:
            self._emit_zone(zone, vpc.id, geos)
        for group in vpc.groups:
            self._emit_group(group, vpc.id, geos)
        g = geos[vpc.id]
        self.w.vertex(
            id=vpc.id, label=vpc.label, style=theme.vpc_style(self.platform),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_cloud(self, geos: dict[str, Geo]) -> None:
        cloud = self.ir.cloud
        if cloud.edge_layer:
            g = geos[cloud.edge_layer.id]
            emit_leaf_grid(self.w, cloud.edge_layer.leaves, cloud.edge_layer.id, is_group=False)
            self.w.vertex(
                id=cloud.edge_layer.id, label=cloud.edge_layer.label, style=theme.edge_layer_style(),
                x=g.x, y=g.y, w=g.w, h=g.h, parent=cloud.id,
            )
        for region in cloud.regions:
            for vpc in region.vpcs:
                self._emit_vpc(vpc, region.id, geos)
            g = geos[region.id]
            self.w.vertex(
                id=region.id, label=region.label, style=theme.region_style(self.platform),
                x=g.x, y=g.y, w=g.w, h=g.h, parent=cloud.id,
            )
        if cloud.connectivity_hubs:
            hub_shelf_id = connectivity_shelf_id(self.ir)
            g = geos[hub_shelf_id]
            emit_leaf_grid(self.w, cloud.connectivity_hubs, hub_shelf_id, is_group=False)
            self.w.vertex(
                id=hub_shelf_id, label="Connectivity / Hybrid", style=theme.edge_layer_style(),
                x=g.x, y=g.y, w=g.w, h=g.h, parent=cloud.id,
            )
        g = geos[cloud.id]
        self.w.vertex(
            id=cloud.id, label=cloud.label, style=theme.cloud_style(self.platform),
            x=g.x, y=g.y, w=g.w, h=g.h, parent="1",
        )
