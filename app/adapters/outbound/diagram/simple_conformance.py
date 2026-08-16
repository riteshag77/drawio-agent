"""Score a ``SimpleDiagramIR`` against the rules its own prompt pack states.

The cloud views have ``view_conformance.score_view``; the non-cloud path had
nothing, so validation could not run on a flowchart, a sequence diagram or a
chart at all.

**Every rule here is one ``config/xml_gen/ir/simple.txt`` already gives**, and
that constraint is the whole design. A conformance score is only worth
iterating against if a *higher* number means the diagram is closer to what was
asked for; a score built from one engineer's taste would send the model
chasing preferences nobody agreed to, which is the mistake the density caps
made before they were removed. So each check below quotes the pack:

- "a node with no edges at all is almost always a mistake"
- "every decision needs its outgoing edges labelled — an unlabelled branch is
  unreadable"
- "labels are 1-4 words. Not sentences."
- "label every message with what is actually sent"
- for `alt`, "each arm has its own [branch]"

Nothing here judges *content* — whether the right services are shown, whether
the process is correct. Those are not measurable from the IR, and pretending
otherwise would produce a number that moves without the diagram improving.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.ir.simple import SimpleDiagramIR

#: What each violation costs, out of 100. Weighted by how badly the defect
#: hurts a reader: an unlabelled branch makes a decision unreadable, a stray
#: node is a loose end, an over-long label is untidy.
_WEIGHTS = {
    "orphan_node": 12,
    "unlabelled_branch": 15,
    "decision_without_branches": 12,
    "long_label": 4,
    "unlabelled_message": 8,
    "orphan_participant": 10,
    "single_arm_alt": 10,
    "single_category": 12,
    "empty_title": 4,
}
#: Words past which a node label has stopped being a label. The pack asks for
#: 1-4; 6 is the point where it is unarguable rather than borderline.
_MAX_LABEL_WORDS = 6


@dataclass
class SimpleViolation:
    code: str
    message: str


@dataclass
class SimpleScore:
    score: int
    passes: bool
    violations: list[SimpleViolation] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def score_simple_ir(ir: SimpleDiagramIR, *, min_score: int = 75) -> SimpleScore:
    """Score ``ir`` 0-100. Deterministic: the same IR always scores the same."""
    violations: list[SimpleViolation] = []

    if not (ir.title or "").strip():
        violations.append(SimpleViolation("empty_title", "the diagram has no title"))

    if ir.kind == "flow":
        violations.extend(_flow_violations(ir))
    elif ir.kind == "sequence":
        violations.extend(_sequence_violations(ir))
    else:
        violations.extend(_chart_violations(ir))

    penalty = sum(_WEIGHTS.get(v.code, 5) for v in violations)
    score = max(0, 100 - penalty)
    return SimpleScore(
        score=score,
        passes=score >= min_score,
        violations=violations,
        metrics={"kind": ir.kind, "min_score": min_score, "violations": len(violations)},
    )


def _flow_violations(ir: SimpleDiagramIR) -> list[SimpleViolation]:
    out: list[SimpleViolation] = []
    touched = {e.source for e in ir.edges} | {e.target for e in ir.edges}
    for node in ir.nodes:
        # A single-node diagram has nothing to connect to; only call a node
        # stranded when there were edges it could have joined.
        if ir.edges and node.id not in touched:
            out.append(SimpleViolation(
                "orphan_node", f"'{node.label or node.id}' is connected to nothing",
            ))
        if len((node.label or "").split()) > _MAX_LABEL_WORDS:
            out.append(SimpleViolation(
                "long_label", f"'{node.label}' is a sentence, not a label",
            ))

    for node in ir.nodes:
        if node.shape != "diamond":
            continue
        outgoing = [e for e in ir.edges if e.source == node.id]
        if len(outgoing) < 2:
            out.append(SimpleViolation(
                "decision_without_branches",
                f"decision '{node.label or node.id}' has {len(outgoing)} way(s) out",
            ))
            continue
        unlabelled = [e for e in outgoing if not (e.label or "").strip()]
        if unlabelled:
            out.append(SimpleViolation(
                "unlabelled_branch",
                f"decision '{node.label or node.id}' has {len(unlabelled)} unlabelled branch(es)",
            ))
    return out


def _sequence_violations(ir: SimpleDiagramIR) -> list[SimpleViolation]:
    out: list[SimpleViolation] = []
    touched = {m.source for m in ir.messages} | {m.target for m in ir.messages}
    for participant in ir.participants:
        if participant.id not in touched:
            out.append(SimpleViolation(
                "orphan_participant",
                f"'{participant.label or participant.id}' sends and receives nothing",
            ))
    for index, message in enumerate(ir.messages):
        if not (message.label or "").strip():
            out.append(SimpleViolation(
                "unlabelled_message", f"message {index + 1} says nothing about what is sent",
            ))

    # An `alt` with one arm is not an alternative — it is an `opt` mislabelled,
    # and it tells a reader there is a choice where there is none.
    arms: dict[str, set[str]] = {}
    for message in ir.messages:
        name = (message.fragment or "").strip()
        if name and message.fragment_kind == "alt":
            arms.setdefault(name, set()).add((message.branch or "").strip())
    for name, branches in arms.items():
        if len(branches) < 2:
            out.append(SimpleViolation(
                "single_arm_alt", f"alt block '{name}' has only one branch",
            ))
    return out


def _chart_violations(ir: SimpleDiagramIR) -> list[SimpleViolation]:
    out: list[SimpleViolation] = []
    # Gantt measures tasks over time, so its "categories" are optional column
    # headings rather than the thing being compared.
    if ir.kind != "gantt" and len(ir.categories) < 2:
        out.append(SimpleViolation(
            "single_category",
            f"a {ir.kind} chart of {len(ir.categories)} category compares nothing",
        ))
    return out
