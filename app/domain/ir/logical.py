"""Logical (capability/dependency) view IR.

Structural rules for Logical corridors (see ``config/xml_gen/ir/logical.txt``):
domain vocabulary is fixed (the layout engine finds domains by exact id, not
label), `domain_saas` is a shelf anchored under `domain_data` rather than a
peer column, and external actors (Persona, IdP) never end up nested inside a
domain because they have their own list.

**Compile note:** ``domain_*`` ids are JSON ranks for L→R order only. The
corridor compiler emits a flat next-ai-style storyboard (no Experience/Core/
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
_INFRA_NOISE_DENYLIST = frozenset({
    "cloudwatch", "secrets_manager", "secret_manager", "key_vault", "_kms",
    "guardduty", "_waf", "shield", "aws_iam", "azure_iam", "gcp_iam",
    "x_ray", "xray", "azure_monitor", "cloud_logging", "cloudtrail",
})


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
        return cls._strip_cross_cutting_infra(data)

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
            if shelf.anchor_id != "domain_data":
                raise ValueError(f"{_SHELF_ID!r} must anchor under 'domain_data' (got {shelf.anchor_id!r})")
        return self

    #: Substrings in a leaf's label that mark it as a replica/standby copy of
    #: another store — an HA/resilience concern for Network (Multi-AZ), not
    #: part of the Logical process story. See semantic_errors below.
    _REPLICA_MARKERS: ClassVar[tuple[str, ...]] = (
        "replica", "standby", "read-replica", "read replica", "mirror",
    )

    def semantic_errors(self) -> list[str]:
        """Hard density/overcrowding rules, plus two story-completeness rules:
        no unconnected leaf/actor (``CorridorIR._orphan_errors`` — Logical-only,
        see that method's docstring for why Security/Data-flow opt out), no
        replica/standby duplicate of a store.

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
        total = sum(len(c.leaves) for c in self.corridors) + sum(len(s.leaves) for s in self.anchored)
        if total > 14:
            errors.append(
                f"Logical has {total} leaves — keep ≤14 (prefer 5–10) so the "
                "journey stays readable left-to-right"
            )
        for corridor in list(self.corridors) + list(self.anchored):
            if len(corridor.leaves) > 5:
                errors.append(
                    f"{corridor.id} has {len(corridor.leaves)} leaves — keep ≤5 "
                    "per domain/shelf; merge or omit padding capabilities"
                )

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