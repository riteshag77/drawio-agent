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
    id_prefix: ClassVar[str] = "zone_"

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
        """
        warnings: list[str] = []
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
