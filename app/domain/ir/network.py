"""Network / deployment view IR.

Every rule in ``config/xml_gen/views/network.txt`` that can be expressed
structurally is expressed here instead of in prose:

* The Cloud -> Region -> VPC -> AZ -> Subnet hierarchy is *nesting*, so a
  "Region floating beside the Cloud box" is not a mistake the model can make.
* External actors (Users / ingress IdP / egress SaaS) are a separate list, so
  they can never end up nested inside a VPC.
* Cross-cutting groups are their own type, so "no edges touch them" is a
  checkable invariant rather than a paragraph of SHOUTING in the prompt.

What is left in the prompt pack is genuinely a judgement call (which services
belong in which tier, what the request path is). What moved here is everything
mechanical.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.domain.ir.base import DiagramIR, Flow, Identified, IRModel, Leaf

SubnetTier = Literal["public", "app", "data"]
GroupKind = Literal["security", "monitoring", "integration", "management"]
ActorKind = Literal["users", "saas_ingress", "saas_egress", "on_prem"]

# Tier render order, top to bottom. Public faces the internet, data sits deepest.
TIER_ORDER: tuple[SubnetTier, ...] = ("public", "app", "data")
GROUP_ORDER: tuple[GroupKind, ...] = ("security", "monitoring", "integration", "management")


class Subnet(Identified):
    """A subnet tier inside one availability zone. Never emitted empty."""

    tier: SubnetTier = Field(description="public | app | data")
    leaves: list[Leaf] = Field(min_length=1, description="Services in this subnet. Must not be empty — omit the subnet instead.")


class AvailabilityZone(Identified):
    label: str = Field(description="Zone name, e.g. 'AZ-1a' or 'us-east-1a'")
    subnets: list[Subnet] = Field(min_length=1, description="Non-empty subnet tiers in this zone.")

    @field_validator("subnets")
    @classmethod
    def _ordered_unique_tiers(cls, value: list[Subnet]) -> list[Subnet]:
        tiers = [s.tier for s in value]
        dupes = [t for t, n in Counter(tiers).items() if n > 1]
        if dupes:
            raise ValueError(f"zone has more than one {dupes[0]!r} subnet — merge them into one")
        # Canonical top-to-bottom order is a layout invariant, not a model choice.
        return sorted(value, key=lambda s: TIER_ORDER.index(s.tier))


class CrossCuttingGroup(Identified):
    """Security / Monitoring / Integration / Management shelf.

    These are inventory, not flow. ``NetworkIR.semantic_errors`` rejects any
    flow touching a member, which is the structural version of network.txt's
    "ABSOLUTE RULE — NO LINES".
    """

    kind: GroupKind
    leaves: list[Leaf] = Field(min_length=1)


class Vpc(Identified):
    label: str = Field(default="VPC", description="e.g. 'VPC', 'VNet', 'prod-vpc'")
    zones: list[AvailabilityZone] = Field(min_length=1)
    gateway: Leaf | None = Field(
        default=None,
        description="Load balancer / API gateway. Sits beside the zones inside the VPC, never inside a subnet.",
    )
    groups: list[CrossCuttingGroup] = Field(
        default_factory=list,
        description="Cross-cutting shelves rendered below the zones. Never connected by edges.",
    )

    @field_validator("groups")
    @classmethod
    def _one_group_per_kind(cls, value: list[CrossCuttingGroup]) -> list[CrossCuttingGroup]:
        dupes = [k for k, n in Counter(g.kind for g in value).items() if n > 1]
        if dupes:
            raise ValueError(f"more than one {dupes[0]!r} group — each kind appears at most once")
        return sorted(value, key=lambda g: GROUP_ORDER.index(g.kind))


class Region(Identified):
    label: str = Field(description="Region name, e.g. 'us-east-1'")
    vpcs: list[Vpc] = Field(min_length=1)


class EdgeLayer(Identified):
    """Global/edge services (CDN, DNS, WAF, static buckets) — inside Cloud, beside Region."""

    label: str = Field(default="Edge / Global Layer")
    leaves: list[Leaf] = Field(min_length=1)


class CloudBoundary(Identified):
    """The outer wrapper. Everything cloud-owned nests inside this."""

    label: str = Field(description="e.g. 'AWS Cloud', 'Azure', 'Google Cloud'")
    regions: list[Region] = Field(min_length=1)
    edge_layer: EdgeLayer | None = None
    connectivity_hubs: list[Leaf] = Field(
        default_factory=list,
        description=(
            "Cross-VPC/account/region hubs (Transit Gateway, VPN Gateway, Direct Connect, PrivateLink). "
            "Placed at cloud level, never inside a single VPC's service row."
        ),
    )


class ExternalActor(Identified):
    """Anything outside the cloud boundary. Always rendered at the root layer.

    ``kind`` drives which side of the Cloud box it lands on, encoding
    network.txt's call-path rule: ingress (what the user hits first) on the
    left, egress (what the app calls out to) on the right.
    """

    kind: ActorKind
    label: str


class NetworkIR(DiagramIR):
    view: Literal["network"] = "network"
    cloud: CloudBoundary
    external_actors: list[ExternalActor] = Field(default_factory=list)
    flows: list[Flow] = Field(default_factory=list)

    # ── id collection ──────────────────────────────────────────────────

    def leaf_ids(self) -> set[str]:
        return {leaf.id for leaf in self.iter_leaves()}

    def iter_leaves(self):
        for region in self.cloud.regions:
            for vpc in region.vpcs:
                if vpc.gateway:
                    yield vpc.gateway
                for zone in vpc.zones:
                    for subnet in zone.subnets:
                        yield from subnet.leaves
                for group in vpc.groups:
                    yield from group.leaves
        if self.cloud.edge_layer:
            yield from self.cloud.edge_layer.leaves
        yield from self.cloud.connectivity_hubs

    def container_ids(self) -> set[str]:
        ids = {self.cloud.id}
        for region in self.cloud.regions:
            ids.add(region.id)
            for vpc in region.vpcs:
                ids.add(vpc.id)
                ids.update(z.id for z in vpc.zones)
                ids.update(s.id for z in vpc.zones for s in z.subnets)
                ids.update(g.id for g in vpc.groups)
        if self.cloud.edge_layer:
            ids.add(self.cloud.edge_layer.id)
        return ids

    def all_ids(self) -> set[str]:
        return self.leaf_ids() | self.container_ids() | {a.id for a in self.external_actors}

    def no_edge_ids(self) -> set[str]:
        """Cross-cutting group containers and their members — flows must not touch these."""
        ids: set[str] = set()
        for region in self.cloud.regions:
            for vpc in region.vpcs:
                for group in vpc.groups:
                    ids.add(group.id)
                    ids.update(leaf.id for leaf in group.leaves)
        return ids

    # ── validation ─────────────────────────────────────────────────────

    @model_validator(mode="after")
    def _check_unique_ids(self) -> "NetworkIR":
        seen: Counter[str] = Counter()
        for region in self.cloud.regions:
            seen[region.id] += 1
            for vpc in region.vpcs:
                seen[vpc.id] += 1
                for zone in vpc.zones:
                    seen[zone.id] += 1
                    for subnet in zone.subnets:
                        seen[subnet.id] += 1
                for group in vpc.groups:
                    seen[group.id] += 1
        seen[self.cloud.id] += 1
        if self.cloud.edge_layer:
            seen[self.cloud.edge_layer.id] += 1
        for leaf in self.iter_leaves():
            seen[leaf.id] += 1
        for actor in self.external_actors:
            seen[actor.id] += 1

        dupes = sorted(i for i, n in seen.items() if n > 1)
        if dupes:
            raise ValueError(f"duplicate ids: {', '.join(dupes)} — every cell id must be unique")
        return self

    def semantic_errors(self) -> list[str]:
        """Cross-field rules, returned as messages rather than raised.

        Returned (not raised) so the generation loop can feed precise, field-level
        feedback back to the model on a retry — "flow x->y touches the security
        group" is a far more actionable correction than "your XML was malformed".
        """
        errors: list[str] = []
        known = self.all_ids()
        forbidden = self.no_edge_ids()

        for flow in self.flows:
            if flow.source not in known:
                errors.append(f"flow source {flow.source!r} is not a known cell id")
            if flow.target not in known:
                errors.append(f"flow target {flow.target!r} is not a known cell id")
            if flow.source == flow.target:
                errors.append(f"flow {flow.source!r} -> itself is not meaningful")
            if flow.source in forbidden or flow.target in forbidden:
                errors.append(
                    f"flow {flow.source!r} -> {flow.target!r} touches a cross-cutting "
                    "(security/monitoring/integration/management) cell — those are inventory shelves "
                    "and must have zero edges"
                )

        for region in self.cloud.regions:
            for vpc in region.vpcs:
                for zone in vpc.zones:
                    for subnet in zone.subnets:
                        if subnet.tier == "data" and any(
                            f.source in {l.id for l in subnet.leaves} and f.target in known
                            and f.target in {a.id for a in self.external_actors}
                            for f in self.flows
                        ):
                            errors.append(
                                f"data-tier subnet {subnet.id!r} has a flow straight out to an external actor — "
                                "data tiers should be reached through the app tier"
                            )
        return errors

    def is_valid(self) -> bool:
        return not self.semantic_errors()
