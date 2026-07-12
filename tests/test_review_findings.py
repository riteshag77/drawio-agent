"""Regression tests for review-finding fixes (layout, edges, SSRF, cycles)."""

from __future__ import annotations

import re
import unittest

from app.adapters.outbound.diagram.draftsman import prepare_and_generate_xml
from app.adapters.outbound.diagram.layout import (
    build_children_map,
    build_layout,
    edge_parent_cell,
)
from app.adapters.outbound.utils.web import assert_public_http_url
from app.adapters.outbound.validation.structural import validate_architecture_structure
from app.domain.entities import ArchitectureDiagram, Edge, Node


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

    def test_pins_literal_public_ip(self) -> None:
        url, host, port, ip = assert_public_http_url("https://1.1.1.1/")
        self.assertEqual(host, "1.1.1.1")
        self.assertEqual(ip, "1.1.1.1")
        self.assertEqual(port, 443)
        self.assertEqual(url, "https://1.1.1.1/")


class WaypointParentTests(unittest.TestCase):
    def test_waypoint_edges_forced_to_root(self) -> None:
        from app.adapters.outbound.diagram.mcp_xml_normalize import force_waypoint_edges_to_root

        xml = (
            '<mxfile host="embed.diagrams.net"><diagram name="t" id="1">'
            '<mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="box" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="100" height="80" as="geometry"/></mxCell>'
            '<mxCell id="edge_1" edge="1" parent="box" source="a" target="b">'
            '<mxGeometry relative="1" as="geometry">'
            '<Array as="points"><mxPoint x="50" y="50"/></Array>'
            "</mxGeometry></mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = force_waypoint_edges_to_root(xml)
        self.assertIn('id="edge_1"', out)
        self.assertRegex(out, r'id="edge_1"[^>]*parent="1"')


class ConfigPathTests(unittest.TestCase):
    def test_prompt_config_dir_resolves(self) -> None:
        from app.adapters.outbound.llm.prompts.system import CONFIG_DIR, _load_prompt

        self.assertTrue((CONFIG_DIR / "architect_default.txt").is_file())
        self.assertTrue((CONFIG_DIR / "contract.txt").is_file())
        text = _load_prompt("contract.txt")
        self.assertIn("EDGE", text.upper())


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
        fixed, fixes = auto_fix_xml("not-xml " + xml)
        self.assertIn("<mxfile", fixed)
        result = validate_and_fix_xml("<<< " + xml)
        out = choose_fixed_xml(xml, result)
        self.assertTrue(out.strip().startswith("<mxfile"), msg=out[:120])
        lines = [ln for ln in out.splitlines() if ln.strip()]
        self.assertTrue(lines[0].startswith("<mxfile"))
        self.assertFalse(lines[1].lstrip().startswith("<mxCell"))


if __name__ == "__main__":
    unittest.main()
