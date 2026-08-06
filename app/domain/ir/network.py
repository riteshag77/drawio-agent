"""Network / deployment view IR.

Every rule in ``config/xml_gen/ir/network.txt`` that can be expressed
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

from app.domain.ir.base import ActorKind, DiagramIR, ExternalActor, Flow, Identified, Leaf

__all__ = [
    "ActorKind",  # re-exported for backward compatibility — canonical home is base.py
    "AvailabilityZone",
    "CloudBoundary",
    "CrossCuttingGroup",
    "EdgeLayer",
    "ExternalActor",  # re-exported — canonical home is base.py, shared by every view
    "GroupKind",
    "NetworkIR",
    "Region",
    "Subnet",
    "SubnetTier",
    "Vpc",
]

SubnetTier = Literal["public", "app", "data"]
GroupKind = Literal["security", "monitoring", "integration", "management"]

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
        description="Load balancer / API gateway. Renders centred in its own row above the zones it fronts, never inside a subnet.",
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


class NetworkIR(DiagramIR):
    view: Literal["network"] = Field(
        default="network", description="Must be exactly the lowercase string 'network' — no other casing."
    )
    cloud: CloudBoundary
    # Declared after `cloud` on purpose: models emit JSON fields in schema
    # order, so the hierarchy (the nodes) must exist before the edges that
    # reference it. See DiagramIR's docstring.
    external_actors: list[ExternalActor] = Field(
        default_factory=list,
        description="Everyone outside the cloud boundary (users, ingress IdP, egress SaaS, on-prem).",
    )
    flows: list[Flow] = Field(
        default_factory=list,
        description=(
            "REQUIRED whenever any external actor exists — never empty. The primary request "
            "path plus sparse secondary paths, referencing only ids already declared above."
        ),
    )
    # Declared last on purpose, same reasoning as flows above: the model
    # should only summarize the business impact of this network AFTER it has
    # fully decided the topology, not before.
    business_notes: list[str] = Field(
        default_factory=list,
        description=(
            "1-3 short bullets (1-2 sentences each) translating this network's "
            "infrastructure choices into business-outcome terms — what each part "
            "protects or guarantees, not just what it's named. Plain language; "
            "define any technical term inline the first time it's used."
        ),
    )

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

    def structural_ids(self) -> set[str]:
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

        Starts from ``DiagramIR._base_flow_errors()`` (dangling refs, self-loops
        — identical for every view) and adds Network's own rules on top.
        """
        errors = self._base_flow_errors()
        known = self.all_ids()
        forbidden = self.no_edge_ids()
        leaf_n = len(list(self.iter_leaves()))

        if not self.flows and leaf_n >= 4:
            # A real capture showed a schema-valid NetworkIR — 14 leaves, 0
            # external_actors, 0 flows — render as an unconnected pile of
            # icons with not one arrow on the page. The gate below (added
            # first) only fires when external_actors is non-empty, so a
            # network with real service leaves but no actors sailed straight
            # through. A Network view exists specifically to show traffic
            # connectivity between tiers, not just inventory (that's what
            # container nesting is for) — 4+ services with zero flows is
            # never a legitimate Network diagram. Same leaf_n>=4 threshold as
            # the subnet-tier rule below, for the same "tiny fixtures stay
            # allowed" reason.
            errors.append(
                "network has 4+ services but zero flows — a Network view exists to show "
                "traffic connectivity between tiers; add edges for at least the primary "
                "request path (e.g. public edge -> app tier -> data tier)"
            )
        elif not self.flows and self.external_actors:
            # `flows` defaults to an empty list, so Pydantic never flags a
            # diagram that omits every connection — but external actors
            # (Users, an IdP, a SaaS integration) floating with zero edges
            # into the network is exactly the same "schema-valid, useless"
            # failure mode CorridorIR guards against (see corridor.py's
            # equivalent rule, added after a real capture showed this exact
            # gap for Logical/Security/Data-flow). Gated on external_actors
            # rather than leaf count: container nesting alone can carry
            # meaning for a purely structural diagram with no actors (see
            # test_wrapping_grid_stays_clean), but an actor with nothing to
            # connect to is never intentional.
            errors.append(
                "flows is empty but external_actors is not — add the edges connecting "
                "actors into the network; actors with no connections are not useful"
            )
        errors.extend(self._external_actor_orphan_errors())

        for flow in self.flows:
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

        # Thoroughness: schema-valid single-tier dumps with many leaves are not useful.
        # Tiny fixtures (1–3 leaves) stay allowed for compiler/unit tests.
        if leaf_n >= 4:
            tiers: set[str] = set()
            for region in self.cloud.regions:
                for vpc in region.vpcs:
                    for zone in vpc.zones:
                        for subnet in zone.subnets:
                            tiers.add(subnet.tier)
            if len(tiers) < 2:
                errors.append(
                    "network has 4+ services but only one subnet tier — span at least two of "
                    "public/app/data (e.g. public edge + app, or app + data)"
                )
        return errors

    def _external_actor_orphan_errors(self) -> list[str]:
        """Every external actor must touch at least one flow.

        Mirrors ``CorridorIR._orphan_errors`` but scoped to ``external_actors``
        only — NOT leaves. Network conveys meaning through container nesting (a
        passive leaf like CloudWatch sitting in a subnet with no drawn edge is
        valid, same reasoning Security/Data-flow use to opt leaves out of the
        corridor version of this check); an external actor with nothing to
        connect to is never intentional, exactly like the total-empty-flows
        check above — this catches the *partial* case where some actors are
        wired in and one or more others are not.
        """
        touched: set[str] = set()
        for flow in self.flows:
            touched.add(flow.source)
            touched.add(flow.target)
        orphans = sorted(a.id for a in self.external_actors if a.id not in touched)
        if not orphans:
            return []
        return [
            f"these external actors have no flow connecting them to the network: "
            f"{', '.join(orphans)} — an actor with no connections is not useful, "
            "connect each one or remove it"
        ]

    def semantic_warnings(self) -> list[str]:
        """Judgment-call thoroughness — one best-effort IR correction, never blocks."""
        warnings: list[str] = []
        leaf_n = len(list(self.iter_leaves()))
        if self.external_actors and leaf_n >= 2:
            has_ingress = self.cloud.edge_layer is not None
            if not has_ingress:
                for region in self.cloud.regions:
                    for vpc in region.vpcs:
                        if vpc.gateway is not None:
                            has_ingress = True
                            break
                        for zone in vpc.zones:
                            if any(s.tier == "public" for s in zone.subnets):
                                has_ingress = True
                                break
                    if has_ingress:
                        break
            if not has_ingress:
                warnings.append(
                    "external_actors present but no public subnet, edge layer, or gateway — "
                    "add a clear ingress path for actors into the network"
                )
        # A live capture confirmed the model's raw structured output can omit
        # business_notes entirely even with the field described in the
        # prompt, because it has a default and is therefore never in the
        # schema's "required" set. A judgment call (not every tiny network
        # needs a paragraph), so this is a nudge via the one-shot corrective
        # pass, not a hard block like the zero-flows rule in semantic_errors.
        if leaf_n >= 4 and not self.business_notes:
            warnings.append(
                "business_notes is empty — add 1-3 short bullets translating this network's "
                "infrastructure into business-outcome terms (see the Business narrative "
                "section of your instructions)"
            )
        return warnings
