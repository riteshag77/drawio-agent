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
from app.adapters.outbound.diagram.compiler.emit import CellWriter
from app.adapters.outbound.diagram.compiler.shared import (
    Box as _Box,
    CompilerError,
    emit_external_actors,
    emit_flows,
    emit_leaf_grid,
    grid_box,
    leaf_label,
)
from app.domain.ir.network import (
    TIER_ORDER,
    AvailabilityZone,
    CrossCuttingGroup,
    NetworkIR,
    Subnet,
    SubnetTier,
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
        # This page's own layout is authoritative — mark it so repair_xml
        # skips re-deriving/overwriting it, the same contract
        # compiler/corridor.py's run_layered() already gives Logical/
        # Security/Data-flow (see xml_diagnostics.py's layout="ir" skip).
        self.w.mark_layout_authoritative()

        page_w = geo.CLOUD_ORIGIN_X + cloud_box.width + 400
        page_h = geo.CLOUD_ORIGIN_Y + cloud_box.height + 120
        return self.w.to_xml(page_width=page_w, page_height=page_h)

    def _unique_id(self, preferred: str) -> str:
        """An id for a compiler-invented container that no IR cell already uses.

        The connectivity shelf is the only container the compiler adds that the
        IR does not name, so it is the only id that could collide with a model-
        chosen one — and a duplicate id silently drops cells in draw.io.
        """
        taken = self.ir.all_ids()
        if preferred not in taken:
            return preferred
        suffix = 2
        while f"{preferred}_{suffix}" in taken:
            suffix += 1
        return f"{preferred}_{suffix}"

    # ── containers, bottom-up ──────────────────────────────────────────

    def _emit_subnet(
        self, subnet: Subnet, parent_id: str, x: float, y: float, w: float, h: float
    ) -> None:
        """Emit one subnet at a size its *tier* dictates, not its own content.

        ``w``/``h`` come from the VPC-wide tier grid (see ``_zone_grid``) so the
        same tier is the same size and at the same y in every peer zone. Leaf
        positions inside are grid-relative and unaffected by the stretch.
        """
        emit_leaf_grid(self.w, subnet.leaves, subnet.id, is_group=False)
        self.w.vertex(
            id=subnet.id,
            label=f"{subnet.tier.title()} Subnet",
            style=theme.subnet_style(subnet.tier),
            x=x, y=y, w=w, h=h,
            parent=parent_id,
        )

    def _zone_grid(self, zones: list[AvailabilityZone]) -> tuple[
        dict[SubnetTier, float], dict[SubnetTier, float], _Box
    ]:
        """Measure one shared row/column grid for every peer zone in a VPC.

        Returns ``(tier_y, tier_h, zone_box)``: the y offset and height each
        subnet tier gets *in every zone*, plus the one size every zone box
        takes. This is what makes network.txt's "peer AZs share top/height;
        subnet rows align across AZs" true by construction. Sizing each zone
        from its own contents instead (the previous behaviour) let one zone with
        an extra service push its data tier a full row below its peer's, so
        mirrored zones read as unrelated stacks and every app->data edge in the
        diagram had a different length.
        """
        # Widest subnet anywhere sets the width for all of them, so icon columns
        # line up across zones as well as down them. This is a subnet *outer*
        # width (grid_box already adds the subnet's own side padding); the zone
        # adds another PAD on each side around it.
        subnet_w = max(
            grid_box(len(subnet.leaves), is_group=False).width
            for zone in zones
            for subnet in zone.subnets
        )
        tier_h: dict[SubnetTier, float] = {}
        for zone in zones:
            for subnet in zone.subnets:
                box = grid_box(len(subnet.leaves), is_group=False)
                tier_h[subnet.tier] = max(tier_h.get(subnet.tier, 0.0), box.height)

        tier_y: dict[SubnetTier, float] = {}
        cursor_y = geo.TITLE_PAD
        for tier in TIER_ORDER:
            if tier not in tier_h:
                continue
            tier_y[tier] = cursor_y
            cursor_y += tier_h[tier] + geo.SUBNET_TIER_GAP

        # Trailing tier gap is not part of the zone — remove it, add bottom pad.
        zone_box = _Box(
            width=subnet_w + geo.PAD * 2,
            height=cursor_y - geo.SUBNET_TIER_GAP + geo.PAD,
        )
        return tier_y, tier_h, zone_box

    def _emit_zone(
        self,
        zone: AvailabilityZone,
        parent_id: str,
        x: float,
        y: float,
        tier_y: dict[SubnetTier, float],
        tier_h: dict[SubnetTier, float],
        zone_box: _Box,
    ) -> None:
        subnet_w = zone_box.width - geo.PAD * 2
        for subnet in zone.subnets:
            self._emit_subnet(
                subnet, zone.id, geo.PAD, tier_y[subnet.tier], subnet_w, tier_h[subnet.tier]
            )
        self.w.vertex(
            id=zone.id,
            label=zone.label,
            style=theme.zone_style(),
            x=x, y=y, w=zone_box.width, h=zone_box.height,
            parent=parent_id,
        )

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
        # Measure the shared zone grid before placing anything: the zones' total
        # span is what the gateway centres on, and every zone is the same size.
        tier_y, tier_h, zone_box = self._zone_grid(vpc.zones)
        zones_span = len(vpc.zones) * zone_box.width + (len(vpc.zones) - 1) * geo.ZONE_GAP_X

        # Gateway is the single entry point that fronts every zone below it —
        # it gets its own row above them (never inside a subnet), not a
        # sibling beside them. Beside-placement stranded it at the VPC's far
        # right edge in the common single-zone case, forcing every edge from
        # the gateway to the zone's services to cross the zone's full width.
        # Centred over the zone span rather than left-aligned so a multi-zone
        # fan-out leaves it symmetrically instead of every branch heading right.
        content_right = geo.PAD
        zones_top = geo.TITLE_PAD
        if vpc.gateway:
            self.w.vertex(
                id=vpc.gateway.id,
                label=leaf_label(vpc.gateway),
                style=theme.leaf_style(),
                x=geo.PAD + max(0.0, (zones_span - geo.LEAF_W) / 2),
                y=geo.TITLE_PAD,
                w=geo.LEAF_W, h=geo.LEAF_H,
                parent=vpc.id,
                shape_type=vpc.gateway.shape_type,
            )
            content_right = geo.PAD + geo.LEAF_W
            zones_top = geo.TITLE_PAD + geo.LEAF_H + geo.LABEL_BAND_H + geo.GATEWAY_GAP_BELOW

        # Zones sit in a row below the gateway (or at the top of the VPC if
        # there is none), all sharing one top y, width and height.
        zone_x = geo.PAD
        for zone in vpc.zones:
            self._emit_zone(zone, vpc.id, zone_x, zones_top, tier_y, tier_h, zone_box)
            zone_x += zone_box.width + geo.ZONE_GAP_X
        zones_bottom = zones_top + zone_box.height
        content_right = max(content_right, geo.PAD + zones_span)

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

        # Edge/global layer goes ABOVE the regions, not below them. It is where
        # the request path *enters* the cloud (DNS -> CDN -> WAF -> gateway), and
        # the gateway is at the top of the VPC — emitting the edge layer last
        # parked it at the bottom of the canvas, so the primary path ran
        # bottom-to-top across the whole diagram and its edges were dragged
        # through the region, both zones and the cross-cutting shelves on the
        # way (diagnose_xml reported them "passing through" shelf icons). With
        # the edge layer on top, flow reads straight down: actors -> edge ->
        # gateway -> app -> data.
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

        # Connectivity hubs (Transit Gateway, VPN, Direct Connect) sit at cloud
        # level below the regions they join, inside their own labelled shelf.
        #
        # Emitted as bare leaves parented straight onto the Cloud box before,
        # which put them wrong in three ways: they had no container, so a
        # Transit Gateway read as an icon dropped into empty cloud space with no
        # stated role; the row used a raw `index * pitch` with no 4-per-row wrap,
        # so a fourth hub onwards marched off to the right and dragged the Cloud
        # (and the page) wider with it; and their footprint ignored the title
        # band every other shelf reserves. Routing them through the same
        # leaf-grid helper as the edge layer fixes all three at once — same
        # wrap cap, same padding, same label band.
        if cloud.connectivity_hubs:
            hub_shelf_id = self._unique_id("connectivity_layer")
            box = emit_leaf_grid(self.w, cloud.connectivity_hubs, hub_shelf_id, is_group=False)
            self.w.vertex(
                id=hub_shelf_id,
                label="Connectivity / Hybrid",
                style=theme.edge_layer_style(),
                x=geo.PAD, y=cursor_y, w=box.width, h=box.height,
                parent=cloud.id,
            )
            content_right = max(content_right, geo.PAD + box.width)
            cursor_y += box.height + geo.EDGE_LAYER_GAP_Y

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
