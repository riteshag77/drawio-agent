"""Logical (capability/dependency) view IR.

Structural rules for Logical corridors (see ``config/xml_gen/ir/logical.txt``):
domain vocabulary is fixed (the layout engine finds domains by exact id, not
label), `domain_saas` is a shelf anchored under `domain_data` rather than a
peer column, and external actors (Persona, IdP) never end up nested inside a
domain because they have their own list. Judgment calls — which capability
goes in which domain, the cache-aside companion pattern — stay in
``config/xml_gen/ir/logical.txt`` and are not re-litigated here.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from app.domain.ir.corridor import CorridorIR

_ROW_IDS = frozenset({"domain_experience", "domain_core", "domain_commerce", "domain_data", "domain_platform"})
_SHELF_ID = "domain_saas"


class LogicalIR(CorridorIR):
    view: Literal["logical"] = Field(
        default="logical", description="Must be exactly the lowercase string 'logical' — no other casing."
    )
    id_prefix: ClassVar[str] = "domain_"

    @classmethod
    def sanitize_raw(cls, data: object) -> object:
        """Pre-parse relocate — see ``SecurityIR.sanitize_raw``."""
        return cls._relocate_vocabulary(
            data, row_ids=_ROW_IDS, shelf_anchors={_SHELF_ID: "domain_data"},
        )

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
        for shelf in self.anchored:
            if shelf.id != _SHELF_ID:
                raise ValueError(f"anchored corridor id {shelf.id!r} must be {_SHELF_ID!r} in Logical")
            if shelf.anchor_id != "domain_data":
                raise ValueError(f"{_SHELF_ID!r} must anchor under 'domain_data' (got {shelf.anchor_id!r})")
        return self

    def semantic_warnings(self) -> list[str]:
        """Promotes the cache-aside "required companion" rule from
        ``config/xml_gen/ir/logical.txt`` prose into code — see that file's
        "Required: cache-aside companion" section for the judgment call this
        mirrors.
        """
        saas_shelf = next((s for s in self.anchored if s.id == _SHELF_ID), None)
        if saas_shelf is None:
            return []
        core = self.corridor_by_id("domain_core") or self.corridor_by_id("domain_commerce")
        if core is not None and not self.leaves_mention(core.leaves, "cache"):
            return [
                "domain_saas shelf is present but no domain_core/domain_commerce leaf mentions "
                "a cache — the cache-aside companion rule calls for one for SaaS-outage "
                "resilience."
            ]
        return []
