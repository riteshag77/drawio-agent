"""Org governance/house-style policy — deterministic, soft-only (v1) rules
checked against a generated IR.

Each rule is a bounded, structured fact-check (a corridor id present, a field
non-empty, a flow's protection level) — never free-form prose, and never an
LLM judgment call. The same IR always produces the same finding for the same
rule; there is no "ask the model whether this passes" anywhere in this file
or in ``core/services/governance.py``'s evaluator. That's deliberate: a
governance report a reviewer can't trust to be reproducible is worse than no
report at all.

v1 is soft-only by design (see the project's own architectural
self-assessment) — every finding here feeds the same one-shot corrective
pass ``semantic_warnings()`` already uses (never blocks or regenerates a
request). Promoting a specific rule to a hard gate later is a small, targeted
follow-up once the rule-authoring UX has survived real use, not a
rearchitecture — see ``core/services/governance.py``'s docstring.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

GovernanceRuleKind = Literal[
    "required_corridor",
    "forbidden_corridor",
    "required_leaf_shape",
    "forbidden_shape_type",
    "required_overview_field",
    "min_narrative_bullets",
    "required_flow_protection",
    "forbidden_label_pattern",
    # Relational kinds. Every kind above asks whether something *exists*; most
    # real house rules are about where a thing sits and what may reach it —
    # "the ledger lives in the restricted zone", "nothing in public talks
    # straight to data". Those were unexpressible, so they were enforced by
    # review or not at all.
    "leaf_shape_in_corridor",
    "forbidden_corridor_flow",
]

#: "any" applies the rule to every view/platform; a specific value scopes it.
GovernanceView = Literal["any", "network", "logical", "security", "data_flow", "archimate"]
GovernancePlatform = Literal["any", "aws", "gcp", "azure", "multi"]

#: Overview/narrative field names a rule can point at — one per IR type's own
#: fields (see NetworkIR/CorridorIR/SecurityIR/DataFlowIR). Kept as a fixed
#: list (not free text) so a typo'd field name is rejected at rule-creation
#: time in the admin UI, not silently no-op forever at evaluation time.
GovernanceOverviewField = Literal[
    "business_notes", "design_tradeoffs", "purpose", "scope_included",
    "scope_excluded", "threat_model_reference", "data_classification",
    "compliance_frameworks", "control_types", "key_assumptions",
    "trust_zones_referenced",
    # ArchiMate's own overview fields (see domain/ir/archimate.py::ArchimateIR).
    "panel_summary", "viewpoint_rationale", "layers_excluded_reason",
    "walkthrough", "compliance_notes", "coaching_note",
]


class GovernanceRule(BaseModel):
    """One house-style rule. Only the params relevant to ``kind`` are set —
    the rest stay at their default. See ``core/services/governance.py``'s
    ``_check_rule`` for exactly what each kind checks."""

    id: str = ""
    kind: GovernanceRuleKind
    view: GovernanceView = "any"
    platform: GovernancePlatform = "any"
    enabled: bool = True
    message: str = Field(
        max_length=300,
        description=(
            "Shown to the user (and fed to the corrective-pass prompt) when this rule "
            "fails — must name the specific requirement, not a generic label. "
            "E.g. 'Every Security diagram must include a detection/monitoring zone "
            "(zone_detect).', not 'Missing required element.'"
        ),
    )
    # kind-specific — set only the ones the chosen kind uses.
    corridor_id: str | None = Field(default=None, max_length=80)
    shape_type: str | None = Field(default=None, max_length=120)
    overview_field: GovernanceOverviewField | None = None
    min_count: int = Field(default=1, ge=1, le=20)
    min_protection: Literal["tls", "mtls"] = "tls"
    boundary_only: bool = True
    label_pattern: str | None = Field(default=None, max_length=200)
    #: The far end of a relational rule. On ``leaf_shape_in_corridor`` this is
    #: unused (``corridor_id`` is where the shape must live); on
    #: ``forbidden_corridor_flow`` it is the corridor a flow may not reach from
    #: ``corridor_id``. Named for the relation rather than for one rule so a
    #: third relational kind does not need a third field.
    target_corridor_id: str | None = Field(default=None, max_length=80)
    created_at: str = ""
    updated_at: str = ""


class GovernanceFinding(BaseModel):
    """One rule's result against one generated IR — the unit the conformance
    report (and the "Governance" insights tab) is built from."""

    rule_id: str
    view: str
    kind: GovernanceRuleKind
    message: str
    passed: bool
    detail: str = Field(default="", description="The specific fact that failed, e.g. the offending leaf/flow — never vague.")


class GovernanceReport(BaseModel):
    findings: list[GovernanceFinding] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(f.passed for f in self.findings)

    @property
    def failed_count(self) -> int:
        return sum(1 for f in self.findings if not f.passed)
