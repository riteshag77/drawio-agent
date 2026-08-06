"""Security / trust-zone view IR.

Structural rules for Security corridors (see ``config/xml_gen/ir/security.txt``):
the eight trust-zone ids are a fixed, engine-recognized vocabulary;
`zone_external_saas` is a shelf anchored under `zone_restricted` rather than a
rightmost peer. Judgment calls — the partner-vs-SaaS distinction, required
resilience/partner companions — stay in ``config/xml_gen/ir/security.txt``.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from app.domain.ir.corridor import CorridorIR

_ROW_IDS = frozenset({
    "zone_public", "zone_dmz", "zone_identity", "zone_partner",
    "zone_private", "zone_restricted", "zone_detect",
})
_SHELF_ID = "zone_external_saas"


class SecurityIR(CorridorIR):
    view: Literal["security"] = Field(
        default="security", description="Must be exactly the lowercase string 'security' — no other casing."
    )

    # Overview-panel metadata, declared after the structural fields (and
    # after business_notes) for the same "decide the diagram before
    # summarizing it" reasoning as DiagramIR's own field-order docstring —
    # all optional/empty by default since not every field applies to every
    # request (e.g. threat_model_reference is often genuinely "N/A"). The
    # compiler builds the rendered Security overview bullets FROM these named
    # fields (see compiler/corridor.py) rather than the model authoring a
    # flat business_notes list directly for this view, so the panel always
    # presents them in the same fixed order/labels regardless of what order
    # the model happened to fill them in.
    purpose: str = Field(
        default="", description="1-2 sentences: what security posture/controls this view represents and why it exists.",
    )
    scope_included: str = Field(
        default="", description="Zones/systems/control domains actually shown (e.g. IAM, network security, encryption, WAF).",
    )
    scope_excluded: str = Field(
        default="", description='Security domains deliberately left out (e.g. "Physical security controls not shown").',
    )
    threat_model_reference: str = Field(
        default="", description='Framework this view aligns to if any (e.g. "STRIDE", "MITRE ATT&CK"), else "N/A".',
    )
    data_classification: str = Field(
        default="", description="Sensitivity tiers of data the controls shown protect (PII, PHI, PCI, Financial, Actuarial).",
    )
    compliance_frameworks: str = Field(
        default="", description="Applicable regulations driving these controls (NAIC, HIPAA, SOC2, GDPR, Solvency II).",
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
        return cls._relocate_vocabulary(
            data, row_ids=_ROW_IDS, shelf_anchors={_SHELF_ID: "zone_restricted"},
        )

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
            if shelf.anchor_id != "zone_restricted":
                raise ValueError(f"{_SHELF_ID!r} must anchor under 'zone_restricted' (got {shelf.anchor_id!r})")
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
        always be fillable) rather than all nine — most of the others are
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
        boundary_ids = {c.id for c in self.corridors if c.id in self._BOUNDARY_ZONE_IDS}
        if boundary_ids:
            leaf_zone = {leaf.id: c.id for c in self.all_corridors() for leaf in c.leaves}
            for flow in self.flows:
                touches_boundary = leaf_zone.get(flow.source) in boundary_ids or leaf_zone.get(flow.target) in boundary_ids
                if touches_boundary and flow.protection == "none":
                    warnings.append(
                        f"flow {flow.source!r} -> {flow.target!r} crosses a public/partner "
                        "boundary zone with protection='none' — set 'tls' or 'mtls' if transport "
                        "protection is actually in effect here."
                    )
        return warnings
