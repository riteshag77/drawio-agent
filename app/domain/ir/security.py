"""Security / trust-zone view IR.

Structural rules lifted from ``config/xml_gen/views/security.txt``: the eight
trust-zone ids are a fixed, engine-recognized vocabulary; `zone_external_saas`
is a shelf anchored under `zone_restricted` rather than a rightmost peer.
Judgment calls — the partner-vs-SaaS distinction, required resilience/partner
companions — stay in ``config/xml_gen/ir/security.txt``.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import model_validator

from app.domain.ir.corridor import CorridorIR

_ROW_IDS = frozenset({
    "zone_public", "zone_dmz", "zone_identity", "zone_partner",
    "zone_private", "zone_restricted", "zone_detect",
})
_SHELF_ID = "zone_external_saas"


class SecurityIR(CorridorIR):
    view: Literal["security"] = "security"
    id_prefix: ClassVar[str] = "zone_"

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
