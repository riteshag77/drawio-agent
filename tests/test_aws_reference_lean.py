"""Tests for lean prompts and basic layout."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.draftsman import generate_xml
from app.adapters.outbound.diagram.layout import build_children_map, build_layout
from app.adapters.outbound.llm.prompts.system import estimate_prompt_tokens, get_system_prompt
from app.domain.entities import ArchitectureDiagram, Edge, Node


class LeanPromptBudgetTests(unittest.TestCase):
    def test_aws_new_diagram_system_prompt_under_1500_tokens(self) -> None:
        prompt = get_system_prompt(platform="aws", is_edit_mode=False, include_schema=True)
        tokens = estimate_prompt_tokens(prompt)
        self.assertLessEqual(tokens, 1500, f"system prompt ~{tokens} tokens exceeds 1500 budget")
        self.assertNotIn("Draw.io stack/autolayout", prompt)
        self.assertNotIn("GCP — Containers", prompt)

    def test_edit_mode_skips_topology_essay(self) -> None:
        prompt = get_system_prompt(platform="aws", is_edit_mode=True, include_schema=False)
        self.assertIn("AWS SHAPE CATALOGUE", prompt)
        self.assertNotIn("AWS REFERENCE TOPOLOGY", prompt)
        self.assertLessEqual(estimate_prompt_tokens(prompt), 1200)

    def test_platform_catalogue_is_filtered(self) -> None:
        aws = get_system_prompt(platform="aws", include_schema=True)
        gcp = get_system_prompt(platform="gcp", include_schema=True)
        azure = get_system_prompt(platform="azure", include_schema=True)
        self.assertIn("aws_fargate", aws)
        self.assertNotIn("azure_vm", aws)
        self.assertIn("gcp_cloud_run", gcp)
        self.assertNotIn("aws_fargate", gcp)

    def test_simple_architecture_generates_valid_xml(self) -> None:
        arch = ArchitectureDiagram(
            title="Simple API",
            nodes=[
                Node(id="user", label="Users", shape_type="user_client", step_number=1),
                Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
                Node(id="api", label="API Gateway", shape_type="aws_api_gateway", parent_id="cloud", step_number=2),
                Node(id="lambda", label="Lambda", shape_type="aws_lambda", parent_id="cloud", step_number=3),
            ],
            edges=[
                Edge(source_id="user", target_id="api", label="HTTPS"),
                Edge(source_id="api", target_id="lambda", label="REST"),
            ],
            suggestions=[],
        )
        xml = generate_xml(arch, platform="aws")
        self.assertIn("<mxGraphModel", xml)
        self.assertIn("id=\"api\"", xml)
        self.assertIn("id=\"lambda\"", xml)


class BasicLayoutTests(unittest.TestCase):
    def test_ltr_ordering_by_step_number(self) -> None:
        arch = ArchitectureDiagram(
            title="Flow Test",
            nodes=[
                Node(id="a", label="A", shape_type="aws_lambda", step_number=1),
                Node(id="b", label="B", shape_type="aws_lambda", step_number=3),
                Node(id="c", label="C", shape_type="aws_lambda", step_number=2),
            ],
            edges=[],
            suggestions=[],
        )
        cm, roots, nbi = build_children_map(arch.nodes)
        layout = build_layout(nbi, cm, roots)
        self.assertLess(layout["a"]["x"], layout["c"]["x"])
        self.assertLess(layout["c"]["x"], layout["b"]["x"])


if __name__ == "__main__":
    unittest.main()
