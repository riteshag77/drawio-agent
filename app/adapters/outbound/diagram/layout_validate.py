"""Hard post-layout validation — raises when structural rules are violated."""

from __future__ import annotations

from dataclasses import dataclass

from app.adapters.outbound.diagram.diagram_classify import (
    is_az_container,
    is_band_container,
    is_boundary_gateway,
    is_igw,
    is_subnet_container,
    is_vpc_like,
)
from app.adapters.outbound.diagram.layout import absolute_box
from app.adapters.outbound.diagram.layout_settings import layout_settings
from app.domain.entities import ArchitectureDiagram, Node


class DiagramLayoutError(Exception):
    """Raised when layout validation finds structural defects."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(
            "Diagram layout validation failed:\n" + "\n".join(f"  - {v}" for v in violations)
        )


@dataclass
class LayoutViolation:
    rule: str
    cell_id: str
    message: str


def validate_layout_structure(
    architecture: ArchitectureDiagram,
    layout: dict[str, dict],
    nodes_by_id: dict[str, Node],
    children_map: dict[str, list[str]],
    *,
    page_w: int,
    page_h: int,
) -> list[LayoutViolation]:
    """Return structural violations (empty list = OK)."""
    s = layout_settings()
    violations: list[LayoutViolation] = []

    # 1. Child geometry must fit inside parent (boundary icons: y >= -h/2)
    for parent_id, child_ids in children_map.items():
        parent_node = nodes_by_id.get(parent_id)
        parent_box = layout.get(parent_id)
        if not parent_node or not parent_box:
            continue
        pw, ph = parent_box["w"], parent_box["h"]
        for child_id in child_ids:
            child = layout.get(child_id)
            node = nodes_by_id.get(child_id)
            if not child or not node:
                continue
            x, y, w, h = child["x"], child["y"], child["w"], child["h"]
            if is_boundary_gateway(node) or is_igw(node):
                if y < -h // 2:
                    violations.append(LayoutViolation(
                        "boundary_clamp", child_id,
                        f"{child_id}: y={y} < -h/2={-h // 2}",
                    ))
                continue
            if x + w > pw + 1 or y + h > ph + 1:
                violations.append(LayoutViolation(
                    "child_overflow", child_id,
                    f"{child_id}: ({x}+{w},{y}+{h}) exceeds parent {parent_id} ({pw}x{ph})",
                ))
            if x < -1 or y < -1:
                violations.append(LayoutViolation(
                    "negative_offset", child_id,
                    f"{child_id}: negative offset ({x},{y}) inside {parent_id}",
                ))

    # 2. Sibling container overlap — same-kind only (AZ↔AZ, subnet↔subnet, band↔band)
    def _kind(nid: str) -> str:
        n = nodes_by_id[nid]
        if is_band_container(n):
            return "band"
        if is_az_container(n):
            return "az"
        if is_subnet_container(n):
            return "subnet"
        return "other"

    for parent_id, child_ids in children_map.items():
        containers = [
            cid for cid in child_ids
            if nodes_by_id.get(cid) and nodes_by_id[cid].is_container and not cid.startswith("badge_")
        ]
        for i, a in enumerate(containers):
            if _kind(a) == "other":
                continue
            box_a = layout.get(a)
            if not box_a:
                continue
            ax, ay, aw, ah = box_a["x"], box_a["y"], box_a["w"], box_a["h"]
            for b_id in containers[i + 1:]:
                if _kind(b_id) != _kind(a):
                    continue
                box_b = layout.get(b_id)
                if not box_b:
                    continue
                bx, by, bw, bh = box_b["x"], box_b["y"], box_b["w"], box_b["h"]
                if not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay):
                    violations.append(LayoutViolation(
                        "sibling_overlap", a,
                        f"{a} overlaps sibling {b_id} under {parent_id}",
                    ))

    # 3. Subnets under VPC without AZ when ≥2 AZs exist (or any AZ present)
    for node in nodes_by_id.values():
        if not is_subnet_container(node):
            continue
        parent = nodes_by_id.get(node.parent_id or "")
        if not parent or not is_vpc_like(parent):
            continue
        az_count = sum(
            1 for c in children_map.get(parent.id, [])
            if is_az_container(nodes_by_id[c])
        )
        if az_count >= 1:
            violations.append(LayoutViolation(
                "missing_az", node.id,
                f"{node.id}: subnet is direct child of VPC {parent.id} while AZs exist",
            ))

    # 4. Bands / top-level groups must stay inside declared parent
    for node in nodes_by_id.values():
        if not (is_band_container(node) or is_az_container(node) or is_subnet_container(node)):
            continue
        parent = nodes_by_id.get(node.parent_id or "")
        child = layout.get(node.id)
        parent_box = layout.get(parent.id) if parent else None
        if not child or not parent or not parent_box:
            continue
        if child["x"] + child["w"] > parent_box["w"] + 2 or child["y"] + child["h"] > parent_box["h"] + 2:
            violations.append(LayoutViolation(
                "group_overflow", node.id,
                f"{node.id}: extends beyond parent {parent.id}",
            ))

    # 5. Docked bands must sit near the VPC (not float across the canvas).
    #    Measure band↔VPC box gap — not leaf centers (left-AZ services are far by design).
    canvas_w = max(page_w, 1)
    canvas_h = max(page_h, 1)
    threshold = s.edge_distance_ratio
    vpc_id = next((nid for nid, n in nodes_by_id.items() if is_vpc_like(n)), None)
    vpc_box = absolute_box(vpc_id, layout, nodes_by_id) if vpc_id and vpc_id in layout else None

    def _box_gap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        dx = max(0, bx - (ax + aw), ax - (bx + bw))
        dy = max(0, by - (ay + ah), ay - (by + bh))
        return float((dx * dx + dy * dy) ** 0.5)

    if vpc_box:
        for nid, node in nodes_by_id.items():
            if not is_band_container(node) or nid not in layout:
                continue
            band_box = absolute_box(nid, layout, nodes_by_id)
            gap = _box_gap(band_box, vpc_box)
            limit = threshold * max(canvas_w, canvas_h)
            if gap > limit:
                violations.append(LayoutViolation(
                    "edge_distance", nid,
                    f"{nid}: too far from VPC (gap={gap:.0f} > {limit:.0f})",
                ))

    return violations


def assert_layout_valid(
    architecture: ArchitectureDiagram,
    layout: dict[str, dict],
    nodes_by_id: dict[str, Node],
    children_map: dict[str, list[str]],
    *,
    page_w: int,
    page_h: int,
) -> None:
    violations = validate_layout_structure(
        architecture, layout, nodes_by_id, children_map, page_w=page_w, page_h=page_h,
    )
    if violations:
        raise DiagramLayoutError([f"[{v.rule}] {v.message}" for v in violations])
