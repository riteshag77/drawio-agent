"""Logical (capability/dependency) view IR.

Structural rules for Logical corridors (see ``config/xml_gen/ir/logical.txt``):
domain vocabulary is fixed (the layout engine finds domains by exact id, not
label), `domain_saas` is a shelf anchored under `domain_data` rather than a
peer column, and external actors (Persona, IdP) never end up nested inside a
domain because they have their own list.

**Compile note:** ``domain_*`` ids are JSON ranks for L→R order only. The
corridor compiler emits a flat process storyboard (no Experience/Core/
Data boxes; optional one ``cloud_boundary``). Judgment calls — which capability
goes in which domain, the cache-aside companion pattern — stay in
``config/xml_gen/ir/logical.txt`` and are not re-litigated here.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from app.domain.ir.corridor import CorridorIR

_ROW_IDS = frozenset({"domain_experience", "domain_core", "domain_commerce", "domain_data", "domain_platform"})
_SHELF_ID = "domain_saas"

# See CorridorIR._strip_cross_cutting_infra — these never belong on Logical
# (Security/Network own them) regardless of what the LLM tries to add.
#
# Networking-plumbing tokens (vpc/subnet/nat/gateway/cloudfront/alb/route53)
# were added after an audit found logical.txt's own exclude/forbidden lists
# already claimed these get stripped as noise — they didn't, so a model that
# ignored the prompt had no deterministic backstop, unlike WAF/CloudWatch/etc.
# _strip_cross_cutting_infra's own docstring even names CloudFront as an
# observed live-capture failure mode; it just was never actually added here.
# Every token below is checked against real shape_type values seen in this
# app's own capture corpus (aws_cloudfront, aws_route53, aws_alb,
# aws_application_load_balancer, aws_vpc, aws_subnet, aws_nat_gateway,
# aws_internet_gateway, aws_transit_gateway, gcp_vpc, azure_vnet — plus
# "datadog" for the one observability vendor still missing) rather than a
# guessed naming convention.
_INFRA_NOISE_DENYLIST = frozenset({
    "cloudwatch", "secrets_manager", "secret_manager", "key_vault", "_kms",
    "guardduty", "_waf", "shield", "aws_iam", "azure_iam", "gcp_iam",
    "x_ray", "xray", "azure_monitor", "cloud_logging", "cloudtrail",
    "_vpc", "vnet", "_subnet", "nat_gateway", "internet_gateway",
    "transit_gateway", "cloudfront", "application_load_balancer", "_alb",
    "network_load_balancer", "_nlb", "route53", "route_53", "datadog",
})


def _strip_infra_noise_from_actors(data: object) -> object:
    """Drop any external_actors entry matching _INFRA_NOISE_DENYLIST.

    Mirrors SecurityIR's _relocate_perimeter_services_from_actors in shape
    (a deterministic backstop for a model misfiling something across the
    leaf/actor boundary) but not in outcome: Security's version *relocates*
    a misfiled perimeter service into zone_public, because that's exactly
    where it belongs. Logical has no such destination — this vocabulary
    (VPC/CloudFront/WAF/CloudWatch/...) doesn't belong on Logical in ANY
    form, leaf or actor, so the correct fix is to remove it entirely, using
    the same denylist _strip_cross_cutting_infra already applies to leaves
    (one vocabulary, two entry points, instead of two lists to keep in sync).

    Deliberately does NOT attempt the other direction (an internal IdP like
    Cognito misfiled as an actor instead of a domain_core leaf) — logical.txt
    offers both "Cloud IdP as leaf" and "External IdP as actor" as legitimate
    choices depending on the brief, so that's a judgment call, not a
    deterministic fix; relocating it could just as easily be wrong.
    """
    if not isinstance(data, dict):
        return data
    actors = data.get("external_actors")
    if not isinstance(actors, list) or not actors:
        return data

    def _is_noise(actor: object) -> bool:
        if not isinstance(actor, dict):
            return False
        # shape_type (rare on a raw actor dict, but a model can hallucinate
        # one before Pydantic's extra="forbid" would reject it) is already
        # underscore-joined; label is human-readable ("Route 53", "NAT
        # Gateway") — normalize spaces to underscores so both match the same
        # denylist tokens instead of needing a parallel space-separated copy
        # of every multi-word entry.
        needle = (actor.get("shape_type") or actor.get("label") or "").lower().replace(" ", "_")
        return any(token in needle for token in _INFRA_NOISE_DENYLIST)

    kept = [a for a in actors if not _is_noise(a)]
    if len(kept) == len(actors):
        return data
    data = dict(data)
    data["external_actors"] = kept
    # Removing the cell is only half the job: any flow still referencing it is
    # now a dangling id, which semantic_errors() treats as fatal and which
    # costs the view its tab. The leaf-side stripper has always bridged around
    # its removals; this one silently didn't, so a model that filed Route 53 as
    # a saas_ingress actor (rather than a leaf) produced an unrenderable IR.
    removed_ids = {a["id"] for a in actors if isinstance(a, dict) and a.get("id") and _is_noise(a)}
    if removed_ids and isinstance(data.get("flows"), list):
        data["flows"] = CorridorIR._bridge_removed_leaves(data["flows"], removed_ids)
    return data


class LogicalIR(CorridorIR):
    """Sparse process / solution-architecture Logical view.

    Fixed domain vocabulary + optional SaaS shelf (see ``logical.txt``). Cloud
    ``shape_type`` keys are allowed when the brief is serverless/cloud so MCP
    can resolve Cognito/Lambda/DynamoDB icons; Network plumbing still belongs
    on Network/Security, not here.
    """

    view: Literal["logical"] = Field(
        default="logical", description="Must be exactly the lowercase string 'logical' — no other casing."
    )
    _infra_noise_denylist: ClassVar[frozenset[str]] = _INFRA_NOISE_DENYLIST

    @classmethod
    def sanitize_raw(cls, data: object) -> object:
        """Pre-parse relocate — see ``SecurityIR.sanitize_raw``."""
        data = cls._relocate_vocabulary(
            data, row_ids=_ROW_IDS, shelf_anchors={_SHELF_ID: "domain_data"},
        )
        data = cls._strip_cross_cutting_infra(data)
        return _strip_infra_noise_from_actors(data)

    @model_validator(mode="before")
    @classmethod
    def _relocate_misplaced_domains(cls, data: object) -> object:
        """Row/shelf membership is fixed vocabulary — fix mis-slotting in code
        (see SecurityIR._relocate_misplaced_zones for the observed failure)."""
        return cls.sanitize_raw(data)

    @model_validator(mode="after")
    def _check_domain_vocabulary(self) -> "LogicalIR":
        for corridor in self.corridors:
            if corridor.id not in _ROW_IDS:
                raise ValueError(
                    f"domain id {corridor.id!r} is not one of {sorted(_ROW_IDS)} — the layout "
                    "engine only recognizes these exact domain ids"
                )
        core_ids = {c.id for c in self.corridors if c.id in ("domain_core", "domain_commerce")}
        if core_ids == {"domain_core", "domain_commerce"}:
            raise ValueError(
                "use domain_core OR domain_commerce — not both (one middle journey column)"
            )
        for shelf in self.anchored:
            if shelf.id != _SHELF_ID:
                raise ValueError(f"anchored corridor id {shelf.id!r} must be {_SHELF_ID!r} in Logical")
            problem = self.shelf_anchor_error(shelf, "domain_data")
            if problem:
                raise ValueError(problem)
        return self

    #: Substrings in a leaf's label that mark it as a replica/standby copy of
    #: another store — an HA/resilience concern for Network (Multi-AZ), not
    #: part of the Logical process story. See semantic_errors below.
    _REPLICA_MARKERS: ClassVar[tuple[str, ...]] = (
        "replica", "standby", "read-replica", "read replica", "mirror",
    )

    #: Vendor/shape_type markers for profile/directory-style SaaS -- the ONLY
    #: category logical.txt's cache-aside rule applies to ("Do not invent
    #: cache-aside for Stripe / billing / SMS, or for thin Cognito serverless
    #: demos"). Without this table the cache-aside warning fired on ANY
    #: domain_saas shelf regardless of what SaaS it held, including billing
    #: vendors the prompt explicitly carves out.
    _PROFILE_DIRECTORY_SAAS_MARKERS: ClassVar[tuple[str, ...]] = (
        "okta", "workday", "auth0", "onelogin", "ping_identity", "ping identity",
        "azure_ad", "azure ad", "entra", "ldap", "active_directory", "active directory",
    )

    def semantic_errors(self) -> list[str]:
        """Two story-completeness rules: no unconnected leaf/actor
        (``CorridorIR._orphan_errors`` — Logical-only, see that method's
        docstring for why Security/Data-flow opt out), and no replica/standby
        duplicate of a store.

        Density used to be here too — ≤14 leaves overall and ≤5 per corridor,
        as hard errors. They are ``density_notes()`` now: a 24-leaf Logical
        page was compiled and rendered and lays out correctly, so the caps
        were never describing a limit the compiler had, and rejecting a
        journey for being longer than a preference fails the user for
        something they never agreed to.

        Logical has no container nesting to fall back on (unlike Network) and
        no visible zone box (unlike Security) — a leaf or actor with zero
        flows is genuinely invisible-as-connected, not just under-annotated,
        and a real capture showed exactly this: an "Ingestion Svc" leaf and
        an "End Users" actor both declared with no flow touching either one.
        Both classes of bug are fixed here rather than left to the prompt
        alone, for the same reliability reason as ``_strip_cross_cutting_infra``.
        """
        errors = super().semantic_errors()
        errors.extend(self._orphan_errors())
        for corridor in list(self.corridors) + list(self.anchored):
            for leaf in corridor.leaves:
                label_lower = leaf.label.lower()
                if any(marker in label_lower for marker in self._REPLICA_MARKERS):
                    errors.append(
                        f"{corridor.id} leaf {leaf.id!r} ({leaf.label!r}) is a replica/standby "
                        "copy of another store — HA/replica topology belongs on Network "
                        "(Multi-AZ), not Logical; remove it and keep one leaf for the store"
                    )
        return errors

    def density_notes(self) -> list[str]:
        """Log-only density observations — see ``IRModel.density_notes``.

        The figures are the pack's shaping advice (5–10 leaves reads best,
        ≤5 per domain), kept here so an operator can see a dense page in the
        logs. Neither one blocks or triggers a corrective ask.
        """
        notes: list[str] = []
        total = sum(len(c.leaves) for c in self.corridors) + sum(len(s.leaves) for s in self.anchored)
        if total > 14:
            notes.append(
                f"Logical has {total} leaves — denser than the 5–10 a journey reads "
                "best at; drawn in full"
            )
        for corridor in list(self.corridors) + list(self.anchored):
            if len(corridor.leaves) > 5:
                notes.append(
                    f"{corridor.id} has {len(corridor.leaves)} leaves — over the 5 a "
                    "domain/shelf reads best at; drawn in full"
                )
        return notes

    def semantic_warnings(self) -> list[str]:
        """Cache-aside companion for enterprise profile SaaS — not thin Cognito demos.

        Also nudges for ``business_notes``: a live capture confirmed the
        model's raw structured output can omit it entirely even with the
        field described in the prompt, because it has a default and is
        therefore never in the schema's "required" set. A judgment call (a
        tiny fixture genuinely may not need a paragraph), so this is a nudge
        via the one-shot corrective pass, not a hard block.
        """
        warnings: list[str] = []
        if not self.business_notes and len(self.leaf_ids()) >= 2:
            warnings.append(
                "business_notes is empty — add 1-3 short bullets translating this journey "
                "into plain-language business terms (see the Business narrative section of "
                "your instructions)"
            )
        saas_shelf = next((s for s in self.anchored if s.id == _SHELF_ID), None)
        if saas_shelf is None:
            return warnings
        # Cache-aside is a profile/directory-SaaS concern only (the prompt is
        # explicit: never for Stripe/billing/SMS) — a shelf holding only a
        # billing vendor must not trigger this, so gate on the shelf actually
        # containing a profile/directory marker before nudging at all.
        if not self.leaves_mention(saas_shelf.leaves, *self._PROFILE_DIRECTORY_SAAS_MARKERS):
            return warnings
        # Skip when core already hosts cloud IdP (Cognito) — serverless auth path.
        core = self.corridor_by_id("domain_core") or self.corridor_by_id("domain_commerce")
        if core is not None:
            if any(
                (leaf.shape_type or "").lower().startswith(("aws_cognito", "aws_identity"))
                or "cognito" in (leaf.label or "").lower()
                for leaf in core.leaves
            ):
                return warnings
            if not self.leaves_mention(core.leaves, "cache"):
                warnings.append(
                    "domain_saas shelf is present but no domain_core/domain_commerce leaf mentions "
                    "a cache — the cache-aside companion rule calls for one for SaaS-outage "
                    "resilience."
                )
        return warnings