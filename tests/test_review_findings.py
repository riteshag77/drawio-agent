"""Regression tests for review-finding fixes (layout, repair, edges, SSRF, cycles)."""

from __future__ import annotations

import re
import unittest

from app.adapters.outbound.diagram.draftsman import prepare_and_generate_xml, prepare_architecture
from app.adapters.outbound.diagram.layout import (
    build_children_map,
    build_layout,
    edge_parent_cell,
    layout_settings,
)
from app.adapters.outbound.diagram.topology_repair import repair_topology
from app.adapters.outbound.utils.web import assert_public_http_url
from app.adapters.outbound.validation.structural import validate_architecture_structure
from app.domain.entities import ArchitectureDiagram, Edge, Node


class PlatformRepairTests(unittest.TestCase):
    def test_gcp_repair_does_not_inject_aws_igw_or_az(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="gcp_vpc", is_container=True),
                Node(id="pub", label="Public", shape_type="gcp_subnet", is_container=True, parent_id="vpc"),
                Node(id="web", label="Web", shape_type="gcp_compute_engine", parent_id="pub", step_number=1),
            ],
            edges=[],
            suggestions=[],
        )
        repaired = repair_topology(arch, platform="gcp")
        shapes = {n.shape_type for n in repaired.nodes}
        self.assertNotIn("aws_internet_gateway", shapes)
        self.assertNotIn("aws_az", shapes)


class EdgeParentTests(unittest.TestCase):
    def test_cross_container_edge_uses_root(self) -> None:
        nodes = [
            Node(id="az_a", label="AZa", shape_type="aws_az", is_container=True),
            Node(id="az_b", label="AZb", shape_type="aws_az", is_container=True),
            Node(id="a", label="A", shape_type="aws_ec2", parent_id="az_a"),
            Node(id="b", label="B", shape_type="aws_ec2", parent_id="az_b"),
        ]
        by_id = {n.id: n for n in nodes}
        self.assertEqual(edge_parent_cell("a", "b", by_id), "1")

    def test_same_parent_edge_keeps_container(self) -> None:
        nodes = [
            Node(id="sub", label="Subnet", shape_type="aws_public_subnet", is_container=True),
            Node(id="a", label="A", shape_type="aws_ec2", parent_id="sub"),
            Node(id="b", label="B", shape_type="aws_ec2", parent_id="sub"),
        ]
        by_id = {n.id: n for n in nodes}
        self.assertEqual(edge_parent_cell("a", "b", by_id), "sub")

    def test_xml_cross_container_parent_is_1(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True),
                Node(id="az_a", label="AZ A", shape_type="aws_az", is_container=True, parent_id="vpc"),
                Node(id="az_b", label="AZ B", shape_type="aws_az", is_container=True, parent_id="vpc"),
                Node(id="pub_a", label="Pub A", shape_type="aws_public_subnet", is_container=True, parent_id="az_a"),
                Node(id="pub_b", label="Pub B", shape_type="aws_public_subnet", is_container=True, parent_id="az_b"),
                Node(id="a", label="A", shape_type="aws_ec2", parent_id="pub_a", step_number=1),
                Node(id="b", label="B", shape_type="aws_ec2", parent_id="pub_b", step_number=2),
            ],
            edges=[Edge(source_id="a", target_id="b")],
            suggestions=[],
        )
        _prepared, xml = prepare_and_generate_xml(arch, platform="aws")
        match = re.search(r'<mxCell id="edge_\d+"[^>]*parent="([^"]+)"[^>]*source="a"[^>]*target="b"', xml)
        if match is None:
            match = re.search(r'<mxCell id="edge_\d+"[^>]*source="a"[^>]*target="b"[^>]*parent="([^"]+)"', xml)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "1")


class LayoutRegressionTests(unittest.TestCase):
    def test_grid_fallback_wraps_without_overlap(self) -> None:
        s = layout_settings()
        n = s.max_cols + 1
        nodes = [
            Node(id=f"n{i}", label=f"N{i}", shape_type="generic_server", parent_id="box")
            for i in range(n)
        ]
        nodes.insert(0, Node(id="box", label="Box", shape_type="aws_generic_group", is_container=True))
        children_map, roots, nodes_by_id = build_children_map(nodes)
        layout = build_layout(nodes_by_id, children_map, roots)
        first_row_y = layout["n0"]["y"]
        wrap_y = layout[f"n{s.max_cols}"]["y"]
        self.assertGreater(wrap_y, first_row_y)

    def test_az_places_non_subnet_children(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True),
                Node(id="az_a", label="AZ A", shape_type="aws_az", is_container=True, parent_id="vpc"),
                Node(id="pub", label="Public", shape_type="aws_public_subnet", is_container=True, parent_id="az_a"),
                Node(id="web", label="Web", shape_type="aws_ec2", parent_id="pub", step_number=1),
                Node(id="nat", label="NAT", shape_type="aws_nat_gateway", parent_id="az_a", step_number=2),
            ],
            edges=[],
            suggestions=[],
        )
        prepared = prepare_architecture(arch, "aws")
        children_map, roots, nodes_by_id = build_children_map(prepared.nodes)
        layout = build_layout(nodes_by_id, children_map, roots)
        self.assertIn("nat", layout)
        self.assertGreater(layout["nat"]["y"], 0)


class GraphValidationTests(unittest.TestCase):
    def test_cycle_rejected(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="a", label="A", shape_type="aws_vpc", is_container=True, parent_id="b"),
                Node(id="b", label="B", shape_type="aws_az", is_container=True, parent_id="a"),
            ],
            edges=[],
            suggestions=[],
        )
        result = validate_architecture_structure(arch)
        self.assertFalse(result.valid)
        self.assertTrue(any("cycle" in i.description.lower() for i in result.issues))


class CanonicalArchitectureTests(unittest.TestCase):
    def test_prepare_returns_repaired_nodes(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True),
                Node(id="pub", label="Public", shape_type="aws_public_subnet", is_container=True, parent_id="vpc"),
                Node(id="web", label="Web", shape_type="aws_ec2", parent_id="pub", step_number=1),
            ],
            edges=[],
            suggestions=[],
        )
        prepared, xml = prepare_and_generate_xml(arch, platform="aws")
        self.assertTrue(any(n.shape_type == "aws_internet_gateway" for n in prepared.nodes))
        self.assertIn("aws_internet_gateway", xml)
        self.assertTrue(any(n.shape_type == "aws_az" for n in prepared.nodes))


class SsrfTests(unittest.TestCase):
    def test_rejects_loopback(self) -> None:
        with self.assertRaises(ValueError):
            assert_public_http_url("http://127.0.0.1/")

    def test_rejects_metadata_ip(self) -> None:
        with self.assertRaises(ValueError):
            assert_public_http_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_non_http(self) -> None:
        with self.assertRaises(ValueError):
            assert_public_http_url("file:///etc/passwd")


class XmlDocumentGuardTests(unittest.TestCase):
    def test_auto_fix_does_not_strip_valid_mxfile_to_bare_cells(self) -> None:
        from app.adapters.outbound.diagram.xml_tools import auto_fix_xml, choose_fixed_xml, validate_and_fix_xml

        arch = ArchitectureDiagram(
            nodes=[
                Node(id="a", label="A", shape_type="aws_ec2", step_number=1),
                Node(id="b", label="B", shape_type="aws_ec2", step_number=2),
            ],
            edges=[Edge(source_id="a", target_id="b")],
            suggestions=[],
        )
        _, xml = prepare_and_generate_xml(arch, platform="aws")
        self.assertTrue(xml.strip().startswith("<mxfile"))
        # Previously, prefixing garbage forced strip → bare mxCells → draw.io line2/col5 error
        fixed, fixes = auto_fix_xml("not-xml " + xml)
        self.assertIn("<mxfile", fixed)
        result = validate_and_fix_xml("<<< " + xml)
        out = choose_fixed_xml(xml, result)
        self.assertTrue(out.strip().startswith("<mxfile"), msg=out[:120])
        # Single root: second line must be nested content, not a sibling root element
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertTrue(lines[0].startswith("<mxfile"))
        self.assertFalse(lines[1].lstrip().startswith("<mxCell"))


if __name__ == "__main__":
    unittest.main()
