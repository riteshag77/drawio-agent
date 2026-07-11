"""Tests for lean prompts, pattern blueprints, and AWS reference topology."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.diagram_normalize import normalize_architecture
from app.adapters.outbound.diagram.draftsman import generate_xml
from app.adapters.outbound.diagram.layout import build_children_map, build_layout
from app.adapters.outbound.diagram.template_merge import apply_pattern_blueprint, blueprint_diagram
from app.adapters.outbound.diagram.topology_repair import (
    ensure_igw,
    reparent_edge_services,
    reparent_entry_actors,
    repair_topology,
)
from app.adapters.outbound.llm.prompts.system import estimate_prompt_tokens, get_system_prompt
from app.adapters.outbound.patterns.file_repository import FilePatternRepository, format_patterns_for_prompt
from app.domain.entities import ArchitectureDiagram, Edge, Node


class LeanPromptBudgetTests(unittest.TestCase):
    def test_aws_new_diagram_system_prompt_under_2k_tokens(self) -> None:
        prompt = get_system_prompt(platform="aws", is_edit_mode=False, include_schema=True)
        tokens = estimate_prompt_tokens(prompt)
        self.assertLessEqual(tokens, 2000, f"system prompt ~{tokens} tokens exceeds 2k budget")
        self.assertNotIn("Draw.io stack/autolayout", prompt)
        self.assertNotIn("GCP — Containers", prompt)
        self.assertIn("attached", prompt.lower())

    def test_edit_mode_skips_topology_essay(self) -> None:
        prompt = get_system_prompt(platform="aws", is_edit_mode=True, include_schema=False)
        self.assertIn("AWS SHAPE CATALOGUE", prompt)
        self.assertNotIn("AWS REFERENCE TOPOLOGY", prompt)
        self.assertLessEqual(estimate_prompt_tokens(prompt), 1200)

    def test_platform_catalogue_is_filtered(self) -> None:
        aws = get_system_prompt(platform="aws", include_schema=True)
        gcp = get_system_prompt(platform="gcp", include_schema=True)
        self.assertIn("aws_fargate", aws)
        self.assertNotIn("azure_vm", aws)
        self.assertIn("gcp_cloud_run", gcp)
        self.assertNotIn("aws_fargate", gcp)


class PatternBlueprintTests(unittest.TestCase):
    def test_three_tier_blueprint_loads(self) -> None:
        diagram = blueprint_diagram("three_tier_web", "aws")
        self.assertIsNotNone(diagram)
        assert diagram is not None
        ids = {n.id for n in diagram.nodes}
        self.assertIn("aws_az_a", ids)
        self.assertIn("aws_az_b", ids)
        self.assertIn("aws_internet_gateway", ids)
        self.assertIn("aws_waf", ids)
        waf = next(n for n in diagram.nodes if n.id == "aws_waf")
        self.assertIsNone(waf.step_number)

    def test_apply_blueprint_fills_missing_az(self) -> None:
        sparse = ArchitectureDiagram(
            nodes=[
                Node(id="user_client", label="Users", shape_type="user_client", step_number=1),
                Node(id="aws_cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="aws_cloudfront", label="CloudFront", shape_type="aws_cloudfront", parent_id="aws_cloud", step_number=2),
            ],
            edges=[],
            suggestions=[],
            pattern_id="three_tier_web",
        )
        merged = apply_pattern_blueprint(sparse, "three_tier_web", "aws")
        ids = {n.id for n in merged.nodes}
        self.assertIn("aws_az_a", ids)
        self.assertIn("aws_az_b", ids)
        self.assertIn("aws_vpc", ids)
        self.assertEqual(merged.nodes[0].id, "user_client")  # LLM nodes preserved first

    def test_format_patterns_includes_skeleton_json(self) -> None:
        repo = FilePatternRepository()
        pattern = repo.get_by_id("three_tier_web")
        self.assertIsNotNone(pattern)
        block = format_patterns_for_prompt([pattern], "aws")
        self.assertIn("Skeleton JSON", block)
        self.assertIn("aws_az_a", block)
        self.assertIn("protects", block)


class TopologyRepairTests(unittest.TestCase):
    def test_ensure_igw_when_public_subnet_present(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True),
                Node(id="pub", label="Public Subnet", shape_type="aws_public_subnet", is_container=True, parent_id="vpc"),
            ],
            edges=[],
            suggestions=[],
        )
        fixed = ensure_igw(arch)
        self.assertTrue(any("internet_gateway" in n.shape_type for n in fixed.nodes))

    def test_reparent_cloudfront_to_cloud(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True, parent_id="cloud"),
                Node(id="cf", label="CloudFront", shape_type="aws_cloudfront", parent_id="vpc", step_number=2),
            ],
            edges=[],
            suggestions=[],
        )
        fixed = reparent_edge_services(arch)
        cf = next(n for n in fixed.nodes if n.id == "cf")
        self.assertEqual(cf.parent_id, "cloud")

    def test_entry_actors_forced_outside_cloud(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="vpc", label="VPC", shape_type="aws_vpc", is_container=True, parent_id="cloud"),
                Node(id="pub", label="Public Subnet", shape_type="aws_public_subnet", is_container=True, parent_id="vpc"),
                Node(id="users", label="Users", shape_type="user_client", parent_id="pub", step_number=1),
                Node(id="alb", label="ALB", shape_type="aws_alb", parent_id="pub"),
            ],
            edges=[],
            suggestions=[],
        )
        fixed = reparent_entry_actors(arch)
        users = next(n for n in fixed.nodes if n.id == "users")
        self.assertIsNone(users.parent_id)

        repaired = repair_topology(arch)
        users2 = next(n for n in repaired.nodes if n.id == "users")
        self.assertIsNone(users2.parent_id)

        cm, roots, nbi = build_children_map(repaired.nodes)
        layout = build_layout(nbi, cm, roots)
        self.assertIn("users", roots)
        self.assertLess(layout["users"]["x"], layout["cloud"]["x"])

    def test_full_pipeline_ha_reference_invariants(self) -> None:
        """Sparse LLM output + blueprint + repair + normalize → AWS reference shape."""
        sparse = ArchitectureDiagram(
            title="HA Web App",
            pattern_id="three_tier_web",
            nodes=[
                Node(id="user_client", label="Users", shape_type="user_client", step_number=1),
                Node(id="aws_cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="aws_route53", label="Route 53", shape_type="aws_route53", parent_id="aws_cloud", step_number=2),
                Node(id="aws_cloudfront", label="CloudFront", shape_type="aws_cloudfront", parent_id="aws_cloud", step_number=3),
                Node(id="aws_waf", label="WAF", shape_type="aws_waf", parent_id="aws_cloud", step_number=4),
                Node(id="aws_shield", label="Shield", shape_type="aws_shield", parent_id="aws_cloud", step_number=5),
                Node(id="aws_api_gateway", label="API Gateway", shape_type="aws_api_gateway", parent_id="aws_cloud", step_number=6),
            ],
            edges=[
                Edge(source_id="user_client", target_id="aws_route53", label="DNS"),
                Edge(source_id="aws_route53", target_id="aws_cloudfront", label="DNS"),
                Edge(source_id="aws_cloudfront", target_id="aws_waf", label="HTTPS"),
                Edge(source_id="aws_waf", target_id="aws_shield", label="HTTPS"),
                Edge(source_id="aws_shield", target_id="aws_api_gateway", label="HTTPS"),
            ],
            suggestions=[],
        )
        prepared = apply_pattern_blueprint(sparse, "three_tier_web", "aws")
        prepared = repair_topology(prepared)
        prepared = normalize_architecture(prepared)

        ids = {n.id: n for n in prepared.nodes}
        self.assertIn("aws_az_a", ids)
        self.assertIn("aws_az_b", ids)
        self.assertTrue(any(is_igw_like(n) for n in prepared.nodes))

        # WAF/Shield in security band, no badges
        self.assertEqual(ids["aws_waf"].parent_id, "band_security")
        self.assertIsNone(ids["aws_waf"].step_number)
        self.assertIsNone(ids["aws_shield"].step_number)

        solid = {(e.source_id, e.target_id) for e in prepared.edges if e.style != "dashed"}
        self.assertIn(("aws_cloudfront", "aws_api_gateway"), solid)
        self.assertNotIn(("aws_cloudfront", "aws_waf"), solid)

        protects = [e for e in prepared.edges if (e.label or "").lower() == "protects"]
        self.assertGreaterEqual(len(protects), 2)

        # IGW on VPC top edge after layout
        cm, roots, nbi = build_children_map(prepared.nodes)
        layout = build_layout(nbi, cm, roots)
        igw_id = next(n.id for n in prepared.nodes if "internet_gateway" in n.shape_type)
        self.assertEqual(layout[igw_id]["y"], max(-30, -layout[igw_id]["h"] // 2))

        xml = generate_xml(sparse, platform="aws")
        self.assertIn('id="band_security"', xml)
        self.assertIn('id="aws_az_a"', xml)
        self.assertIn('id="aws_az_b"', xml)


def is_igw_like(node: Node) -> bool:
    return "internet_gateway" in (node.shape_type or "")


if __name__ == "__main__":
    unittest.main()
