"""GenericDiagramIR validation — the guardrails around as-is image replication."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.domain.ir.generic import GenericDiagramIR, GenericEdge, GenericGroup, GenericNode


def _node(i: str, shape: str = "rectangle", **kwargs) -> GenericNode:
    return GenericNode(id=i, shape=shape, label=i, **kwargs)


class GenericNodeValidationTests(unittest.TestCase):
    def test_extra_fields_are_rejected(self) -> None:
        """extra='forbid' must reject cloud-only fields an LLM might leak in."""
        with self.assertRaises(ValidationError):
            GenericNode(id="a", shape="rectangle", label="A", shape_type="aws_rds")

    def test_bad_shape_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GenericNode(id="a", shape="not_a_real_shape", label="A")

    def test_reserved_id_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GenericNode(id="0", shape="rectangle", label="A")

    def test_negative_row_col_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            GenericNode(id="a", shape="rectangle", label="A", row=-1)


class GenericDiagramIRSemanticTests(unittest.TestCase):
    def test_empty_nodes_is_an_error(self) -> None:
        ir = GenericDiagramIR(title="Empty")
        self.assertIn("nodes is empty — a diagram needs at least one shape", ir.semantic_errors())
        self.assertFalse(ir.is_valid())

    def test_minimal_valid_diagram(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a"), _node("b")], edges=[GenericEdge(source="a", target="b")])
        self.assertEqual(ir.semantic_errors(), [])
        self.assertTrue(ir.is_valid())
        self.assertEqual(ir.leaf_ids(), {"a", "b"})

    def test_dangling_edge_source_is_an_error(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a")], edges=[GenericEdge(source="missing", target="a")])
        errors = ir.semantic_errors()
        self.assertTrue(any("missing" in e for e in errors))

    def test_dangling_edge_target_is_an_error(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a")], edges=[GenericEdge(source="a", target="missing")])
        errors = ir.semantic_errors()
        self.assertTrue(any("missing" in e for e in errors))

    def test_self_loop_is_an_error(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a")], edges=[GenericEdge(source="a", target="a")])
        errors = ir.semantic_errors()
        self.assertTrue(any("itself" in e for e in errors))

    def test_duplicate_node_id_is_an_error(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a"), _node("a")])
        errors = ir.semantic_errors()
        self.assertTrue(any("duplicate" in e for e in errors))

    def test_node_referencing_unknown_group_is_an_error(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a", group="nope")])
        errors = ir.semantic_errors()
        self.assertTrue(any("unknown group" in e for e in errors))

    def test_group_scoped_node_is_valid(self) -> None:
        ir = GenericDiagramIR(
            groups=[GenericGroup(id="g1", label="Team")],
            nodes=[_node("a", group="g1"), _node("b")],
        )
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual(ir.structural_ids(), {"g1"})
        self.assertEqual(ir.all_ids(), {"a", "b", "g1"})


if __name__ == "__main__":
    unittest.main()
