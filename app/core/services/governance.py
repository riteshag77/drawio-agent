"""Deterministic org-policy (governance) conformance checks against a
generated IR.

v1 is soft-only by design (see the project's own architectural
self-assessment): every failed finding here feeds the *same* one-shot
corrective pass ``agent._apply_semantic_warning_pass`` already runs for
``business_notes``/``design_tradeoffs``/structural warnings — never a second
LLM round-trip, never a hard block. A malformed org rule degrades to "one
diagram doesn't get nudged," not "generation breaks org-wide," which matters
for a brand-new, human-authored rule schema nobody has battle-tested yet.
Promoting a specific rule to a hard gate later (folding it into
``semantic_errors()`` instead) is a small, targeted change once a rule has
survived real use — not a rearchitecture of this module.

Every check is a pure structural fact about the IR (a corridor id present, a
field non-empty, a flow's protection level) — no LLM judgment decides
pass/fail. The same IR always produces the same findings for the same rule
set: that reproducibility is the entire point of a "conformance report" a
reviewer can trust, as opposed to asking a model "does this look compliant?"
and getting a different answer each time.
"""

from __future__ import annotations

import re

from app.domain.entities.governance import GovernanceFinding, GovernanceRule

#: Levels a flow's `protection` field can compare against, low to high.
_PROTECTION_LEVELS = {"none": 0, "tls": 1, "mtls": 2}


def _all_leaves(ir) -> list:
    """Every leaf regardless of IR shape — Network nests them in a
    cloud/region/vpc/zone/subnet hierarchy (``iter_leaves()``), corridor views
    (Logical/Security/Data-flow) keep a flat corridor/leaves list
    (``all_corridors()``), ArchiMate keeps a flat ``elements`` list. Neither
    shape is reachable from the others, so this is the one place that needs
    to know all three.

    ArchimateElement has no ``shape_type`` (icons resolve from
    ``ARCHIMATE_ELEMENT_TABLE``, not a leaf-authored key) and names itself
    via ``.name`` rather than ``.label`` — callers below read both through
    ``getattr`` with a fallback rather than assuming the corridor/leaf shape,
    so a rule like ``forbidden_label_pattern`` (``view: "any"``) still
    actually runs against ArchiMate instead of silently seeing no leaves.
    """
    if hasattr(ir, "iter_leaves"):
        return list(ir.iter_leaves())
    if hasattr(ir, "all_corridors"):
        return [leaf for corridor in ir.all_corridors() for leaf in corridor.leaves]
    if hasattr(ir, "elements"):
        return list(ir.elements)
    return []


def _leaf_label(leaf) -> str:
    """``leaf.label`` for corridor/Network leaves, ``leaf.name`` for
    ArchimateElement — see ``_all_leaves``."""
    return getattr(leaf, "label", None) or getattr(leaf, "name", "") or ""


def _corridor_ids(ir) -> set[str]:
    corridors = getattr(ir, "corridors", None)
    anchored = getattr(ir, "anchored", None)
    if corridors is None and anchored is None:
        return set()
    return {c.id for c in (corridors or [])} | {c.id for c in (anchored or [])}


def _leaf_corridor_ids(ir) -> dict[str, str]:
    """Maps leaf id -> the id of the corridor/zone that contains it.

    Only corridor-shaped IRs (Logical/Security/Data-flow) have a notion of
    "which zone is this leaf in" — used by ``required_flow_protection`` to
    check whether a flow's *endpoint* sits in a boundary zone. Network has no
    such grouping, so this is empty there (that rule still works on Network
    via the classification check, just never via boundary-crossing).
    """
    all_corridors = getattr(ir, "all_corridors", None)
    if not callable(all_corridors):
        return {}
    return {leaf.id: corridor.id for corridor in all_corridors() for leaf in corridor.leaves}


def evaluate_governance_rules(ir, rules: list[GovernanceRule]) -> list[GovernanceFinding]:
    """Run every rule scoped to ``ir``'s view/platform and return one finding
    per rule (pass and fail both included, so the conformance report can show
    a complete checklist, not just the failures)."""
    view = getattr(ir, "view", "") or ""
    platform = (getattr(ir, "platform", "") or "").strip().lower()
    findings: list[GovernanceFinding] = []
    for rule in rules:
        if not rule.enabled:
            continue
        if rule.view != "any" and rule.view != view:
            continue
        if rule.platform != "any" and rule.platform != platform:
            continue
        passed, detail = _check_rule(ir, rule)
        findings.append(GovernanceFinding(
            rule_id=rule.id, view=view, kind=rule.kind,
            message=rule.message, passed=passed, detail=detail,
        ))
    return findings


def findings_to_warnings(findings: list[GovernanceFinding]) -> list[str]:
    """Failed findings, worded for the corrective-pass prompt — reuses the
    exact same "one non-blocking ask" agent.py already sends for
    business_notes/design_tradeoffs, just with org-authored rule text mixed
    into the same list of sentences."""
    return [f"{f.message} ({f.detail})" if f.detail else f.message for f in findings if not f.passed]


def _check_rule(ir, rule: GovernanceRule) -> tuple[bool, str]:
    kind = rule.kind

    if kind == "required_corridor":
        ids = _corridor_ids(ir)
        ok = not ids or rule.corridor_id in ids  # vacuously true for shapes with no corridors (Network)
        return ok, "" if ok else f"corridor {rule.corridor_id!r} is not present in this diagram"

    if kind == "forbidden_corridor":
        ids = _corridor_ids(ir)
        ok = rule.corridor_id not in ids
        return ok, "" if ok else f"forbidden corridor {rule.corridor_id!r} is present"

    if kind == "required_leaf_shape":
        needle = (rule.shape_type or "").lower()
        shapes = [s for leaf in _all_leaves(ir) if (s := getattr(leaf, "shape_type", None))]
        ok = any(needle in s.lower() for s in shapes)
        return ok, "" if ok else f"no leaf with a shape_type containing {rule.shape_type!r}"

    if kind == "forbidden_shape_type":
        needle = (rule.shape_type or "").lower()
        matches = [
            _leaf_label(leaf) for leaf in _all_leaves(ir)
            if (s := getattr(leaf, "shape_type", None)) and needle in s.lower()
        ]
        ok = not matches
        return ok, "" if ok else f"forbidden shape used by: {', '.join(matches)}"

    if kind == "required_overview_field":
        field = rule.overview_field or ""
        value = getattr(ir, field, None)
        ok = bool(value)
        return ok, "" if ok else f"{field} is empty"

    if kind == "min_narrative_bullets":
        field = rule.overview_field or "business_notes"
        value = getattr(ir, field, None) or []
        count = len(value) if isinstance(value, list) else 0
        ok = count >= rule.min_count
        return ok, "" if ok else f"{field} has {count} bullet(s), policy requires at least {rule.min_count}"

    if kind == "required_flow_protection":
        # rule.boundary_only silently has no effect on any IR type other than
        # SecurityIR — it's the only one that defines _BOUNDARY_ZONE_IDS (its
        # trust-tier zone vocabulary has no equivalent on Logical/Data-flow/
        # Network/ArchiMate). A rule with boundary_only=True scoped to one of
        # those views still evaluates — it just falls back to the
        # classification-only check below, with no error and no signal that
        # half its configuration was ignored. Currently-seeded rules are all
        # configured correctly (boundary_only=True only on the security-
        # scoped rule) so this isn't live-firing today, but the admin UI's
        # checkbox defaults to checked with no view-conditional warning — see
        # its label in admin.html — so a rule authored for another view
        # without deliberately unchecking it would hit exactly this. Locked
        # in as accepted, documented behavior (not a crash, not a silent
        # false pass) rather than a wider architecture change to give every
        # IR type a boundary-zone concept it doesn't otherwise need — see
        # tests/test_governance.py::BoundaryOnlyScopeTests.
        boundary_ids = getattr(ir, "_BOUNDARY_ZONE_IDS", frozenset())
        leaf_zone = _leaf_corridor_ids(ir)
        needed = _PROTECTION_LEVELS.get(rule.min_protection, 1)
        offenders = []
        for flow in getattr(ir, "flows", []):
            classified = flow.classification in ("pii", "pci")
            crosses_boundary = rule.boundary_only and (
                leaf_zone.get(flow.source) in boundary_ids or leaf_zone.get(flow.target) in boundary_ids
            )
            if not (classified or crosses_boundary):
                continue
            have = _PROTECTION_LEVELS.get(flow.protection, 0)
            if have < needed:
                offenders.append(f"{flow.source}->{flow.target}")
        ok = not offenders
        return ok, "" if ok else f"under-protected flow(s) below {rule.min_protection}: {', '.join(offenders)}"

    if kind == "forbidden_label_pattern":
        try:
            pattern = re.compile(rule.label_pattern or "", re.IGNORECASE)
        except re.error:
            return True, ""  # an unparseable admin-authored pattern must never break generation
        matches = [lab for leaf in _all_leaves(ir) if (lab := _leaf_label(leaf)) and pattern.search(lab)]
        ok = not matches
        return ok, "" if ok else f"label matches forbidden pattern {rule.label_pattern!r}: {', '.join(matches)}"

    return True, ""
