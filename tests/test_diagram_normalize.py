"""Tests for diagram normalization, bands, and quality gates."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.diagram_normalize import normalize_architecture
from app.adapters.outbound.diagram.draftsman import generate_xml
from app.adapters.outbound.diagram.layout import build_children_map, build_layout
from app.domain.entities import ArchitectureDiagram, Edge, Node


def _ha_web_arch() -> ArchitectureDiagram:
    return ArchitectureDiagram(
        title="HA Web App",
        nodes=[
            Node(id="user_client", label="Users", shape_type="user_client", step_number=1),
            Node(id="aws_cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
            Node(id="aws_route53", label="Route 53", shape_type="aws_route53", step_number=2, parent_id="aws_cloud"),
            Node(id="aws_cloudfront", label="CloudFront", shape_type="aws_cloudfront", step_number=3, parent_id="aws_cloud"),
            Node(id="aws_shield_advanced", label="Shield Advanced", shape_type="aws_shield_advanced", step_number=4, parent_id="aws_cloud"),
            Node(id="aws_waf", label="WAF", shape_type="aws_waf", step_number=5, parent_id="aws_cloud"),
            Node(id="aws_region", label="Region", shape_type="aws_region", is_container=True, parent_id="aws_cloud"),
            Node(id="aws_vpc", label="VPC", shape_type="aws_vpc", is_container=True, parent_id="aws_region"),
            Node(id="aws_internet_gateway", label="Internet Gateway", shape_type="aws_internet_gateway", parent_id="aws_vpc"),
            Node(id="aws_az_a", label="AZ A", shape_type="aws_az", is_container=True, parent_id="aws_vpc"),
            Node(id="aws_public_subnet_a", label="Public Subnet", shape_type="aws_public_subnet", is_container=True, parent_id="aws_az_a"),
            Node(id="aws_private_subnet_a", label="Private Subnet", shape_type="aws_private_subnet", is_container=True, parent_id="aws_az_a"),
            Node(id="aws_data_subnet_a", label="Data Subnet", shape_type="aws_private_subnet", is_container=True, parent_id="aws_az_a"),
            Node(id="aws_api_gateway", label="API Gateway", shape_type="aws_api_gateway", step_number=6, parent_id="aws_public_subnet_a"),
            Node(id="aws_alb_a", label="ALB", shape_type="aws_alb", step_number=7, parent_id="aws_public_subnet_a"),
            Node(id="aws_fargate_a", label="Fargate", shape_type="aws_fargate", step_number=8, parent_id="aws_private_subnet_a"),
            Node(id="aws_elasticache_a", label="ElastiCache", shape_type="aws_elasticache", step_number=9, parent_id="aws_private_subnet_a"),
            Node(id="aws_aurora_a", label="Aurora", shape_type="aws_aurora", step_number=10, parent_id="aws_data_subnet_a"),
            Node(id="aws_cloudwatch", label="CloudWatch", shape_type="aws_cloudwatch", parent_id="aws_cloud"),
        ],
        edges=[
            Edge(source_id="user_client", target_id="aws_route53", label="DNS"),
            Edge(source_id="aws_route53", target_id="aws_cloudfront", label="DNS"),
            Edge(source_id="aws_cloudfront", target_id="aws_shield_advanced", label="HTTPS"),
            Edge(source_id="aws_shield_advanced", target_id="aws_waf", label="HTTPS"),
            Edge(source_id="aws_waf", target_id="aws_api_gateway", label="HTTPS"),
            Edge(source_id="aws_api_gateway", target_id="aws_fargate_a", label="REST"),
            Edge(source_id="aws_fargate_a", target_id="aws_elasticache_a", label="Redis"),
            Edge(source_id="aws_fargate_a", target_id="aws_aurora_a", label="SQL"),
        ],
        suggestions=[],
    )


class DiagramNormalizeTests(unittest.TestCase):
    def test_removes_inline_security_hops(self) -> None:
        norm = normalize_architecture(_ha_web_arch())
        ids = {(e.source_id, e.target_id) for e in norm.edges if e.style != "dashed"}
        self.assertIn(("aws_cloudfront", "aws_api_gateway"), ids)
        self.assertNotIn(("aws_cloudfront", "aws_shield_advanced"), ids)
        self.assertNotIn(("aws_waf", "aws_api_gateway"), ids)

    def test_moves_waf_shield_to_security_band(self) -> None:
        norm = normalize_architecture(_ha_web_arch())
        nodes = {n.id: n for n in norm.nodes}
        self.assertEqual(nodes["aws_waf"].parent_id, "band_security")
        self.assertEqual(nodes["aws_shield_advanced"].parent_id, "band_security")
        self.assertIsNone(nodes["aws_waf"].step_number)

    def test_protects_edges_to_cloudfront(self) -> None:
        norm = normalize_architecture(_ha_web_arch())
        protects = [e for e in norm.edges if (e.label or "").lower() == "protects"]
        sources = {e.source_id for e in protects}
        self.assertIn("aws_waf", sources)
        self.assertIn("aws_shield_advanced", sources)

    def test_renumbers_flow_to_eight_steps(self) -> None:
        norm = normalize_architecture(_ha_web_arch())
        self.assertEqual(len(norm.flow_steps), 8)
        self.assertEqual(norm.flow_steps[0].title, "User Request")
        self.assertEqual(norm.flow_steps[3].title, "API Entry Point")

    def test_igw_placed_on_vpc_top_edge(self) -> None:
        norm = normalize_architecture(_ha_web_arch())
        cm, roots, nbi = build_children_map(norm.nodes)
        layout = build_layout(nbi, cm, roots)
        igw = layout["aws_internet_gateway"]
        vpc = layout["aws_vpc"]
        self.assertEqual(igw["y"], -30)
        self.assertAlmostEqual(igw["x"], vpc["w"] // 2 - igw["w"] // 2, delta=30)

    def test_generates_band_containers_in_xml(self) -> None:
        xml = generate_xml(_ha_web_arch(), platform="aws")
        self.assertIn('id="band_security"', xml)
        self.assertIn('id="band_logging"', xml)
        self.assertIn("endArrow=none", xml)
        # Band chrome is label-only (no AWS group shape icon)
        self.assertNotIn("mxgraph.aws4.group", xml.split('id="band_security"')[1].split("</mxCell>")[0])
        # Empty management/integration bands are not forced onto the page
        self.assertNotIn('id="band_management"', xml)
        self.assertNotIn('id="band_integration"', xml)


if __name__ == "__main__":
    unittest.main()
