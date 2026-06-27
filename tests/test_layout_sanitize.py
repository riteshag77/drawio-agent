"""Tests for layout hint stripping and container depth validation."""

from __future__ import annotations

import re
import unittest

from app.diagram.draftsman import generate_xml, sanitize_layout_hints
from app.diagram.xml_tools import auto_fix_xml, validate_and_fix_xml
from app.models import ArchitectureDiagram, Edge, Node
from app.validation.structural import validate_architecture_structure


def _max_x_from_xml(xml: str) -> int:
    xs = [int(m) for m in re.findall(r'<mxGeometry[^>]*\bx="(\d+)"', xml)]
    widths = [int(m) for m in re.findall(r'<mxGeometry[^>]*\bwidth="(\d+)"', xml)]
    if not xs:
        return 0
    return max(x + (w if w else 0) for x, w in zip(xs, widths, strict=False))


class LayoutSanitizeTests(unittest.TestCase):
    def test_sanitize_strips_all_layout_hints(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(
                    id="a",
                    label="A",
                    shape_type="generic_server",
                    x=5000,
                    y=4000,
                    width=200,
                    height=200,
                ),
            ],
            edges=[],
            suggestions=[],
        )
        cleaned = sanitize_layout_hints(arch)
        node = cleaned.nodes[0]
        self.assertIsNone(node.x)
        self.assertIsNone(node.y)
        self.assertIsNone(node.width)
        self.assertIsNone(node.height)

    def test_far_llm_hints_produce_compact_canvas(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(
                    id="a",
                    label="Web",
                    shape_type="generic_server",
                    step_number=1,
                    x=5000,
                    y=5000,
                ),
                Node(
                    id="b",
                    label="DB",
                    shape_type="generic_database",
                    step_number=2,
                    x=9000,
                    y=5000,
                ),
            ],
            edges=[Edge(source_id="a", target_id="b")],
            suggestions=[],
        )
        xml = generate_xml(arch, platform="aws")
        self.assertLess(_max_x_from_xml(xml), 1200)

    def test_container_depth_over_five_is_critical(self) -> None:
        nodes = [
            Node(id="l0", label="Cloud", shape_type="aws_cloud", is_container=True),
            Node(id="l1", label="VPC", shape_type="aws_vpc", is_container=True, parent_id="l0"),
            Node(id="l2", label="AZ", shape_type="aws_az", is_container=True, parent_id="l1"),
            Node(id="l3", label="Subnet A", shape_type="aws_subnet", is_container=True, parent_id="l2"),
            Node(id="l4", label="Subnet B", shape_type="aws_subnet", is_container=True, parent_id="l3"),
            Node(id="l5", label="Tier", shape_type="aws_subnet", is_container=True, parent_id="l4"),
            Node(id="svc", label="EC2", shape_type="aws_ec2", parent_id="l5"),
        ]
        arch = ArchitectureDiagram(nodes=nodes, edges=[], suggestions=[])
        result = validate_architecture_structure(arch)
        self.assertFalse(result.valid)
        critical = [i for i in result.issues if i.severity == "critical"]
        self.assertTrue(any("too deep" in i.description.lower() for i in critical))

    def test_coordinates_on_nodes_emit_warning(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="a", label="A", shape_type="generic_server", x=100, y=50),
            ],
            edges=[],
            suggestions=[],
        )
        result = validate_architecture_structure(arch)
        warnings = [i for i in result.issues if i.severity == "warning"]
        self.assertTrue(any("x/y coordinates" in i.description for i in warnings))

    def test_three_tier_scaffold_applies_container_sizes(self) -> None:
        arch = ArchitectureDiagram(
            pattern_id="three_tier_web",
            nodes=[
                Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True, parent_id="cloud"),
                Node(
                    id="pub",
                    label="Public Subnet",
                    shape_type="aws_subnet",
                    is_container=True,
                    parent_id="vpc",
                ),
            ],
            edges=[],
            suggestions=[],
        )
        xml = generate_xml(arch, platform="aws")
        self.assertIn('parent="vpc"', xml)

    def test_xml_hardening_adds_missing_geometry(self) -> None:
        broken = (
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n1" value="Svc" vertex="1" parent="1"/>'
        )
        fixed, fixes = auto_fix_xml(broken)
        self.assertTrue(any("mxGeometry" in f for f in fixes))
        self.assertIn('as="geometry"', fixed)

    def test_xml_hardening_reorders_parent_before_child(self) -> None:
        out_of_order = (
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="child" value="Child" vertex="1" parent="parent">'
            '<mxGeometry x="10" y="10" width="60" height="60" as="geometry"/></mxCell>'
            '<mxCell id="parent" value="Parent" vertex="1" parent="1" style="swimlane;">'
            '<mxGeometry x="0" y="0" width="200" height="120" as="geometry"/></mxCell>'
        )
        result = validate_and_fix_xml(out_of_order)
        self.assertTrue(result.valid or result.fixes)
        output = result.fixed or out_of_order
        parent_idx = output.find('id="parent"')
        child_idx = output.find('id="child"')
        self.assertLess(parent_idx, child_idx)


if __name__ == "__main__":
    unittest.main()
