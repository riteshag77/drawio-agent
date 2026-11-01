"""Security / trust-zone view IR.

Structural rules for Security corridors (see ``config/xml_gen/ir/security.txt``):
the eight trust-zone ids are a fixed, engine-recognized vocabulary;
`zone_external_saas` is a shelf anchored under `zone_restricted` rather than a
rightmost peer. Judgment calls — the partner-vs-SaaS distinction, the required
secrets/detect and partner companions — stay in
``config/xml_gen/ir/security.txt``.
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from app.domain.ir.base import _ID_RE
from app.domain.ir.corridor import CorridorIR

_ROW_IDS = frozenset({
    "zone_public", "zone_dmz", "zone_identity", "zone_partner",
    "zone_private", "zone_restricted", "zone_detect",
})
_SHELF_ID = "zone_external_saas"

#: First-party perimeter/edge services the model sometimes misfiles into
#: external_actors — a live capture showed Route 53 emitted as
#: {"kind": "saas_ingress", "label": "Route 53"} even though security.txt's
#: prompt explicitly forbids "putting any leaf in external_actors that is not
#: actually outside the trust boundary". Prompt wording alone isn't reliable
#: (the same request, resent, sometimes gets this right and sometimes
#: doesn't — Network/Data-flow's IR for the identical service was correct in
#: every one of those same captures), so this is a deterministic code-level
#: backstop rather than another sentence added to the prompt. zone_public is
#: the one unambiguous destination: security.txt already documents "WAF/API
#: GW/public ALB/Shield" as zone_public's contents, and _ROW_ORDER sorts
#: corridors by fixed id regardless of list position, so it's safe to always
#: create/append here without worrying about render order.
#: Keys are exact (lowercased, stripped) actor labels — deliberately not a
#: substring match, so a customer's own generically-named "Load Balancer"
#: actor is never silently relabeled.
_PERIMETER_SERVICE_SHAPE_TYPES: dict[str, str] = {
    # AWS
    "route 53": "aws_route53", "route53": "aws_route53", "amazon route 53": "aws_route53",
    "cloudfront": "aws_cloudfront", "amazon cloudfront": "aws_cloudfront",
    "application load balancer": "aws_application_load_balancer", "alb": "aws_application_load_balancer",
    "network load balancer": "aws_network_load_balancer", "nlb": "aws_network_load_balancer",
    "waf": "aws_waf", "aws waf": "aws_waf",
    "shield": "aws_shield", "aws shield": "aws_shield",
    "api gateway": "aws_api_gateway",
    "global accelerator": "aws_global_accelerator",
    # GCP
    "cloud dns": "gcp_cloud_dns",
    "cloud cdn": "gcp_cloud_cdn",
    "cloud armor": "gcp_cloud_armor",
    "cloud load balancing": "gcp_cloud_load_balancing",
    # Azure
    "azure dns": "azure_dns", "dns zone": "azure_dns",
    "front door": "azure_front_door", "azure front door": "azure_front_door",
    "azure firewall": "azure_firewall",
    "application gateway": "azure_application_gateway",
    "azure load balancer": "azure_load_balancer",
}


def perimeter_leaf_id(actor: dict, shape_type: str, taken: set[str]) -> str:
    """A legal, collision-free leaf id for a relocated perimeter service.

    The obvious ``actor["id"] or label`` is wrong: an actor dict is allowed
    to arrive without an ``id`` (a live capture showed exactly
    ``{"kind": "saas_ingress", "label": "Route 53"}``), and 22 of the 30
    labels in ``_PERIMETER_SERVICE_SHAPE_TYPES`` are multi-word, so the
    label became an id with a space in it. That fails ``base._ID_RE`` — and
    the resulting error names ``corridors.N.leaves.M.id``, a cell the model
    never wrote, inside a corridor this relocation may itself have invented.
    Worse, ``llm_client._recover_structured_parse`` swallows the exception
    and falls through to direct XML — the exact outcome this relocation
    exists to prevent, on the exact input shape it was written for.

    So: keep a legal declared id, else fall back to ``shape_type`` (already
    canonical snake_case, e.g. ``aws_route53``), else normalize the label.
    Then de-duplicate against ``taken``, since a relocated service can
    collide with a leaf the model already declared in zone_public — which
    ``CorridorIR._check_uniqueness`` would turn into a whole-view failure.
    """
    declared = (actor.get("id") or "").strip()
    if declared and _ID_RE.match(declared):
        base = declared
    else:
        normalized = re.sub(r"[^a-zA-Z0-9_]", "_", (actor.get("label") or "").strip().lower()).strip("_")
        base = shape_type if _ID_RE.match(shape_type) else (normalized or "perimeter_service")
        if not _ID_RE.match(base):
            base = "perimeter_service"
    if base not in taken:
        return base
    suffix = 2
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


def _collect_taken_ids(data: dict, *, excluding: list[dict]) -> set[str]:
    """Every id already present in a raw IR payload that a relocated leaf
    must not collide with.

    ``excluding`` is the set of actors being relocated: their ids are moving
    from the actor bucket to the leaf bucket, so an actor keeping its own
    declared id is not a collision with itself. (``_check_uniqueness``
    counts both buckets in one Counter, so a genuine pre-existing clash
    would have failed with or without this relocation.)
    """
    moving = {id(a) for a in excluding}
    taken: set[str] = set()
    for key in ("corridors", "anchored"):
        for container in data.get(key) or []:
            if not isinstance(container, dict):
                continue
            if isinstance(container.get("id"), str):
                taken.add(container["id"])
            for leaf in container.get("leaves") or []:
                if isinstance(leaf, dict) and isinstance(leaf.get("id"), str):
                    taken.add(leaf["id"])
    for actor in data.get("external_actors") or []:
        if isinstance(actor, dict) and id(actor) not in moving and isinstance(actor.get("id"), str):
            taken.add(actor["id"])
    return taken


def _relocate_perimeter_services_from_actors(data: object) -> object:
    """Move any external_actors entry matching ``_PERIMETER_SERVICE_SHAPE_TYPES``
    into a Leaf under zone_public, creating that corridor if it doesn't exist
    yet. Runs on the raw dict, same stage as ``_relocate_vocabulary``, so a
    live capture with this mistake compiles correctly on the first pass
    instead of needing (and hoping for) a corrective LLM round-trip.
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

    # Ids are assigned only after the full moving set is known, so an actor
    # keeping its own declared id isn't treated as colliding with itself.
    taken = _collect_taken_ids(data, excluding=[a for a, _ in moving])
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
    # Shallow-copy each corridor dict so mutating zone_public's leaves below
    # never reaches back into the caller's original nested structure.
    corridors = [dict(c) if isinstance(c, dict) else c for c in (data.get("corridors") or [])]
    zone_public = next((c for c in corridors if isinstance(c, dict) and c.get("id") == "zone_public"), None)
    if zone_public is None:
        zone_public = {"id": "zone_public", "label": "Public", "leaves": []}
        corridors.append(zone_public)
    zone_public["leaves"] = list(zone_public.get("leaves") or []) + relocated
    data["corridors"] = corridors
    return data


class SecurityIR(CorridorIR):
    view: Literal["security"] = Field(
        default="security", description="Must be exactly the lowercase string 'security' — no other casing."
    )

    # Overview-panel metadata, declared after the structural fields (and
    # after business_notes) for the same "decide the diagram before
    # summarizing it" reasoning as DiagramIR's own field-order docstring —
    # all optional/empty by default since not every field applies to every
    # request. An inapplicable field is left EMPTY, never filled with "N/A":
    # compiler/corridor.py::_overview_notes drops empty fields so they
    # produce no bullet at all, which is the whole point of the named-field
    # design — a literal "N/A" would defeat it and render as a bullet.
    # The compiler builds the rendered Security overview bullets FROM these
    # named fields rather than the model authoring a flat business_notes list
    # directly for this view, so the panel always presents them in the same
    # fixed order/labels regardless of what order the model filled them in.
    #: Security's own "so what" line, plus the two every corridor view
    #: carries — see IRModel._schema_required.
    _schema_required: ClassVar[tuple[str, ...]] = (
        *CorridorIR._schema_required, "purpose",
    )

    purpose: str = Field(
        default="",
        description=(
            "1-2 sentences: what security posture/controls this view represents and why it "
            "exists. Not a restatement of the zone list — the 'so what' a risk owner reads first."
        ),
    )
    scope_included: str = Field(
        default="",
        description=(
            "Control domains actually shown (e.g. IAM, network security, encryption, WAF). "
            "Do not list the zones — the compiler derives that bullet itself."
        ),
    )
    scope_excluded: str = Field(
        default="", description='Security domains deliberately left out (e.g. "Physical security controls not shown").',
    )
    threat_model_reference: str = Field(
        default="",
        description=(
            'Framework this view aligns to if any (e.g. "STRIDE", "MITRE ATT&CK"). '
            "Leave empty if none — never invent one, and never write \"N/A\"."
        ),
    )
    data_classification: str = Field(
        default="",
        description=(
            "Sensitivity tiers of data the controls shown protect (PII, PHI, PCI, Financial, "
            "Actuarial), only when the request genuinely handles regulated data. Leave empty "
            "if none — never write 'None', 'N/A' or 'Not applicable' as a value."
        ),
    )
    compliance_frameworks: str = Field(
        default="",
        description=(
            "Applicable regulations driving these controls (NAIC, HIPAA, SOC2, GDPR, Solvency "
            "II), only when one genuinely applies. Leave empty if none — never write 'None', "
            "'N/A' or 'Not applicable' as a value."
        ),
    )
    control_types: str = Field(
        default="",
        description=(
            "Categories of controls shown (authN/authZ, encryption at rest/in transit, "
            "network segmentation, logging/monitoring, key management)."
        ),
    )
    key_assumptions: str = Field(
        default="",
        description=(
            'Controls assumed but not visually depicted (e.g. "Assumes all internal traffic '
            'is mTLS-encrypted — not shown as separate edges").'
        ),
    )

    @classmethod
    def sanitize_raw(cls, data: object) -> object:
        """Pre-parse relocate for LangChain json_mode paths that surface the
        raw dict before (or instead of) running ``mode='before'`` validators.
        Live captures put ``zone_detect`` in ``anchored`` and failed parse even
        though ``model_validate`` with the before-validator relocates it.
        """
        data = cls._relocate_vocabulary(
            data, row_ids=_ROW_IDS, shelf_anchors={_SHELF_ID: "zone_restricted"},
        )
        return _relocate_perimeter_services_from_actors(data)

    @model_validator(mode="before")
    @classmethod
    def _relocate_misplaced_zones(cls, data: object) -> object:
        """Row/shelf membership is fixed vocabulary — fix mis-slotting in code.

        A live capture showed the model emitting ``zone_detect`` (a main-row
        zone) as an anchored shelf, hard-failing the whole view to direct XML
        even though every leaf was right. Relocation is unambiguous, so do it
        deterministically instead of burning a corrective LLM pass.
        """
        return cls.sanitize_raw(data)

    @model_validator(mode="after")
    def _check_zone_vocabulary(self) -> "SecurityIR":
        for corridor in self.corridors:
            if corridor.id not in _ROW_IDS:
                raise ValueError(
                    f"zone id {corridor.id!r} is not one of {sorted(_ROW_IDS)} — the layout "
                    "engine only recognizes these exact zone ids"
                )
        for shelf in self.anchored:
            if shelf.id != _SHELF_ID:
                raise ValueError(f"anchored corridor id {shelf.id!r} must be {_SHELF_ID!r} in Security")
            problem = self.shelf_anchor_error(shelf, "zone_restricted")
            if problem:
                raise ValueError(problem)
        return self

    #: Boundary zones a flow can cross from/to the outside world — a flow
    #: touching either with no transport protection is a judgment call worth
    #: flagging, not a hard error (the request may genuinely be modelling an
    #: intentionally open path).
    _BOUNDARY_ZONE_IDS: ClassVar[frozenset[str]] = frozenset({"zone_public", "zone_partner"})

    def semantic_warnings(self) -> list[str]:
        """Promotes three "required companions" rules from
        ``config/xml_gen/ir/security.txt`` prose into code — see that file's
        "Required companions" section for the judgment calls these mirror.

        Also nudges for the overview-panel fields: same lesson learned for
        business_notes (see NetworkIR/LogicalIR) — an optional field with a
        default is never in the schema's "required" set, so a live capture
        confirmed the model can skip it entirely even when the prompt
        describes it. Only checks ``purpose`` (the one field that should
        always be fillable) rather than all eight — most of the others are
        legitimately blank for a given request (e.g. no applicable threat
        model), and the prompt already says so.
        """
        warnings: list[str] = []
        if not self.purpose.strip() and len(self.leaf_ids()) >= 2:
            warnings.append(
                "purpose is empty — add 1-2 sentences on what security posture/controls this "
                "view represents and why it exists (see the Overview panel metadata section of "
                "your instructions)"
            )
        private = self.corridor_by_id("zone_private")
        saas_shelf = next((s for s in self.anchored if s.id == _SHELF_ID), None)
        if private is not None and saas_shelf is not None:
            if not self.leaves_mention(private.leaves, "secret", "vault", "kms", "key vault"):
                warnings.append(
                    "zone_private has no secrets/KMS leaf even though zone_external_saas is "
                    "present — the required-companions rule calls for one (Secrets Manager / "
                    "Key Vault) when a private app + enterprise SaaS both appear."
                )
            if self.corridor_by_id("zone_detect") is None:
                warnings.append(
                    "zone_external_saas is present but there is no zone_detect corridor — the "
                    "required-companions rule calls for a detection zone (GuardDuty/CloudWatch "
                    "or equivalent) when a private app + enterprise SaaS both appear."
                )
        partner = self.corridor_by_id("zone_partner")
        if partner is not None and not self.leaves_mention(partner.leaves, "gateway"):
            warnings.append(
                "zone_partner is present but has no Partner API Gateway leaf — the "
                "required-companions rule calls for a single dedicated partner ingress/egress "
                "point, distinct from the consumer WAF/ALB."
            )
        leaf_zone = {leaf.id: c.id for c in self.all_corridors() for leaf in c.leaves}
        boundary_ids = {c.id for c in self.corridors if c.id in self._BOUNDARY_ZONE_IDS}
        if boundary_ids:
            for flow in self.flows:
                src_zone, tgt_zone = leaf_zone.get(flow.source), leaf_zone.get(flow.target)
                # A *crossing* means exactly one end sits in a boundary zone.
                # Testing "either end touches one" also fired on flows with
                # BOTH ends inside zone_public (e.g. Route 53 -> CloudFront),
                # which cross nothing — that false positive burned the single
                # corrective LLM pass and double-counted as a conformance
                # violation (view_conformance.py mirrors this rule).
                if (src_zone in boundary_ids) == (tgt_zone in boundary_ids):
                    continue
                if flow.protection == "none":
                    warnings.append(
                        f"flow {flow.source!r} -> {flow.target!r} crosses a public/partner "
                        "boundary zone with protection='none' — set 'tls' or 'mtls' if transport "
                        "protection is actually in effect here."
                    )

        # security.txt's Forbidden list: "routing partner traffic straight
        # into zone_private without passing through zone_partner's Partner
        # API Gateway". The most Security-specific structural rule in the
        # pack, and nothing noticed it before.
        for flow in self.flows:
            if leaf_zone.get(flow.source) != "zone_partner" or leaf_zone.get(flow.target) != "zone_private":
                continue
            source_leaf = next(
                (l for c in self.all_corridors() for l in c.leaves if l.id == flow.source), None,
            )
            if source_leaf is not None and "gateway" not in source_leaf.label.lower():
                warnings.append(
                    f"flow {flow.source!r} -> {flow.target!r} routes partner traffic straight into "
                    "zone_private — partner traffic must enter through zone_partner's Partner API "
                    "Gateway, never directly."
                )

        # security.txt: "Set each flow's structured protection to match its
        # label: mtls for any (mTLS)-labeled flow, tls for (TLS)-labeled."
        # Label-vs-field consistency is mechanically checkable; previously a
        # flow labeled "Partner API (mTLS)" with protection='none' was silent
        # unless it happened to touch a boundary zone.
        for flow in self.flows:
            low = (flow.label or "").lower()
            if flow.protection != "none":
                continue
            if "mtls" in low or "(tls)" in low or low.endswith("tls"):
                warnings.append(
                    f"flow {flow.source!r} -> {flow.target!r} is labeled {flow.label!r} but has "
                    "protection='none' — set the structured field to match the label ('mtls' or 'tls')."
                )

        # design_tradeoffs is declared on CorridorIR, returned by the API and
        # rendered as its own card, and its own docstring names Security as a
        # view where it matters most — but nothing asked for it here. Same
        # nudge Network and Data-flow already carry.
        if len(self.leaf_ids()) >= 4 and not self.design_tradeoffs:
            warnings.append(
                "design_tradeoffs is empty — name 2-4 genuine control choices in this design and "
                "the specific alternative each one beat (see the Design tradeoffs section of your "
                "instructions)"
            )
        return warnings
