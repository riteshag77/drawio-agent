"""
Diagram validation utilities — port of next-ai-draw-io lib/diagram-validator.ts

Includes structural JSON validation (always available) and feedback formatting for LLM retries.
"""

from __future__ import annotations

from app.domain.entities import (
    ArchitectureDiagram,
    Node,
    ValidationIssue,
    ValidationResult,
)


def format_validation_feedback(result: ValidationResult) -> str:
    """
    Format validation feedback for the LLM retry loop.
    Mirrors next-ai-draw-io formatValidationFeedback().
    """
    if result.valid and not result.issues:
        return ""

    lines: list[str] = [
        "DIAGRAM VALIDATION FAILED",
        "",
    ]

    critical = [i for i in result.issues if i.severity == "critical"]
    warnings = [i for i in result.issues if i.severity == "warning"]

    if critical:
        lines.append("Critical Issues (must fix):")
        for issue in critical:
            lines.append(f"  - [{issue.type}] {issue.description}")
        lines.append("")

    if warnings:
        lines.append("Warnings:")
        for issue in warnings:
            lines.append(f"  - [{issue.type}] {issue.description}")
        lines.append("")

    if result.suggestions:
        lines.append("Suggestions to fix:")
        for suggestion in result.suggestions:
            lines.append(f"  - {suggestion}")
        lines.append("")

    lines.append(
        "Please regenerate the architecture JSON with corrected layout, spacing, and edge topology "
        "to fix these issues. Return the COMPLETE updated JSON."
    )
    return "\n".join(lines)


def _node_by_id(nodes: list[Node]) -> dict[str, Node]:
    return {n.id: n for n in nodes}


def _parent_cycle_ids(node_map: dict[str, Node]) -> set[str]:
    """Return node ids that participate in a parent_id cycle (DFS colour-state)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in node_map}
    cyclic: set[str] = set()

    def visit(nid: str) -> None:
        color[nid] = GRAY
        node = node_map[nid]
        parent = node.parent_id
        if parent and parent in node_map:
            if color[parent] == GRAY:
                cyclic.add(nid)
                cyclic.add(parent)
            elif color[parent] == WHITE:
                visit(parent)
                if parent in cyclic:
                    cyclic.add(nid)
        color[nid] = BLACK

    for nid in node_map:
        if color[nid] == WHITE:
            visit(nid)
    return cyclic


def _hierarchy_depth(node_id: str, node_map: dict[str, Node]) -> int:
    depth = 0
    seen: set[str] = set()
    current = node_map.get(node_id)
    while current and current.parent_id and current.parent_id in node_map:
        if current.parent_id in seen:
            return depth  # cycle — structural check reports separately
        seen.add(current.parent_id)
        depth += 1
        current = node_map[current.parent_id]
    return depth


def validate_architecture_structure(diagram: ArchitectureDiagram) -> ValidationResult:
    """
    Deterministic structural validation on ArchitectureDiagram JSON.
    Catches topology errors before layout/XML generation.
    """
    issues: list[ValidationIssue] = []
    suggestions: list[str] = []
    nodes = diagram.nodes
    edges = diagram.edges
    node_map = _node_by_id(nodes)
    node_ids = set(node_map)

    if not nodes:
        issues.append(
            ValidationIssue(
                type="rendering",
                severity="critical",
                description="Diagram has no nodes.",
            )
        )
        return ValidationResult(valid=False, issues=issues, suggestions=["Add at least one infrastructure node."])

    seen_ids: set[str] = set()
    for node in nodes:
        if node.id in seen_ids:
            issues.append(
                ValidationIssue(
                    type="rendering",
                    severity="critical",
                    description=f"Duplicate node id: '{node.id}'",
                )
            )
        seen_ids.add(node.id)

        if node.parent_id is not None and node.parent_id not in node_ids:
            issues.append(
                ValidationIssue(
                    type="rendering",
                    severity="critical",
                    description=f"Node '{node.id}' references missing parent '{node.parent_id}'",
                )
            )

    for cid in sorted(_parent_cycle_ids(node_map)):
        issues.append(
            ValidationIssue(
                type="rendering",
                severity="critical",
                description=f"Node '{cid}' is part of a parent_id cycle",
            )
        )
        suggestions.append("Remove parent_id cycles so every node has an acyclic ancestor chain.")

    containers = {n.id for n in nodes if n.is_container}
    for node in nodes:
        if not node.is_container and node.parent_id and node.parent_id not in containers:
            issues.append(
                ValidationIssue(
                    type="layout",
                    severity="warning",
                    description=f"Node '{node.label}' parent '{node.parent_id}' is not marked as a container",
                )
            )

    for node in nodes:
        if node.x is not None or node.y is not None:
            issues.append(
                ValidationIssue(
                    type="layout",
                    severity="warning",
                    description=f"Node '{node.label}' has x/y coordinates — remove them; layout engine handles placement",
                )
            )
        if node.width is not None or node.height is not None:
            if not node.is_container:
                issues.append(
                    ValidationIssue(
                        type="layout",
                        severity="warning",
                        description=f"Leaf node '{node.label}' has width/height — remove them; layout engine sizes icons",
                    )
                )

    max_depth = max((_hierarchy_depth(n.id, node_map) for n in nodes), default=0)
    # AWS: cloud → region → vpc → az → subnet → service can reach depth 5
    if max_depth > 6:
        issues.append(
            ValidationIssue(
                type="layout",
                severity="critical",
                description=f"Container hierarchy too deep (depth {max_depth}, max 6)",
            )
        )
        suggestions.append(
            "Flatten nesting: cloud → region → vpc → az → subnet → service (max 6 levels)."
        )
    elif max_depth > 5:
        issues.append(
            ValidationIssue(
                type="layout",
                severity="warning",
                description=f"Container hierarchy depth {max_depth} is deep — prefer ≤5 container hops",
            )
        )

    for edge in edges:
        if edge.source_id not in node_ids:
            issues.append(
                ValidationIssue(
                    type="rendering",
                    severity="critical",
                    description=f"Edge references missing source node '{edge.source_id}'",
                )
            )
        if edge.target_id not in node_ids:
            issues.append(
                ValidationIssue(
                    type="rendering",
                    severity="critical",
                    description=f"Edge references missing target node '{edge.target_id}'",
                )
            )

    outgoing: dict[str, int] = {}
    solid_outgoing: dict[str, int] = {}
    for edge in edges:
        if edge.source_id in node_ids and edge.target_id in node_ids:
            outgoing[edge.source_id] = outgoing.get(edge.source_id, 0) + 1
            if (edge.style or "solid") == "solid":
                solid_outgoing[edge.source_id] = solid_outgoing.get(edge.source_id, 0) + 1

    for node_id, count in solid_outgoing.items():
        node = node_map.get(node_id)
        label = node.label if node else node_id
        is_lb = node and any(
            k in (node.shape_type or "").lower()
            for k in ("alb", "nlb", "elb", "load_bal", "gateway", "apigateway")
        )
        limit = 4 if is_lb else 3
        if count > limit + 1:
            issues.append(
                ValidationIssue(
                    type="edge_routing",
                    severity="critical",
                    description=f"Node '{label}' has {count} outgoing solid edges (max {limit + 1})",
                )
            )
            suggestions.append(f"Reduce solid edges from '{label}' or merge flows via an intermediate service.")
        elif count > limit:
            issues.append(
                ValidationIssue(
                    type="edge_routing",
                    severity="warning",
                    description=f"Node '{label}' has {count} outgoing solid edges (target ≤{limit})",
                )
            )

    step_nodes = [(n.step_number, n) for n in nodes if n.step_number is not None]
    if step_nodes:
        steps = sorted(s for s, _ in step_nodes)
        if steps[0] != 1:
            issues.append(
                ValidationIssue(
                    type="layout",
                    severity="warning",
                    description=f"step_number should start at 1, got {steps[0]}",
                )
            )
        for i in range(1, len(steps)):
            if steps[i] - steps[i - 1] > 1:
                issues.append(
                    ValidationIssue(
                        type="layout",
                        severity="warning",
                        description=f"Gap in step_number sequence: {steps[i - 1]} → {steps[i]}",
                    )
                )

    if len(nodes) > 18:
        issues.append(
            ValidationIssue(
                type="layout",
                severity="warning",
                description=f"Diagram has {len(nodes)} nodes — may appear cramped (target ≤15)",
            )
        )
        suggestions.append("Group related services into containers or remove non-essential components.")

    critical_count = sum(1 for i in issues if i.severity == "critical")
    return ValidationResult(
        valid=critical_count == 0,
        issues=issues,
        suggestions=suggestions,
    )
