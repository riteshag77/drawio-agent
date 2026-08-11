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
from app.domain.ir.security import _PERIMETER_SERVICE_SHAPE_TYPES, perimeter_leaf_id

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
    "These groups must have ZERO flows."
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


def _collect_network_ids(data: dict, *, excluding: list[dict]) -> set[str]:
    """Every id already present in a raw NetworkIR payload — the Network
    equivalent of ``security._collect_taken_ids`` (see it for why relocating
    actors are excluded). Walks the nested cloud hierarchy rather than a flat
    corridor list."""
    moving = {id(a) for a in excluding}
    taken: set[str] = set()

    def _add(obj: object) -> None:
        if isinstance(obj, dict) and isinstance(obj.get("id"), str):
            taken.add(obj["id"])

    cloud = data.get("cloud")
    if isinstance(cloud, dict):
        _add(cloud)
        edge_layer = cloud.get("edge_layer")
        if isinstance(edge_layer, dict):
            _add(edge_layer)
            for leaf in edge_layer.get("leaves") or []:
                _add(leaf)
        for hub in cloud.get("connectivity_hubs") or []:
            _add(hub)
        for region in cloud.get("regions") or []:
            if not isinstance(region, dict):
                continue
            _add(region)
            for vpc in region.get("vpcs") or []:
                if not isinstance(vpc, dict):
                    continue
                _add(vpc)
                _add(vpc.get("gateway"))
                for zone in vpc.get("zones") or []:
                    if not isinstance(zone, dict):
                        continue
                    _add(zone)
                    for subnet in zone.get("subnets") or []:
                        if not isinstance(subnet, dict):
                            continue
                        _add(subnet)
                        for leaf in subnet.get("leaves") or []:
                            _add(leaf)
                for group in vpc.get("groups") or []:
                    if not isinstance(group, dict):
                        continue
                    _add(group)
                    for leaf in group.get("leaves") or []:
                        _add(leaf)
    for actor in data.get("external_actors") or []:
        if isinstance(actor, dict) and id(actor) not in moving:
            _add(actor)
    return taken


def _relocate_perimeter_services_from_actors(data: object) -> object:
    """Move any external_actors entry matching ``_PERIMETER_SERVICE_SHAPE_TYPES``
    into a Leaf under ``cloud.edge_layer``, creating that layer if it doesn't
    exist yet. Same table and stage as
    ``SecurityIR._relocate_perimeter_services_from_actors`` (imported, not
    duplicated) — Network has no fixed ``zone_public``-equivalent corridor to
    relocate into, but ``edge_layer`` is the documented home for CDN/DNS/WAF/
    static-hosting services per network.txt, so that's the target here.
    """
    if not isinstance(data, dict):
        return data
    actors = data.get("external_actors")
    if not isinstance(actors, list) or not actors:
        return data

    kept: list[object] = []
    moving: list[tuple[dict, str]] = []
    for actor in actors:
        label = actor.get("label") if isinstance(actor, dict) else None
        shape_type = _PERIMETER_SERVICE_SHAPE_TYPES.get((label or "").strip().lower())
        if isinstance(actor, dict) and shape_type:
            moving.append((actor, shape_type))
        else:
            kept.append(actor)
    if not moving:
        return data

    # Same id-synthesis rules as Security's original — see perimeter_leaf_id
    # for why "actor id or label" was wrong (multi-word vendor labels are not
    # legal ids, and the resulting failure is swallowed into a direct-XML
    # fallback). The taken-id walk differs only because Network's payload is
    # nested (cloud -> regions -> vpcs -> zones -> subnets) rather than a flat
    # corridor list.
    taken = _collect_network_ids(data, excluding=[a for a, _ in moving])
    relocated: list[dict[str, str]] = []
    for actor, shape_type in moving:
        leaf_id = perimeter_leaf_id(actor, shape_type, taken)
        taken.add(leaf_id)
        relocated.append({
            "id": leaf_id,
            "shape_type": shape_type,
            "label": actor.get("label"),
            "annotation": "",
            "criticality": "standard",
        })

    data = dict(data)
    data["external_actors"] = kept
    # Shallow-copy cloud/edge_layer so mutating edge_layer's leaves below
    # never reaches back into the caller's original nested structure.
    cloud = dict(data["cloud"]) if isinstance(data.get("cloud"), dict) else {}
    edge_layer = dict(cloud["edge_layer"]) if isinstance(cloud.get("edge_layer"), dict) else None
    if edge_layer is None:
        edge_layer = {"id": "edge_layer", "label": "Edge / Global Layer", "leaves": []}
    edge_layer["leaves"] = list(edge_layer.get("leaves") or []) + relocated
    cloud["edge_layer"] = edge_layer
    data["cloud"] = cloud
    return data


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
    # Declared last, after business_notes: a distinct narrative ask (why a
    # choice was made, not what it does) — only worth naming once the design
    # itself is finished, same reasoning as business_notes above.
    design_tradeoffs: list[str] = Field(
        default_factory=list,
        description=(
            "2-4 short bullets (1 sentence each), each naming ONE genuine "
            "architectural choice in this design and the specific alternative "
            "it beat — e.g. 'NAT Gateway over NAT instances: managed, no "
            "patching burden.' Only include choices with a real, nameable "
            "alternative a competent architect would have actually considered. "
            "Leave empty rather than invent a tradeoff that wasn't a genuine "
            "judgment call."
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

    def leaf_tiers(self) -> dict[str, str]:
        """Map every subnet-hosted leaf id to its tier (public/app/data).

        Leaves outside a subnet (edge_layer, connectivity_hubs, gateway) are
        deliberately absent — they have no tier, only subnet members do.
        """
        tiers: dict[str, str] = {}
        for region in self.cloud.regions:
            for vpc in region.vpcs:
                for zone in vpc.zones:
                    for subnet in zone.subnets:
                        for leaf in subnet.leaves:
                            tiers[leaf.id] = subnet.tier
        return tiers

    def leaf_vpc_ids(self) -> dict[str, str]:
        """Map every VPC-scoped leaf id (subnet leaves, gateway, group members)
        to its owning VPC id — used to test connectivity-hub cardinality."""
        ids: dict[str, str] = {}
        for region in self.cloud.regions:
            for vpc in region.vpcs:
                if vpc.gateway:
                    ids[vpc.gateway.id] = vpc.id
                for zone in vpc.zones:
                    for subnet in zone.subnets:
                        for leaf in subnet.leaves:
                            ids[leaf.id] = vpc.id
                for group in vpc.groups:
                    for leaf in group.leaves:
                        ids[leaf.id] = vpc.id
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

    @classmethod
    def sanitize_raw(cls, data: object) -> object:
        """Pre-parse relocate — see ``SecurityIR.sanitize_raw``.

        Network had no ``model_validator(mode="before")`` at all before this
        — no scaffolding to hang a fixer on, unlike every other corridor-
        shaped view. It's also the view most exposed to actor/leaf boundary
        misclassification: it's the one place perimeter/edge services
        (Route 53, CloudFront, WAF, ALB) legitimately belong, so a model
        reaching for that vocabulary is likeliest to also misfile one as an
        external actor here. Reuses Security's own
        ``_PERIMETER_SERVICE_SHAPE_TYPES`` lookup rather than a third copy of
        the same vendor-label table.
        """
        return _relocate_perimeter_services_from_actors(data)

    @model_validator(mode="before")
    @classmethod
    def _relocate_misplaced_actors(cls, data: object) -> object:
        return cls.sanitize_raw(data)

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
        if leaf_n >= 4 and not self.design_tradeoffs:
            warnings.append(
                "design_tradeoffs is empty — name 2-4 genuine architectural choices in this "
                "design and the specific alternative each one beat"
            )

        # network.txt: "Never leave label blank on the primary path; empty
        # labels make Network diagrams unreadable for platform reviews."
        # Flow.label only truncates to 6 words — nothing enforces non-empty.
        for flow in self.flows:
            if flow.primary and not (flow.label or "").strip():
                warnings.append(
                    f"primary flow {flow.source!r} -> {flow.target!r} has no label — "
                    "every flow needs a short label, especially the primary path"
                )

        # network.txt implies the primary request path is the spine of the
        # diagram — a data-tier subnet that the primary path never reaches is
        # a topology only reachable off-path, which is unusual enough to flag
        # rather than silently accept. Gated on external_actors + an actual
        # primary flow existing, mirroring the ingress-path warning above, so
        # a leaf-only structural diagram with no actors (a legitimate shape —
        # see `_external_actor_orphan_errors`) never trips this.
        leaf_tiers = self.leaf_tiers()
        if self.external_actors and any(f.primary for f in self.flows) and "data" in leaf_tiers.values():
            entry_ids: set[str] = {a.id for a in self.external_actors}
            if self.cloud.edge_layer:
                entry_ids.update(leaf.id for leaf in self.cloud.edge_layer.leaves)
            for region in self.cloud.regions:
                for vpc in region.vpcs:
                    if vpc.gateway:
                        entry_ids.add(vpc.gateway.id)

            adjacency: dict[str, list[str]] = {}
            for flow in self.flows:
                if flow.primary:
                    adjacency.setdefault(flow.source, []).append(flow.target)

            visited: set[str] = set()
            queue = list(entry_ids)
            while queue:
                node = queue.pop()
                if node in visited:
                    continue
                visited.add(node)
                queue.extend(adjacency.get(node, []))

            if not any(leaf_tiers.get(node) == "data" for node in visited):
                warnings.append(
                    "a data-tier subnet exists but the primary flow path never reaches it — "
                    "the primary request path should flow through to the data tier "
                    "(e.g. edge -> app -> data)"
                )

        # network.txt: connectivity hubs are "only for hubs that span multiple
        # VPCs, accounts, or regions... A hub serving one VPC is not a hub,
        # put it in a subnet." Gated on the hub actually having a flow yet —
        # early in a multi-turn edit a hub may be declared with no edges at
        # all, which shouldn't hard-fail a judgment call this soft.
        if self.cloud.connectivity_hubs:
            leaf_vpc_ids = self.leaf_vpc_ids()
            for hub in self.cloud.connectivity_hubs:
                touched_vpcs: set[str] = set()
                has_flow = False
                for flow in self.flows:
                    if flow.source == hub.id:
                        other = flow.target
                    elif flow.target == hub.id:
                        other = flow.source
                    else:
                        continue
                    has_flow = True
                    vpc_id = leaf_vpc_ids.get(other)
                    if vpc_id:
                        touched_vpcs.add(vpc_id)
                if has_flow and len(touched_vpcs) < 2:
                    warnings.append(
                        f"connectivity hub {hub.label!r} only touches {len(touched_vpcs)} VPC(s) via "
                        "its flows — hubs are for connections spanning multiple VPCs/accounts/regions; "
                        "a hub serving one VPC is not a hub, put it in a subnet instead"
                    )

        return warnings
