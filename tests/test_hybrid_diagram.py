"""Smoke tests for hybrid diagram generation fixes."""

from __future__ import annotations

import unittest

from app.diagram.draftsman import generate_xml
from app.diagram.edge_router import collect_obstacles, route_edge
from app.diagram.styles import build_style, normalize_shape_type
from app.models import ArchitectureDiagram, Edge, Node
from app.patterns.library import ARCHITECTURE_PATTERNS
from app.services.pipeline import classify_diagram_intent


class HybridDiagramTests(unittest.TestCase):
    def test_three_tier_pattern_uses_canonical_alb_key(self) -> None:
        pattern = next(p for p in ARCHITECTURE_PATTERNS if p.id == "three_tier_web")
        aws_services = pattern.services["aws"]
        self.assertIn("aws_alb", aws_services)
        self.assertNotIn("aws_application_load_balancer", aws_services)

    def test_legacy_alb_key_resolves_to_resource_icon(self) -> None:
        canonical = normalize_shape_type("aws_alb")
        legacy = normalize_shape_type("aws_application_load_balancer")
        self.assertEqual(canonical, legacy)
        style = build_style("aws_alb")
        self.assertIn("application_load_balancer", style)

    def test_azure_legacy_keys_resolve(self) -> None:
        for legacy, fragment in (
            ("azure_app_services", "App_Services"),
            ("azure_sql_database", "SQL_Database"),
            ("azure_kubernetes_services", "Kubernetes_Services"),
        ):
            style = build_style(legacy)
            self.assertIn(fragment, style, msg=f"{legacy} should map to Azure icon")

    def test_classify_simple_vs_cloud(self) -> None:
        self.assertEqual(
            classify_diagram_intent("Simple flowchart: login → validate → dashboard"),
            "simple",
        )
        self.assertEqual(
            classify_diagram_intent("AWS 3-tier web app with ALB and RDS"),
            "cloud",
        )
        self.assertEqual(
            classify_diagram_intent("anything", diagram_mode="simple"),
            "simple",
        )

    def test_edge_router_adds_waypoints_when_obstacle_blocks(self) -> None:
        src = (40, 40, 60, 60)
        tgt = (400, 40, 60, 60)
        obstacle = (200, 20, 80, 80)
        obstacles = collect_obstacles(
            {"a": src, "b": tgt, "obs": obstacle},
            exclude={"a", "b"},
        )
        _, _, _, _, waypoints = route_edge(src, tgt, obstacles, prefer_horizontal=True)
        self.assertTrue(len(waypoints) >= 2)

    def test_generate_xml_with_obstacle_produces_waypoint_array(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="a", label="A", shape_type="generic_server", step_number=1),
                Node(id="b", label="B", shape_type="generic_server", step_number=2),
                Node(id="c", label="C", shape_type="generic_server", step_number=3),
            ],
            edges=[
                Edge(source_id="a", target_id="c", label=None, style="solid", color="#ED7100"),
            ],
            suggestions=[],
        )
        xml = generate_xml(arch, platform="aws")
        self.assertIn("<mxGraphModel", xml)
        self.assertIn('source="a"', xml)
        self.assertIn('target="c"', xml)
        self.assertIn('<Array as="points">', xml)


if __name__ == "__main__":
    unittest.main()
