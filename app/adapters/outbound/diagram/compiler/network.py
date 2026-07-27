"""Compile a NetworkIR into draw.io XML.

Bottom-up layout: leaves are gridded first, then each container is sized to
exactly wrap its children plus canonical padding. Nothing is ever placed at a
guessed coordinate and corrected later, which is why the output needs no
fit-to-children or overlap-repair pass.

Layout order mirrors the canonical hierarchy in
``config/xml_gen/views/network.txt``:

    Cloud
      Region
        VPC
          gateway (beside zones)
          Zone x N
            Subnet (public -> app -> data, top to bottom)
          cross-cutting shelves (below all zones)
      Edge/Global layer (below region, still inside cloud)
    external actors (root layer, ingress left / egress right)
"""

from __future__ import annotations

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import theme
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import (
    Box as _Box,
    CompilerError,
    emit_external_actors,
    emit_flows,
    emit_leaf_grid,
)
from app.domain.ir.network import (
    AvailabilityZone,
    CrossCuttingGroup,
    NetworkIR,
    Subnet,
    Vpc,
)


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

    # ── entry point ────────────────────────────────────────────────────

    def run(self) -> str:
        cloud = self.ir.cloud
        cloud_box = self._emit_cloud(cloud_x=geo.CLOUD_ORIGIN_X, cloud_y=geo.CLOUD_ORIGIN_Y)
        emit_external_actors(
            self.w, self.ir.external_actors,
            left_x=geo.CLOUD_ORIGIN_X - geo.ACTOR_GAP_X - geo.LEAF_W,
            right_x=geo.CLOUD_ORIGIN_X + cloud_box.width + geo.ACTOR_GAP_X,
            top_y=geo.CLOUD_ORIGIN_Y + geo.TITLE_PAD,
        )
        emit_flows(self.w, self.ir.flows)

        page_w = geo.CLOUD_ORIGIN_X + cloud_box.width + 400
        page_h = geo.CLOUD_ORIGIN_Y + cloud_box.height + 120
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    # ── containers, bottom-up ──────────────────────────────────────────

    def _emit_subnet(self, subnet: Subnet, parent_id: str, x: float, y: float) -> _Box:
        box = emit_leaf_grid(self.w, subnet.leaves, subnet.id, is_group=False)
        self.w.vertex(
            id=subnet.id,
            label=f"{subnet.tier.title()} Subnet",
            style=theme.subnet_style(subnet.tier),
            x=x, y=y, w=box.width, h=box.height,
            parent=parent_id,
        )
        return box

    def _emit_zone(self, zone: AvailabilityZone, parent_id: str, x: float, y: float) -> _Box:
        cursor_y = geo.TITLE_PAD
        widest = 0.0
        for subnet in zone.subnets:
            box = self._emit_subnet(subnet, zone.id, geo.PAD, cursor_y)
            widest = max(widest, box.width)
            cursor_y += box.height + geo.SUBNET_TIER_GAP

        # Trailing tier gap is not part of the zone — remove it, add bottom pad.
        inner_h = cursor_y - geo.SUBNET_TIER_GAP + geo.PAD
        zone_box = _Box(width=widest + geo.PAD * 2, height=inner_h)
        self.w.vertex(
            id=zone.id,
            label=zone.label,
            style=theme.zone_style(),
            x=x, y=y, w=zone_box.width, h=zone_box.height,
            parent=parent_id,
        )
        return zone_box

    def _emit_group(self, group: CrossCuttingGroup, parent_id: str, x: float, y: float) -> _Box:
        box = emit_leaf_grid(self.w, group.leaves, group.id, is_group=True)
        self.w.vertex(
            id=group.id,
            label=f"{group.kind.title()} Group",
            style=theme.group_style(group.kind),
            x=x, y=y, w=box.width, h=box.height,
            parent=parent_id,
        )
        return box

    def _emit_vpc(self, vpc: Vpc, parent_id: str, x: float, y: float) -> _Box:
        # Zones sit in a row across the top of the VPC.
        zone_x = geo.PAD
        zones_bottom = geo.TITLE_PAD
        for zone in vpc.zones:
            zone_box = self._emit_zone(zone, vpc.id, zone_x, geo.TITLE_PAD)
            zone_x += zone_box.width + geo.ZONE_GAP_X
            zones_bottom = max(zones_bottom, geo.TITLE_PAD + zone_box.height)
        zones_right = zone_x - geo.ZONE_GAP_X

        # Gateway is a sibling of the zones, to their right — never in a subnet.
        content_right = zones_right
        if vpc.gateway:
            gw_x = zones_right + geo.ZONE_GAP_X * 2
            self.w.vertex(
                id=vpc.gateway.id,
                label=vpc.gateway.label,
                style=theme.leaf_style(),
                x=gw_x, y=geo.TITLE_PAD,
                w=geo.LEAF_W, h=geo.LEAF_H,
                parent=vpc.id,
                shape_type=vpc.gateway.shape_type,
            )
            content_right = gw_x + geo.LEAF_W

        # Cross-cutting shelves go strictly below every zone.
        content_bottom = zones_bottom
        if vpc.groups:
            group_y = zones_bottom + geo.GROUP_GAP_BELOW_ZONES
            group_x = geo.PAD
            for group in vpc.groups:
                group_box = self._emit_group(group, vpc.id, group_x, group_y)
                group_x += group_box.width + geo.GROUP_GAP_X
                content_bottom = max(content_bottom, group_y + group_box.height)
            content_right = max(content_right, group_x - geo.GROUP_GAP_X)

        vpc_box = _Box(width=content_right + geo.PAD, height=content_bottom + geo.PAD)
        self.w.vertex(
            id=vpc.id,
            label=vpc.label,
            style=theme.vpc_style(self.platform),
            x=x, y=y, w=vpc_box.width, h=vpc_box.height,
            parent=parent_id,
        )
        return vpc_box

    def _emit_cloud(self, *, cloud_x: float, cloud_y: float) -> _Box:
        cloud = self.ir.cloud
        content_right = 0.0
        cursor_y = geo.TITLE_PAD

        for region in cloud.regions:
            vpc_x = geo.PAD
            region_bottom = geo.TITLE_PAD
            for vpc in region.vpcs:
                vpc_box = self._emit_vpc(vpc, region.id, vpc_x, geo.TITLE_PAD)
                vpc_x += vpc_box.width + geo.VPC_GAP_X
                region_bottom = max(region_bottom, geo.TITLE_PAD + vpc_box.height)
            region_box = _Box(
                width=(vpc_x - geo.VPC_GAP_X) + geo.PAD,
                height=region_bottom + geo.PAD,
            )
            self.w.vertex(
                id=region.id,
                label=region.label,
                style=theme.region_style(self.platform),
                x=geo.PAD, y=cursor_y,
                w=region_box.width, h=region_box.height,
                parent=cloud.id,
            )
            content_right = max(content_right, geo.PAD + region_box.width)
            cursor_y += region_box.height + geo.EDGE_LAYER_GAP_Y

        # Edge/global layer: inside Cloud, below the regions.
        if cloud.edge_layer:
            box = emit_leaf_grid(self.w, cloud.edge_layer.leaves, cloud.edge_layer.id, is_group=False)
            self.w.vertex(
                id=cloud.edge_layer.id,
                label=cloud.edge_layer.label,
                style=theme.edge_layer_style(),
                x=geo.PAD, y=cursor_y, w=box.width, h=box.height,
                parent=cloud.id,
            )
            content_right = max(content_right, geo.PAD + box.width)
            cursor_y += box.height + geo.EDGE_LAYER_GAP_Y

        # Connectivity hubs (Transit Gateway etc.) sit at cloud level, in a row.
        if cloud.connectivity_hubs:
            pitch = geo.pitch_for(False)
            for index, hub in enumerate(cloud.connectivity_hubs):
                self.w.vertex(
                    id=hub.id,
                    label=hub.label,
                    style=theme.leaf_style(),
                    x=geo.PAD + index * pitch, y=cursor_y,
                    w=geo.LEAF_W, h=geo.LEAF_H,
                    parent=cloud.id,
                    shape_type=hub.shape_type,
                )
            hubs_w = (len(cloud.connectivity_hubs) - 1) * pitch + geo.LEAF_W
            content_right = max(content_right, geo.PAD + hubs_w)
            cursor_y += geo.LEAF_H + geo.LABEL_BAND_H + geo.EDGE_LAYER_GAP_Y

        cloud_box = _Box(
            width=content_right + geo.PAD,
            height=cursor_y - geo.EDGE_LAYER_GAP_Y + geo.PAD,
        )
        self.w.vertex(
            id=cloud.id,
            label=cloud.label,
            style=theme.cloud_style(self.platform),
            x=cloud_x, y=cloud_y,
            w=cloud_box.width, h=cloud_box.height,
            parent="1",
        )
        return cloud_box
