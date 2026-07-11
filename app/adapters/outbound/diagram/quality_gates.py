"""Quality gates A–H for diagram generation (build-blocking when enabled)."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.adapters.outbound.diagram.diagram_classify import (
    ATTACHED_LABELS,
    band_for_node,
    is_attached_control,
    is_igw,
    shape_suffix,
)
from app.adapters.outbound.diagram.layout import absolute_box
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.domain.entities import ArchitectureDiagram, Node


@dataclass
class GateResult:
    gate: str
    passed: bool
    message: str
    fixes: list[str] = field(default_factory=list)


def _boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int], *, pad: int = 0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + pad <= bx
        or bx + bw + pad <= ax
        or ay + ah + pad <= by
        or by + bh + pad <= ay
    )


def gate_a_containment(
    layout: dict[str, dict],
    nodes_by_id: dict[str, Node],
    children_map: dict[str, list[str]],
) -> GateResult:
    s = layout_settings()
    violations: list[str] = []
    for node_id, node in nodes_by_id.items():
        if node_id.startswith("band_") or node.is_container:
            continue
        nl = layout.get(node_id, {})
        x, y = nl.get("x", 0), nl.get("y", 0)
        parent = nodes_by_id.get(node.parent_id or "")
        if x == 0 and y == 0 and parent and not is_igw(node) and not parent.id.startswith("band_"):
            violations.append(f"{node_id} at (0,0) inside {node.parent_id}")
        if y >= 0 and y < s.label_band_height and parent and parent.is_container and not is_igw(node):
            if not parent.id.startswith("band_"):
                violations.append(f"{node_id} in label band (y={y})")
    return GateResult("A", not violations, "; ".join(violations) or "containment OK", violations)


def gate_b_overlap(
    layout: dict[str, dict],
    nodes_by_id: dict[str, Node],
) -> GateResult:
    from app.adapters.outbound.diagram.diagram_classify import is_boundary_gateway, is_igw

    boxes: list[tuple[str, tuple[int, int, int, int]]] = []
    for node_id, node in nodes_by_id.items():
        if node.is_container:
            continue
        box = absolute_box(node_id, layout, nodes_by_id)
        boxes.append((node_id, box))

    violations: list[str] = []
    for i, (id_a, box_a) in enumerate(boxes):
        for id_b, box_b in boxes[i + 1:]:
            if id_a.startswith("badge_") or id_b.startswith("badge_"):
                continue
            na, nb = nodes_by_id[id_a], nodes_by_id[id_b]
            # Band icons may share canvas space with VPC-border gateways; not a layout defect
            a_band = bool(na.parent_id and str(na.parent_id).startswith("band_"))
            b_band = bool(nb.parent_id and str(nb.parent_id).startswith("band_"))
            if (a_band and (is_igw(nb) or is_boundary_gateway(nb))) or (
                b_band and (is_igw(na) or is_boundary_gateway(na))
            ):
                continue
            if a_band != b_band:
                # Cross-cutting vs workload absolute overlap is tolerated; docking is validated separately
                continue
            if _boxes_overlap(box_a, box_b):
                violations.append(f"{id_a} overlaps {id_b}")
    return GateResult("B", not violations, "; ".join(violations) or "no overlaps", violations)


def gate_c_attached_vs_inline(architecture: ArchitectureDiagram, nodes_by_id: dict[str, Node]) -> GateResult:
    violations: list[str] = []
    for edge in architecture.edges:
        src = nodes_by_id.get(edge.source_id)
        tgt = nodes_by_id.get(edge.target_id)
        if not src or not tgt:
            continue
        src_att = is_attached_control(src)
        tgt_att = is_attached_control(tgt)
        label = (edge.label or "").lower()
        if edge.style != "dashed" and (src_att or tgt_att) and label not in ATTACHED_LABELS:
            if src_att and tgt_att:
                violations.append(f"inline edge between attached services {edge.source_id}->{edge.target_id}")
            elif src_att or tgt_att:
                mid_att = src_att and not tgt_att
                if mid_att and edge.style == "solid":
                    violations.append(f"attached {edge.source_id} on solid path to {edge.target_id}")
    return GateResult("C", not violations, "; ".join(violations) or "edge classification OK", violations)


def gate_d_narrative_parity(architecture: ArchitectureDiagram, nodes_by_id: dict[str, Node]) -> GateResult:
    numbered = sorted(
        (n for n in architecture.nodes if n.step_number is not None and not n.is_container),
        key=lambda n: n.step_number or 0,
    )
    solid_path_edges = [
        e for e in architecture.edges
        if e.style != "dashed" and (e.label or "").lower() not in ATTACHED_LABELS
    ]
    flow_count = len(architecture.flow_steps) if architecture.flow_steps else len(numbered)
    violations: list[str] = []
    if len(numbered) != flow_count:
        violations.append(f"badges({len(numbered)}) != flow_steps({flow_count})")
    for node in numbered:
        if is_attached_control(node):
            violations.append(f"badge on attached service {node.id}")
    return GateResult("D", not violations, "; ".join(violations) or "narrative parity OK", violations)


def gate_e_multi_az(nodes_by_id: dict[str, Node]) -> GateResult:
    singletons = ("route53", "cloudfront", "api_gateway", "internet_gateway")
    counts: dict[str, int] = {}
    for node in nodes_by_id.values():
        if node.is_container:
            continue
        suffix = shape_suffix(node)
        for key in singletons:
            if key == suffix or key in suffix or key.replace("_", " ") in (node.label or "").lower():
                counts[key] = counts.get(key, 0) + 1
                break
    violations = [f"{k} duplicated {v}x" for k, v in counts.items() if v > 1 and k != "internet_gateway"]
    return GateResult("E", not violations, "; ".join(violations) or "multi-AZ convention OK", violations)


def gate_f_edge_sanity(architecture: ArchitectureDiagram, nodes_by_id: dict[str, Node]) -> GateResult:
    violations: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in architecture.edges:
        if edge.source_id not in nodes_by_id:
            violations.append(f"orphan source {edge.source_id}")
        if edge.target_id not in nodes_by_id:
            violations.append(f"orphan target {edge.target_id}")
        key = (edge.source_id, edge.target_id, (edge.label or "").strip().lower())
        if key in seen:
            violations.append(f"duplicate edge {key}")
        seen.add(key)
    return GateResult("F", not violations, "; ".join(violations) or "edge sanity OK", violations)


def gate_g_page_bounds(page_w: int, page_h: int, max_x: int, max_y: int) -> GateResult:
    s = layout_settings()
    violations: list[str] = []
    if max_x + s.page_margin > page_w:
        violations.append(f"content width {max_x} exceeds page {page_w}")
    if max_y + s.page_margin > page_h:
        violations.append(f"content height {max_y} exceeds page {page_h}")
    return GateResult("G", not violations, "; ".join(violations) or "page bounds OK", violations)


def gate_h_band_placement(nodes_by_id: dict[str, Node]) -> GateResult:
    violations: list[str] = []
    for node in nodes_by_id.values():
        band = band_for_node(node)
        if not band:
            continue
        parent = nodes_by_id.get(node.parent_id or "")
        if not parent or not (parent.id == f"band_{band}"):
            violations.append(f"{node.id} not in band_{band} (parent={node.parent_id})")
    return GateResult("H", not violations, "; ".join(violations) or "band placement OK", violations)


def run_quality_gates(
    architecture: ArchitectureDiagram,
    layout: dict[str, dict],
    nodes_by_id: dict[str, Node],
    children_map: dict[str, list[str]],
    *,
    page_w: int,
    page_h: int,
    max_x: int,
    max_y: int,
) -> list[GateResult]:
    return [
        gate_a_containment(layout, nodes_by_id, children_map),
        gate_b_overlap(layout, nodes_by_id),
        gate_c_attached_vs_inline(architecture, nodes_by_id),
        gate_d_narrative_parity(architecture, nodes_by_id),
        gate_e_multi_az(nodes_by_id),
        gate_f_edge_sanity(architecture, nodes_by_id),
        gate_g_page_bounds(page_w, page_h, max_x, max_y),
        gate_h_band_placement(nodes_by_id),
    ]


def all_gates_passed(results: list[GateResult]) -> bool:
    return all(r.passed for r in results)
