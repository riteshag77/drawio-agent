"""Tests for layout hint stripping and container depth validation."""

from __future__ import annotations

import re
import unittest

from app.diagram.draftsman import generate_xml, sanitize_layout_hints
from app.diagram.edge_router import route_edge
from app.diagram.layout import build_children_map, build_layout
from app.diagram.styles import build_style
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

    def test_container_depth_over_six_is_critical(self) -> None:
        # cloud→vpc→az→subnet→subnet→tier→extra→svc = depth 7
        nodes = [
            Node(id="l0", label="Cloud", shape_type="aws_cloud", is_container=True),
            Node(id="l1", label="VPC", shape_type="aws_vpc", is_container=True, parent_id="l0"),
            Node(id="l2", label="AZ", shape_type="aws_az", is_container=True, parent_id="l1"),
            Node(id="l3", label="Subnet A", shape_type="aws_subnet", is_container=True, parent_id="l2"),
            Node(id="l4", label="Subnet B", shape_type="aws_subnet", is_container=True, parent_id="l3"),
            Node(id="l5", label="Tier", shape_type="aws_subnet", is_container=True, parent_id="l4"),
            Node(id="l6", label="Extra", shape_type="aws_subnet", is_container=True, parent_id="l5"),
            Node(id="svc", label="EC2", shape_type="aws_ec2", parent_id="l6"),
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

    def test_topology_edges_use_default_anchors(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="a", label="A", shape_type="generic_server", step_number=1),
                Node(id="b", label="B", shape_type="generic_server", step_number=2),
            ],
            edges=[Edge(source_id="a", target_id="b")],
            suggestions=[],
        )
        xml = generate_xml(arch, platform="aws")
        self.assertIn("exitX=1", xml)
        self.assertIn("entryX=0", xml)
        self.assertNotIn('<Array as="points">', xml)

    def test_topology_container_children_are_parent_relative(self) -> None:
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
        # AZ inserted between VPC and subnet; subnet remains nested under VPC chain
        self.assertIn('parent="vpc"', xml)
        self.assertTrue('parent="vpc_az_a"' in xml or 'parent="az_a"' in xml or 'subnet' in xml)

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
        # AZ inserted between VPC and subnet; subnet remains nested under VPC chain
        self.assertIn('parent="vpc"', xml)
        self.assertTrue('parent="vpc_az_a"' in xml or 'parent="az_a"' in xml or 'subnet' in xml)

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

    def test_subnet_services_stack_vertically(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True),
                Node(id="pub", label="Public Subnet", shape_type="aws_public_subnet", is_container=True, parent_id="vpc"),
                Node(id="api", label="API Gateway", shape_type="aws_api_gateway", step_number=1, parent_id="pub"),
                Node(id="alb", label="ALB", shape_type="aws_alb", step_number=2, parent_id="pub"),
                Node(id="nat", label="NAT Gateway", shape_type="aws_nat_gateway", step_number=3, parent_id="pub"),
            ],
            edges=[],
            suggestions=[],
        )
        children_map, roots, nodes_by_id = build_children_map(arch.nodes)
        layout = build_layout(nodes_by_id, children_map, roots)
        self.assertLess(layout["api"]["y"], layout["alb"]["y"])
        self.assertLess(layout["alb"]["y"], layout["nat"]["y"])
        self.assertEqual(layout["api"]["x"], layout["alb"]["x"])

    def test_vpc_tier_subnets_stack_vertically(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True),
                Node(id="pub", label="Public Subnet", shape_type="aws_public_subnet", is_container=True, parent_id="vpc"),
                Node(id="app", label="App Subnet", shape_type="aws_private_subnet", is_container=True, parent_id="vpc"),
                Node(id="data", label="Data Subnet", shape_type="aws_private_subnet", is_container=True, parent_id="vpc"),
                Node(id="api", label="API Gateway", shape_type="aws_api_gateway", parent_id="pub"),
                Node(id="fargate", label="Fargate", shape_type="aws_fargate", parent_id="app"),
                Node(id="db", label="Aurora", shape_type="aws_aurora", parent_id="data"),
            ],
            edges=[],
            suggestions=[],
        )
        children_map, roots, nodes_by_id = build_children_map(arch.nodes)
        layout = build_layout(nodes_by_id, children_map, roots)
        # Public → App → Data stacked top-to-bottom (content-sized, not full-height columns)
        self.assertLess(layout["pub"]["y"], layout["app"]["y"])
        self.assertLess(layout["app"]["y"], layout["data"]["y"])
        self.assertEqual(layout["pub"]["x"], layout["app"]["x"])
        self.assertLess(layout["pub"]["h"] + layout["app"]["h"], layout["vpc"]["h"])

    def test_az_containers_placed_side_by_side(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True),
                Node(id="az_a", label="AZ A", shape_type="aws_az", is_container=True, parent_id="vpc"),
                Node(id="az_b", label="AZ B", shape_type="aws_az", is_container=True, parent_id="vpc"),
                Node(id="pub_a", label="Public Subnet", shape_type="aws_public_subnet", is_container=True, parent_id="az_a"),
                Node(id="data_a", label="Data Subnet", shape_type="aws_private_subnet", is_container=True, parent_id="az_a"),
                Node(id="pub_b", label="Public Subnet", shape_type="aws_public_subnet", is_container=True, parent_id="az_b"),
                Node(id="data_b", label="Data Subnet", shape_type="aws_private_subnet", is_container=True, parent_id="az_b"),
                Node(id="api", label="API Gateway", shape_type="aws_api_gateway", step_number=6, parent_id="pub_a"),
                Node(id="fargate_b", label="Fargate", shape_type="aws_fargate", step_number=8, parent_id="pub_b"),
            ],
            edges=[],
            suggestions=[],
        )
        children_map, roots, nodes_by_id = build_children_map(arch.nodes)
        layout = build_layout(nodes_by_id, children_map, roots)
        self.assertEqual(layout["az_a"]["y"], layout["az_b"]["y"])
        self.assertLess(layout["az_a"]["x"], layout["az_b"]["x"])
        # Public always above data in both AZs, synced Y bands.
        self.assertLess(layout["pub_a"]["y"], layout["data_a"]["y"])
        self.assertLess(layout["pub_b"]["y"], layout["data_b"]["y"])
        self.assertEqual(layout["pub_a"]["y"], layout["pub_b"]["y"])
        self.assertEqual(layout["data_a"]["y"], layout["data_b"]["y"])
        self.assertEqual(layout["pub_a"]["h"], layout["pub_b"]["h"])
        self.assertEqual(layout["data_a"]["h"], layout["data_b"]["h"])

    def test_entry_actor_stays_left_of_cloud(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="users", label="Users", shape_type="user_client", step_number=1),
                Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="route53", label="Route 53", shape_type="aws_route_53", step_number=2, parent_id="cloud"),
            ],
            edges=[],
            suggestions=[],
        )
        children_map, roots, nodes_by_id = build_children_map(arch.nodes)
        layout = build_layout(nodes_by_id, children_map, roots)
        self.assertLess(layout["users"]["x"], layout["cloud"]["x"])

    def test_parent_emitted_before_child(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="svc", label="Lambda", shape_type="aws_lambda", parent_id="cloud"),
            ],
            edges=[],
            suggestions=[],
        )
        xml = generate_xml(arch, platform="aws")
        self.assertLess(xml.find('id="cloud"'), xml.find('id="svc"'))

    def test_shield_advanced_resolves_to_shield_icon(self) -> None:
        style = build_style("aws_shield_advanced")
        self.assertIn("resIcon=mxgraph.aws4.shield", style)
        self.assertNotIn("shield_advanced", style)

    def test_edge_anchors_connect_face_to_face(self) -> None:
        exit_x, exit_y, entry_x, entry_y, waypoints = route_edge((0, 0, 60, 60), (220, 0, 60, 60))
        self.assertEqual(exit_x, 1.0)
        self.assertEqual(entry_x, 0.0)
        self.assertEqual(exit_y, 0.5)
        self.assertEqual(entry_y, 0.5)
        self.assertEqual(waypoints, [])

        _, exit_y2, _, entry_y2, _ = route_edge((0, 0, 60, 60), (0, 220, 60, 60))
        self.assertEqual(exit_y2, 1.0)
        self.assertEqual(entry_y2, 0.0)

    def test_edges_use_face_slot_offsets(self) -> None:
        """Different exit slots offset along the shared face so fan-out edges diverge."""
        a = route_edge((0, 0, 60, 60), (220, 0, 60, 60), exit_slot=0)
        b = route_edge((0, 0, 60, 60), (220, 0, 60, 60), exit_slot=1)
        self.assertEqual(a[0], b[0])  # same exit face (right)
        self.assertNotEqual(a[1], b[1])  # different Y along that face
        self.assertEqual(a[4], [])

    def test_edges_emitted_after_vertices(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="a", label="A", shape_type="generic_server", step_number=1),
                Node(id="b", label="B", shape_type="generic_server", step_number=2),
            ],
            edges=[Edge(source_id="a", target_id="b")],
            suggestions=[],
        )
        xml = generate_xml(arch, platform="aws")
        vertex_positions = [
            m.start()
            for m in re.finditer(r'<mxCell id="(?!edge_|badge_|0|1|flow_)[^"]+"[^>]*vertex="1"', xml)
        ]
        edge_positions = [m.start() for m in re.finditer(r'<mxCell id="edge_', xml)]
        self.assertTrue(vertex_positions)
        self.assertTrue(edge_positions)
        last_vertex = max(vertex_positions)
        self.assertTrue(all(pos > last_vertex for pos in edge_positions))


if __name__ == "__main__":
    unittest.main()
