"""Tests for surgical edits and IaC attachments."""

from __future__ import annotations

import unittest

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
