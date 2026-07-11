"""Regression tests for prompt/token and style quality optimizations."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.diagram_normalize import normalize_architecture
from app.adapters.outbound.diagram.styles import build_style
from app.adapters.outbound.diagram.xml_render import append_edge
from app.adapters.outbound.llm.prompt_builders import architecture_prompt_json, build_chat_prompt
from app.domain.entities import ArchitectureDiagram, Edge, Node


class ArchitectureDumpTests(unittest.TestCase):
    def test_drops_null_geometry(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(
                    id="a",
                    label="A",
                    shape_type="aws_lambda",
                    step_number=1,
                    x=None,
                    y=None,
                    width=None,
                    height=None,
                ),
            ],
            edges=[Edge(source_id="a", target_id="a", label=None)],
            suggestions=[],
            title="T",
        )
        dumped = architecture_prompt_json(arch)
        self.assertNotIn('"x"', dumped)
        self.assertNotIn('"y"', dumped)
        self.assertNotIn('"width"', dumped)
        self.assertNotIn('"height"', dumped)
        self.assertIn("aws_lambda", dumped)

    def test_chat_prompt_uses_compact_dump(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[Node(id="u", label="Users", shape_type="user_client", step_number=1)],
            edges=[],
            suggestions=["add cache"],
        )
        prompt = build_chat_prompt(
            [{"role": "user", "content": "add redis"}],
            "Amazon Web Services (AWS)",
            current_architecture=arch,
        )
        self.assertIn('"id":"u"', prompt)
        self.assertNotIn('"x":null', prompt)


class StylesDenylistTests(unittest.TestCase):
    def test_broken_dynamodb_table_remapped(self) -> None:
        style = build_style("aws_dynamodb_table")
        self.assertIn("resIcon=mxgraph.aws4.dynamodb", style)
        self.assertNotIn("dynamodb_table", style)

    def test_opensearch_uses_elasticsearch_stencil(self) -> None:
        style = build_style("aws_opensearch")
        self.assertIn("elasticsearch_service", style)

    def test_alb_uses_resource_level_shape(self) -> None:
        style = build_style("aws_alb")
        self.assertIn("shape=mxgraph.aws4.application_load_balancer", style)
        self.assertIn("strokeColor=none", style)
        self.assertNotIn("resourceIcon", style)


class EdgeEmitTests(unittest.TestCase):
    def test_omit_empty_value_attr(self) -> None:
        xml: list[str] = []
        edge = Edge(source_id="a", target_id="b", label=None, style="solid")
        layout = {
            "a": {"x": 0, "y": 0, "w": 48, "h": 48},
            "b": {"x": 200, "y": 0, "w": 48, "h": 48},
        }
        nodes = {
            "a": Node(id="a", label="A", shape_type="aws_lambda"),
            "b": Node(id="b", label="B", shape_type="aws_dynamodb"),
        }
        append_edge(
            xml,
            edge,
            cell_id=1,
            layout=layout,
            nodes_by_id=nodes,
            default_edge_color="#ED7100",
            platform_key="aws",
        )
        joined = "\n".join(xml)
        self.assertIn('id="edge_1"', joined)
        self.assertNotIn("value=", joined)
        self.assertIn("rounded=1", joined)

    def test_strip_obvious_labels(self) -> None:
        arch = ArchitectureDiagram(
            nodes=[
                Node(id="a", label="Lambda", shape_type="aws_lambda", step_number=1),
                Node(id="b", label="DDB", shape_type="aws_dynamodb", step_number=2),
            ],
            edges=[Edge(source_id="a", target_id="b", label="Write", style="solid")],
            suggestions=[],
        )
        out = normalize_architecture(arch)
        edge = next(e for e in out.edges if e.source_id == "a" and e.target_id == "b")
        self.assertIsNone(edge.label)


if __name__ == "__main__":
    unittest.main()
