"""Tests for surgical edits and IaC attachments."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.diagram_edits import (
    apply_diagram_edits,
    looks_like_surgical_edit,
)
from app.adapters.outbound.utils.documents import extract_document
from app.domain.entities.diagram_edit import DiagramEditOp, DiagramEditPlan


class SurgicalEditTests(unittest.TestCase):
    def test_looks_like_surgical(self):
        self.assertTrue(looks_like_surgical_edit("Add a NAT gateway in the public subnet", has_current_xml=True))
        self.assertFalse(looks_like_surgical_edit("Redesign the whole architecture from scratch", has_current_xml=True))
        self.assertFalse(looks_like_surgical_edit("Add a NAT gateway", has_current_xml=False))

    def test_apply_add_and_connect(self):
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="alb" value="ALB" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(
            applicable=True,
            ops=[
                DiagramEditOp(op="add_node", id="nat", label="NAT", shape_type="aws.nat", parent="1", x=200, y=40),
                DiagramEditOp(op="connect", source="alb", target="nat", edge_id="e_alb_nat"),
            ],
        )
        out = apply_diagram_edits(base, plan)
        self.assertIn('id="nat"', out)
        self.assertIn('shape_type="aws.nat"', out)
        self.assertIn('source="alb"', out)
        self.assertIn('target="nat"', out)

    def test_connect_self_loop_is_a_noop(self):
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n1" value="N1" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(applicable=True, ops=[DiagramEditOp(op="connect", source="n1", target="n1")])
        out = apply_diagram_edits(base, plan)
        self.assertEqual(out.count('edge="1"'), 0)

    def test_connect_exact_duplicate_is_a_noop(self):
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n1" value="N1" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="n2" value="N2" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="n1" target="n2">'
            '<mxGeometry relative="1" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(applicable=True, ops=[DiagramEditOp(op="connect", source="n1", target="n2")])
        out = apply_diagram_edits(base, plan)
        self.assertEqual(out.count('edge="1"'), 1)

    def test_disconnect_removes_only_first_matching_edge(self):
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="n1" value="N1" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="n2" value="N2" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="n1" target="n2">'
            '<mxGeometry relative="1" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="e2" edge="1" parent="1" source="n1" target="n2">'
            '<mxGeometry relative="1" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(applicable=True, ops=[DiagramEditOp(op="disconnect", source="n1", target="n2")])
        out = apply_diagram_edits(base, plan)
        self.assertEqual(out.count('edge="1"'), 1)

    def test_delete_node_bridges_single_chain(self):
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="c" value="C" vertex="1" parent="1">'
            '<mxGeometry x="360" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="e_ab" edge="1" parent="1" source="a" target="b">'
            '<mxGeometry relative="1" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="e_bc" edge="1" parent="1" source="b" target="c">'
            '<mxGeometry relative="1" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(applicable=True, ops=[DiagramEditOp(op="delete_node", id="b")])
        out = apply_diagram_edits(base, plan)
        tree = ET.fromstring(out)
        edges = [(c.get("source"), c.get("target")) for c in tree.iter("mxCell") if c.get("edge") == "1"]
        self.assertIn(("a", "c"), edges)
        self.assertNotIn("b", [c.get("id") for c in tree.iter("mxCell")])

    def test_delete_node_cross_product_bridges_fan_in_fan_out(self):
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="a1" value="A1" vertex="1" parent="1"><mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="a2" value="A2" vertex="1" parent="1"><mxGeometry x="0" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="hub" value="HUB" vertex="1" parent="1"><mxGeometry x="200" y="50" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="c1" value="C1" vertex="1" parent="1"><mxGeometry x="400" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="c2" value="C2" vertex="1" parent="1"><mxGeometry x="400" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e_a1" edge="1" parent="1" source="a1" target="hub"><mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e_a2" edge="1" parent="1" source="a2" target="hub"><mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e_c1" edge="1" parent="1" source="hub" target="c1"><mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e_c2" edge="1" parent="1" source="hub" target="c2"><mxGeometry relative="1" as="geometry"/></mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(applicable=True, ops=[DiagramEditOp(op="delete_node", id="hub")])
        out = apply_diagram_edits(base, plan)
        tree = ET.fromstring(out)
        edges = {(c.get("source"), c.get("target")) for c in tree.iter("mxCell") if c.get("edge") == "1"}
        self.assertEqual(
            edges,
            {("a1", "c1"), ("a1", "c2"), ("a2", "c1"), ("a2", "c2")},
        )

    def test_delete_node_does_not_bridge_a_self_loop_pair(self):
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="a" value="A" vertex="1" parent="1"><mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="hub" value="HUB" vertex="1" parent="1"><mxGeometry x="200" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e_in" edge="1" parent="1" source="a" target="hub"><mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e_out" edge="1" parent="1" source="hub" target="a"><mxGeometry relative="1" as="geometry"/></mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(applicable=True, ops=[DiagramEditOp(op="delete_node", id="hub")])
        out = apply_diagram_edits(base, plan)
        tree = ET.fromstring(out)
        edges = [(c.get("source"), c.get("target")) for c in tree.iter("mxCell") if c.get("edge") == "1"]
        self.assertEqual(edges, [])

    def test_relabel_keeps_ir_layout_marker(self):
        """Label-only edits must not unlock fit (preserves corridor band)."""
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0" layout="ir"/>'
            '<mxCell id="alb" value="ALB" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(
            applicable=True,
            ops=[DiagramEditOp(op="relabel", id="alb", label="Load Balancer")],
        )
        out = apply_diagram_edits(base, plan)
        layer = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "1")
        self.assertEqual(layer.get("layout"), "ir")
        self.assertIn('value="Load Balancer"', out)

    def test_geometry_edit_clears_ir_layout_marker(self):
        """Add/move/delete must clear layout=ir so fit can run."""
        base = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0" layout="ir"/>'
            '<mxCell id="alb" value="ALB" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel></diagram></mxfile>'
        )
        plan = DiagramEditPlan(
            applicable=True,
            ops=[DiagramEditOp(op="add_node", id="nat", label="NAT", parent="1", x=200, y=40)],
        )
        out = apply_diagram_edits(base, plan)
        layer = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "1")
        self.assertIsNone(layer.get("layout"))
        self.assertIn('id="nat"', out)


class IacAttachmentTests(unittest.TestCase):
    def test_terraform_file_banner(self):
        data = b'resource "aws_s3_bucket" "b" {\n  bucket = "demo"\n}\n'
        result = extract_document("main.tf", data)
        self.assertEqual(result.kind, "text")
        self.assertIn("[IaC attachment", result.content or "")
        self.assertIn("aws_s3_bucket", result.content or "")

    def test_k8s_yaml_banner(self):
        data = b"apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: api\n"
        result = extract_document("deploy.yaml", data)
        self.assertEqual(result.kind, "text")
        self.assertIn("Kubernetes", result.content or "")


if __name__ == "__main__":
    unittest.main()
