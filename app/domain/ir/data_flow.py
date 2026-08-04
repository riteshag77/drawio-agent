"""Data-flow (data journey) view IR.

Structural rules for Data-flow corridors (see ``config/xml_gen/ir/data_flow.txt``):
the phase-id vocabulary is fixed; `phase_consume`, `phase_saas_producer` and
`phase_saas_consumer` are all shelves stacked under `phase_store` (not peer
columns); `phase_control` is a shelf below the *entire* row rather than under
one phase. The SaaS shelf is split by data direction rather than being one
undifferentiated bucket: `phase_saas_producer` for SaaS systems that feed data
INTO the pipeline, `phase_saas_consumer` for systems the pipeline sends data
OUT to — this is the model's own per-leaf decision (see
``config/xml_gen/ir/data_flow.txt``), the same way it already decides which of
`phase_ingest`/`phase_process`/`phase_store` a leaf belongs to. Judgment calls
— the required DLQ/backup/replica/schema-registry companions — stay in that
prompt pack.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from app.domain.ir.corridor import CorridorIR

_ROW_IDS = frozenset({"phase_ingest", "phase_process", "phase_store"})
_STORE_SHELF_IDS = frozenset({"phase_consume", "phase_saas_producer", "phase_saas_consumer"})
_WHOLE_ROW_SHELF_ID = "phase_control"

# See CorridorIR._strip_cross_cutting_infra — these never belong on Data-flow
# (Security/Network own them) regardless of what the LLM tries to add.
_INFRA_NOISE_DENYLIST = frozenset({
    "cloudwatch", "secrets_manager", "secret_manager", "key_vault", "_kms",
    "guardduty", "_waf", "shield", "aws_iam", "azure_iam", "gcp_iam",
    "x_ray", "xray", "azure_monitor", "cloud_logging", "cloudtrail",
})


class DataFlowIR(CorridorIR):
    view: Literal["data_flow"] = Field(
        default="data_flow", description="Must be exactly the lowercase string 'data_flow' — no other casing."
    )
    _infra_noise_denylist: ClassVar[frozenset[str]] = _INFRA_NOISE_DENYLIST

    @classmethod
    def sanitize_raw(cls, data: object) -> object:
        """Pre-parse relocate — see ``SecurityIR.sanitize_raw``."""
        data = cls._relocate_vocabulary(
            data,
            row_ids=_ROW_IDS,
            shelf_anchors={
                "phase_consume": "phase_store",
                "phase_saas_producer": "phase_store",
                "phase_saas_consumer": "phase_store",
                _WHOLE_ROW_SHELF_ID: None,
            },
        )
        return cls._strip_cross_cutting_infra(data)

    @model_validator(mode="before")
    @classmethod
    def _relocate_misplaced_phases(cls, data: object) -> object:
        """Row/shelf membership is fixed vocabulary — fix mis-slotting in code
        (see SecurityIR._relocate_misplaced_zones for the observed failure).
        Data-flow is the likeliest to trip this: ``phase_consume`` reads like a
        fourth journey phase but is defined as a shelf under ``phase_store``."""
        return cls.sanitize_raw(data)

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

    def semantic_warnings(self) -> list[str]:
        """Promotes two "required companions" rules from
        ``config/xml_gen/ir/data_flow.txt`` prose into code — see that file's
        "Required companions" section for the judgment calls these mirror.
        """
        warnings: list[str] = []
        ingest = self.corridor_by_id("phase_ingest")
        if ingest is not None and self.leaves_mention(
            ingest.leaves, "kafka", "kinesis", "stream", "event hub", "pub/sub",
        ):
            process = self.corridor_by_id("phase_process")
            has_dlq = process is not None and self.leaves_mention(
                process.leaves, "dlq", "dead-letter", "dead letter", "retry",
            )
            if not has_dlq:
                warnings.append(
                    "phase_ingest has a streaming/event leaf but phase_process has no "
                    "dead-letter/DLQ leaf — the required-companions rule calls for one when "
                    "ingest is streaming."
                )
        store = self.corridor_by_id("phase_store")
        if store is not None and not self.leaves_mention(store.leaves, "snapshot", "archive", "backup"):
            warnings.append(
                "phase_store has no snapshot/archive/backup leaf — the required-companions "
                "rule calls for one so the primary store isn't the only copy of the data."
            )
        return warnings
