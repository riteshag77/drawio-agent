"""Regression: simplified layout produces valid draw.io XML."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.draftsman import generate_xml
from app.adapters.outbound.diagram.layout import absolute_box, build_children_map, build_layout
from app.domain.entities import ArchitectureDiagram, Edge, Node


def _simple_arch() -> ArchitectureDiagram:
    return ArchitectureDiagram(
        title="Test App",
        nodes=[
            Node(id="user", label="Users", shape_type="user_client", step_number=1),
            Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
            Node(id="cf", label="CloudFront", shape_type="aws_cloudfront", parent_id="cloud", step_number=2),
            Node(id="api", label="API Gateway", shape_type="aws_api_gateway", parent_id="cloud", step_number=3),
            Node(id="lambda", label="Lambda", shape_type="aws_lambda", parent_id="cloud", step_number=4),
            Node(id="db", label="DynamoDB", shape_type="aws_dynamodb", parent_id="cloud", step_number=5),
        ],
        edges=[
            Edge(source_id="user", target_id="cf", label="HTTPS"),
            Edge(source_id="cf", target_id="api", label="HTTPS"),
            Edge(source_id="api", target_id="lambda", label="REST"),
            Edge(source_id="lambda", target_id="db", label="SQL"),
        ],
        suggestions=[],
    )


class SimplifiedLayoutTests(unittest.TestCase):
    def test_generate_xml_produces_valid_output(self) -> None:
        xml = generate_xml(_simple_arch(), platform="aws")
        self.assertIn("<mxGraphModel", xml)
        self.assertIn("id=\"user\"", xml)
        self.assertIn("id=\"cf\"", xml)
        self.assertIn("id=\"api\"", xml)
        self.assertIn("id=\"db\"", xml)
        self.assertIn("</mxfile>", xml)

    def test_layout_places_entry_actors_leftmost(self) -> None:
        arch = _simple_arch()
        cm, roots, nbi = build_children_map(arch.nodes)
        layout = build_layout(nbi, cm, roots)
        self.assertIn("user", layout)
        self.assertIn("cloud", layout)
        self.assertLess(layout["user"]["x"], layout["cloud"]["x"])

    def test_nodes_placed_left_to_right_by_step(self) -> None:
        arch = _simple_arch()
        cm, roots, nbi = build_children_map(arch.nodes)
        layout = build_layout(nbi, cm, roots)
        steps = sorted(
            [(n.id, n.step_number) for n in arch.nodes if n.step_number],
            key=lambda x: (x[1] or 0),
        )
        positions = [(sid, absolute_box(sid, layout, nbi)[0]) for sid, _ in steps if sid in layout]
        for i in range(len(positions) - 1):
            self.assertLess(positions[i][1], positions[i + 1][1])


if __name__ == "__main__":
    unittest.main()
