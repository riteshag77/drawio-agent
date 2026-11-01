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
`phase_ingest`/`phase_process`/`phase_store` a leaf belongs to. The *trigger*
for the required DLQ/backup/replica/schema-registry companions (SaaS + a
primary store both present) is promoted to ``semantic_warnings()`` below;
which specific leaf to add and how to label it stays the model's judgment,
in that prompt pack.
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
#
# Networking-plumbing tokens (vpc/subnet/nat/gateway/cloudfront/alb/route53)
# mirror the identical extension LogicalIR's own denylist got after an audit
# found logical.txt's exclude list already claimed these get stripped as
# noise when they didn't — data_flow.txt makes the identical claim ("Network
# plumbing... never draw them here"), so it inherited the same unmet promise
# until now. One vocabulary, kept in sync with logical.py's list by hand
# since the two views' denylists aren't otherwise shared code.
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

    See ``LogicalIR._strip_infra_noise_from_actors`` — identical shape and
    identical reasoning (Data-flow, like Logical, has no fixed "perimeter
    service" leaf slot the way Network's ``edge_layer`` is, so a misfiled
    infra actor is dropped outright rather than relocated).
    """
    if not isinstance(data, dict):
        return data
    actors = data.get("external_actors")
    if not isinstance(actors, list) or not actors:
        return data

    def _is_noise(actor: object) -> bool:
        if not isinstance(actor, dict):
            return False
        needle = (actor.get("shape_type") or actor.get("label") or "").lower().replace(" ", "_")
        return any(token in needle for token in _INFRA_NOISE_DENYLIST)

    kept = [a for a in actors if not _is_noise(a)]
    if len(kept) == len(actors):
        return data
    data = dict(data)
    data["external_actors"] = kept
    return data


class DataFlowIR(CorridorIR):
    view: Literal["data_flow"] = Field(
        default="data_flow", description="Must be exactly the lowercase string 'data_flow' — no other casing."
    )
    _infra_noise_denylist: ClassVar[frozenset[str]] = _INFRA_NOISE_DENYLIST

    # Overview-panel metadata — same reasoning as SecurityIR's own block:
    # declared after the structural fields, all optional, rendered by the
    # compiler from these named fields in a fixed order (see
    # compiler/corridor.py) rather than a flat LLM-authored list.
    data_classification: str = Field(
        default="",
        description=(
            "Types of sensitive data flowing through (PII, PHI, PCI, Financial, Actuarial), "
            "only when the pipeline genuinely carries regulated data. Leave empty if none — "
            "never write 'None', 'N/A' or 'Not applicable' as a value."
        ),
    )
    compliance_frameworks: str = Field(
        default="",
        description=(
            "Applicable regulations (NAIC, HIPAA, SOC2, GDPR, Solvency II), only when one "
            "genuinely applies. Leave empty if none — never write 'None', 'N/A' or 'Not "
            "applicable' as a value."
        ),
    )
    trust_zones_referenced: str = Field(
        default="",
        description=(
            "Network/trust tiers this data conceptually passes through even though Data-flow "
            "doesn't draw zone boxes itself (public, DMZ, internal, restricted)."
        ),
    )
    key_assumptions: str = Field(
        default="",
        description='Anything assumed but not visually depicted (e.g. "mTLS assumed between all internal services").',
    )

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
        data = cls._strip_cross_cutting_infra(data)
        return _strip_infra_noise_from_actors(data)

    @model_validator(mode="before")
    @classmethod
    def _relocate_misplaced_phases(cls, data: object) -> object:
        """Row/shelf membership is fixed vocabulary — fix mis-slotting in code
        (see SecurityIR._relocate_misplaced_zones for the observed failure).
        Data-flow is the likeliest to trip this: ``phase_consume`` reads like a
        fourth journey phase but is defined as a shelf under ``phase_store``."""
        return cls.sanitize_raw(data)

    #: Flow ``classification`` -> how it reads in the Overview panel. Ordered
    #: most-sensitive first, which is the order they are listed in.
    _CLASSIFICATION_LABELS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("pci", "PCI"),
        ("pii", "PII"),
        ("confidential", "Confidential"),
        ("internal", "Internal"),
    )

    @model_validator(mode="after")
    def _derive_data_classification(self) -> "DataFlowIR":
        """Summarise the panel's data classification from the flows themselves.

        The model marks each flow's ``classification`` as it draws it, then
        routinely leaves this summary field blank — it has a default, so it is
        never in the schema's required set and gets skipped. The result is a
        diagram whose edges are labelled PII above an Overview panel that says
        nothing about data sensitivity: the fact is already on the page, just
        not in the summary of it. Across the stored corpus that was 63 of the
        73 Data-flow views with this field empty, 56 of them carrying PII.

        Derived rather than asked for, like the sequence view's activation bars
        and ArchiMate's confidence counts: the model already stated it, so a
        second LLM round-trip could only reword what is in hand — and the
        warning-correction pass that used to spend that call demonstrably did
        not fill this in.

        Only ever fills a blank. A model that wrote its own value keeps it,
        including vocabulary the flows cannot express (PHI, Actuarial).
        """
        if self.data_classification.strip():
            return self
        present = {flow.classification for flow in self.flows}
        labels = [label for key, label in self._CLASSIFICATION_LABELS if key in present]
        if labels:
            self.data_classification = ", ".join(labels)
        return self

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
                problem = self.shelf_anchor_error(shelf, "phase_store")
                if problem:
                    raise ValueError(problem)
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
        """Promotes the "required companions" trigger from
        ``config/xml_gen/ir/data_flow.txt`` prose into code — see that
        file's "Required companions" section, which states one trigger for
        all four companions (DLQ, backup/archive, CDC replica, schema
        registry): the app calls external SaaS AND a primary store exists.
        Previously this promoted two different, undocumented heuristics
        instead (streaming-keyword-in-ingest for DLQ, phase_store-exists-
        at-all for backup) — the first almost never matched the documented
        case, the second padded every thin diagram with a phase_store
        regardless of SaaS, directly contradicting data_flow.txt's own
        "never density padding" rule. Fixed to match the single documented
        trigger exactly, for all four.

        Also nudges for the overview-panel fields: same lesson learned for
        business_notes (see NetworkIR/LogicalIR) — an optional field with a
        default is never in the schema's "required" set, so a live capture
        confirmed the model can skip it entirely even when the prompt
        describes it. Checks data_classification/compliance_frameworks
        together (the two most broadly-applicable fields) rather than all
        four — trust_zones_referenced/key_assumptions are legitimately blank
        more often, and the prompt already says so.
        """
        warnings: list[str] = []
        if not self.data_classification.strip() and not self.compliance_frameworks.strip() and len(self.leaf_ids()) >= 2:
            warnings.append(
                "data_classification and compliance_frameworks are both empty — fill in "
                "whichever genuinely apply to the data this pipeline handles (see the Overview "
                "panel metadata section of your instructions)"
            )

        saas_producer = self.corridor_by_id("phase_saas_producer")
        saas_consumer = self.corridor_by_id("phase_saas_consumer")
        store = self.corridor_by_id("phase_store")
        has_saas = bool(saas_producer) or bool(saas_consumer)

        if has_saas and store is not None:
            process = self.corridor_by_id("phase_process")
            if not (process is not None and self.leaves_mention(
                process.leaves, "dlq", "dead-letter", "dead letter", "retry", "sqs",
            )):
                warnings.append(
                    "app calls external SaaS and a primary store exists but phase_process has "
                    "no dead-letter/DLQ leaf — the required-companions rule calls for one (see "
                    "the Required companions section of your instructions)"
                )
            if not self.leaves_mention(store.leaves, "snapshot", "archive", "backup"):
                warnings.append(
                    "app calls external SaaS and a primary store exists but phase_store has no "
                    "snapshot/archive/backup leaf — the required-companions rule calls for one"
                )
            if not self.leaves_mention(store.leaves, "replica", "read replica", "cdc"):
                warnings.append(
                    "app calls external SaaS and a primary store exists but phase_store has no "
                    "CDC/read-replica leaf — the required-companions rule calls for one"
                )
            control = self.corridor_by_id("phase_control")
            if not (control is not None and self.leaves_mention(
                control.leaves, "schema", "registry", "glue", "catalog",
            )):
                warnings.append(
                    "app calls external SaaS and a primary store exists but phase_control has "
                    "no schema-registry leaf — the required-companions rule calls for one"
                )

        # design_tradeoffs is fully wired (CorridorIR field, rendered as its
        # own frontend card) but this view's prompt never asked for it and
        # no nudge existed — the field's own docstring says Security/
        # Data-flow are where it matters most, yet it was empty on
        # essentially every Data-flow generation. Same threshold/pattern as
        # NetworkIR's own design_tradeoffs nudge.
        if len(self.leaf_ids()) >= 4 and not self.design_tradeoffs:
            warnings.append(
                "design_tradeoffs is empty — name 2-4 genuine data-handling choices in this "
                "pipeline and the specific alternative each one beat (see the Design tradeoffs "
                "section of your instructions)"
            )
        return warnings
