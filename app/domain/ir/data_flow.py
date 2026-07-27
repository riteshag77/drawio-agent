"""Data-flow (data journey) view IR.

Structural rules lifted from ``config/xml_gen/views/data_flow.txt``: the
phase-id vocabulary is fixed; `phase_consume` and `phase_saas` are both
shelves stacked under `phase_store` (not peer columns); `phase_control` is a
shelf below the *entire* row rather than under one phase. Judgment calls —
which producer/consumer goes where, the required DLQ/backup/replica/schema-
registry companions — stay in ``config/xml_gen/ir/data_flow.txt``.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import model_validator

from app.domain.ir.corridor import CorridorIR

_ROW_IDS = frozenset({"phase_ingest", "phase_process", "phase_store"})
_STORE_SHELF_IDS = frozenset({"phase_consume", "phase_saas"})
_WHOLE_ROW_SHELF_ID = "phase_control"


class DataFlowIR(CorridorIR):
    view: Literal["data_flow"] = "data_flow"
    id_prefix: ClassVar[str] = "phase_"

    @model_validator(mode="after")
    def _check_phase_vocabulary(self) -> "DataFlowIR":
        for corridor in self.corridors:
            if corridor.id not in _ROW_IDS:
                raise ValueError(
                    f"phase id {corridor.id!r} is not one of {sorted(_ROW_IDS)} — the layout "
                    "engine only recognizes these exact phase ids in the main row"
                )
        for shelf in self.anchored:
            if shelf.id in _STORE_SHELF_IDS:
                if shelf.anchor_id != "phase_store":
                    raise ValueError(f"{shelf.id!r} must anchor under 'phase_store' (got {shelf.anchor_id!r})")
            elif shelf.id == _WHOLE_ROW_SHELF_ID:
                if shelf.anchor_id is not None:
                    raise ValueError(f"{_WHOLE_ROW_SHELF_ID!r} anchors below the whole row — anchor_id must be None")
            else:
                raise ValueError(
                    f"anchored corridor id {shelf.id!r} is not one of "
                    f"{sorted(_STORE_SHELF_IDS | {_WHOLE_ROW_SHELF_ID})} in Data flow"
                )
        return self
