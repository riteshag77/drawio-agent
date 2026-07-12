"""Tests for diagram normalization (dedup, label stripping) and quality gates."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.diagram_normalize import normalize_architecture
from app.adapters.outbound.diagram.draftsman import generate_xml
from app.domain.entities import ArchitectureDiagram, Edge, Node


def _simple_arch() -> ArchitectureDiagram:
    return ArchitectureDiagram(
        title="Test",
        nodes=[
            Node(id="user", label="Users", shape_type="user_client", step_number=1),
            Node(id="cloud", label="AWS Cloud", shape_type="aws_cloud", is_container=True),
            Node(id="cf", label="CloudFront", shape_type="aws_cloudfront", parent_id="cloud", step_number=2),
            Node(id="api", label="API Gateway", shape_type="aws_api_gateway", parent_id="cloud", step_number=3),
            Node(id="lambda", label="Lambda", shape_type="aws_lambda", parent_id="cloud", step_number=4),
        ],
        edges=[
            Edge(source_id="user", target_id="cf", label="HTTPS"),
            Edge(source_id="cf", target_id="api", label="HTTPS"),
        ],
        suggestions=[],
    )


class DiagramNormalizeTests(unittest.TestCase):
    def test_deduplicates_identical_edges(self) -> None:
        arch = _simple_arch()
        arch = arch.model_copy(update={
            "edges": list(arch.edges) + [Edge(source_id="user", target_id="cf", label="HTTPS")],
        })
        norm = normalize_architecture(arch)
        cf_edges = [e for e in norm.edges if e.source_id == "user" and e.target_id == "cf"]
        self.assertEqual(len(cf_edges), 1)

    def test_strips_obvious_edge_labels(self) -> None:
        arch = _simple_arch()
        arch = arch.model_copy(update={
            "edges": [Edge(source_id="user", target_id="cf", label="request")],
        })
        norm = normalize_architecture(arch)
        self.assertIsNone(norm.edges[0].label)

    def test_preserves_protocol_labels(self) -> None:
        arch = _simple_arch()
        arch = arch.model_copy(update={
            "edges": [Edge(source_id="cf", target_id="api", label="HTTPS")],
        })
        norm = normalize_architecture(arch)
        self.assertEqual(norm.edges[0].label, "HTTPS")

    def test_generates_valid_xml(self) -> None:
        xml = generate_xml(_simple_arch(), platform="aws")
        self.assertIn("<mxGraphModel", xml)
        self.assertIn("id=\"cf\"", xml)


if __name__ == "__main__":
    unittest.main()
