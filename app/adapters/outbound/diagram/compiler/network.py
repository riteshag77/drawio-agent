"""Compile a NetworkIR into draw.io XML.

Container/box geometry comes from ELK (``elk_layout_network.layout_network_ir``
— see that module's docstring for the graph shape and why it carries no flow
edges). This module just walks the IR hierarchy — deciding which parent a
cell nests under is a semantic/structural choice this compiler owns — and
emits each cell at the position ELK already computed. Edge routing stays
entirely with the existing local router
(``edge_routing.fix_edge_anchors_from_geometry``'s
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
from app.adapters.outbound.diagram.logical_layout import (
    Geo,
    align_leaves_across_containers,
    order_leaves_by_flow,
)
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
        self._leaf_abs_cy: dict[str, float] = {}

    def _ordered(self, leaves: list) -> list:
        """Flow-aware order so tier-to-tier arrows stay column-aligned."""
        return order_leaves_by_flow(list(leaves), self.ir.flows)

    def _record_leaf_centers(
        self, leaves: list, parent_abs_y: float, *, is_group: bool,
    ) -> list:
        """Emit-order leaves and record predicted absolute centers for actor Y."""
        ordered = self._ordered(leaves)
        _cw, _ch, cols, _rows = geo.grid_size(len(ordered), is_group=is_group)
        for index, leaf in enumerate(ordered):
            row = index // cols if cols else 0
            cy = parent_abs_y + geo.TITLE_PAD + row * geo.ROW_PITCH_Y + geo.LEAF_H / 2.0
            self._leaf_abs_cy[leaf.id] = cy
        return ordered

    def run(self) -> str:
        geos = layout_network_ir(self.ir)
        self._geos = geos
        self._leaf_abs_cy = {}
        self._emit_cloud(geos)
        g = geos[self.ir.cloud.id]
        emit_external_actors(
            self.w, self.ir.external_actors,
            left_x=g.x - geo.ACTOR_GAP_X - geo.LEAF_W,
            right_x=g.x + g.w + geo.ACTOR_GAP_X,
            top_y=g.y + geo.TITLE_PAD,
            flows=self.ir.flows,
            endpoint_cy=self._leaf_abs_cy,
            band_hi=g.y + g.h,
        )
        emit_flows(self.w, self.ir.flows)
        self.w.set_business_notes(self.ir.business_notes)
        # This page's container geometry is compiler-final — mark layout="ir"
        # so fit_containers_to_children does not reflow it (same as corridor).
        self.w.mark_layout_authoritative()
        page_w = g.x + g.w + 400
        page_h = g.y + g.h + 120
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    # ── containers, bottom-up — geometry from `geos`, structure from the IR ──

    def _emit_subnet(
        self, subnet: Subnet, parent_id: str, geos: dict[str, Geo],
        *, parent_abs_y: float, leaves: list | None = None,
    ) -> None:
        g = geos[subnet.id]
        abs_y = parent_abs_y + g.y
        ordered = self._record_leaf_centers(
            leaves if leaves is not None else subnet.leaves, abs_y, is_group=False,
        )
        emit_leaf_grid(self.w, ordered, subnet.id, is_group=False)
        self.w.vertex(
            id=subnet.id, label=f"{subnet.tier.title()} Subnet", style=theme.subnet_style(subnet.tier),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_zone(
        self, zone: AvailabilityZone, parent_id: str, geos: dict[str, Geo], *, parent_abs_y: float,
    ) -> None:
        g = geos[zone.id]
        abs_y = parent_abs_y + g.y
        # Cross-tier column alignment: public → app → data share primary-path rows.
        tier_lists = [self._ordered(s.leaves) for s in zone.subnets]
        aligned = align_leaves_across_containers(tier_lists, self.ir.flows)
        for subnet, leaves in zip(zone.subnets, aligned):
            self._emit_subnet(subnet, zone.id, geos, parent_abs_y=abs_y, leaves=leaves)
        self.w.vertex(
            id=zone.id, label=zone.label, style=theme.zone_style(),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_group(
        self, group: CrossCuttingGroup, parent_id: str, geos: dict[str, Geo], *, parent_abs_y: float,
    ) -> None:
        g = geos[group.id]
        abs_y = parent_abs_y + g.y
        ordered = self._record_leaf_centers(group.leaves, abs_y, is_group=True)
        emit_leaf_grid(self.w, ordered, group.id, is_group=True)
        self.w.vertex(
            id=group.id, label=f"{group.kind.title()} Group", style=theme.group_style(group.kind),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_vpc(self, vpc: Vpc, parent_id: str, geos: dict[str, Geo], *, parent_abs_y: float) -> None:
        g = geos[vpc.id]
        abs_y = parent_abs_y + g.y
        if vpc.gateway:
            gg = geos[vpc.gateway.id]
            self._leaf_abs_cy[vpc.gateway.id] = abs_y + gg.y + gg.h / 2.0
            self.w.vertex(
                id=vpc.gateway.id, label=leaf_label(vpc.gateway), style=theme.leaf_style(),
                x=gg.x, y=gg.y, w=gg.w, h=gg.h, parent=vpc.id, shape_type=vpc.gateway.shape_type,
            )
        for zone in vpc.zones:
            self._emit_zone(zone, vpc.id, geos, parent_abs_y=abs_y)
        for group in vpc.groups:
            self._emit_group(group, vpc.id, geos, parent_abs_y=abs_y)
        self.w.vertex(
            id=vpc.id, label=vpc.label, style=theme.vpc_style(self.platform),
            x=g.x, y=g.y, w=g.w, h=g.h, parent=parent_id,
        )

    def _emit_cloud(self, geos: dict[str, Geo]) -> None:
        cloud = self.ir.cloud
        g = geos[cloud.id]
        cloud_abs_y = g.y  # root-level → page-absolute
        if cloud.edge_layer:
            eg = geos[cloud.edge_layer.id]
            ordered = self._record_leaf_centers(
                cloud.edge_layer.leaves, cloud_abs_y + eg.y, is_group=False,
            )
            emit_leaf_grid(self.w, ordered, cloud.edge_layer.id, is_group=False)
            self.w.vertex(
                id=cloud.edge_layer.id, label=cloud.edge_layer.label, style=theme.edge_layer_style(),
                x=eg.x, y=eg.y, w=eg.w, h=eg.h, parent=cloud.id,
            )
        for region in cloud.regions:
            rg = geos[region.id]
            for vpc in region.vpcs:
                self._emit_vpc(vpc, region.id, geos, parent_abs_y=cloud_abs_y + rg.y)
            self.w.vertex(
                id=region.id, label=region.label, style=theme.region_style(self.platform),
                x=rg.x, y=rg.y, w=rg.w, h=rg.h, parent=cloud.id,
            )
        if cloud.connectivity_hubs:
            hub_shelf_id = connectivity_shelf_id(self.ir)
            hg = geos[hub_shelf_id]
            ordered = self._record_leaf_centers(
                cloud.connectivity_hubs, cloud_abs_y + hg.y, is_group=False,
            )
            emit_leaf_grid(self.w, ordered, hub_shelf_id, is_group=False)
            self.w.vertex(
                id=hub_shelf_id, label="Connectivity / Hybrid", style=theme.edge_layer_style(),
                x=hg.x, y=hg.y, w=hg.w, h=hg.h, parent=cloud.id,
            )
        self.w.vertex(
            id=cloud.id, label=cloud.label, style=theme.cloud_style(self.platform),
            x=g.x, y=g.y, w=g.w, h=g.h, parent="1",
        )
