"""Sparse process-storyboard Logical (serverless auth) — Network/Security/Data untouched."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import assign_logical_shape_types
from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.view_conformance import score_view
from app.adapters.outbound.diagram.view_fixers import apply_view_fixers
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.ir import Corridor, ExternalActor, Flow, Leaf, LogicalIR


def serverless_auth_logical_ir() -> LogicalIR:
    """Mirrors a sparse serverless-auth storyboard at IR level (sparse process path)."""
    return LogicalIR(
        corridors=[
            Corridor(
                id="domain_experience",
                label="Experience",
                leaves=[Leaf(id="react", shape_type="logical_portal", label="React Client")],
            ),
            Corridor(
                id="domain_core",
                label="Core",
                leaves=[
                    Leaf(
                        id="cognito",
                        shape_type="aws_cognito",
                        label="Amazon Cognito",
                        criticality="critical",
                    ),
                    Leaf(id="apigw", shape_type="aws_api_gateway", label="API Gateway"),
                    Leaf(id="lambda_fn", shape_type="aws_lambda", label="AWS Lambda"),
                ],
            ),
            Corridor(
                id="domain_data",
                label="Data",
                leaves=[
                    Leaf(
                        id="ddb",
                        shape_type="aws_dynamodb",
                        label="DynamoDB",
                        criticality="critical",
                    ),
                ],
            ),
        ],
        external_actors=[
            ExternalActor(id="user", kind="users", label="User"),
            ExternalActor(id="stripe", kind="saas_egress", label="Stripe API"),
        ],
        flows=[
            Flow(source="user", target="react", label="Initiates Login"),
            Flow(source="react", target="cognito", label="Authenticates"),
            Flow(source="react", target="apigw", label="Authenticated Request (JWT)"),
            Flow(source="apigw", target="cognito", label="Validate JWT"),
            Flow(source="apigw", target="lambda_fn", label="Forwards Request"),
            Flow(source="lambda_fn", target="stripe", label="Query Subscription"),
            Flow(source="lambda_fn", target="ddb", label="Fetch User Data"),
            Flow(
                source="lambda_fn",
                target="react",
                label="Return Data (JSON)",
                primary=False,
            ),
        ],
    )


class ServerlessAuthLogicalTests(unittest.TestCase):
    def test_ir_valid_and_sparse(self) -> None:
        ir = serverless_auth_logical_ir()
        self.assertEqual(ir.semantic_errors(), [])
        total = sum(len(c.leaves) for c in ir.corridors)
        self.assertLessEqual(total, 10)
        self.assertTrue(any(l.shape_type.startswith("aws_") for c in ir.corridors for l in c.leaves))

    def test_compile_preserves_aws_shape_types(self) -> None:
        xml = compile_corridor_ir(serverless_auth_logical_ir(), page_name="Logical")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        fixed = apply_view_fixers(repaired, view="logical")
        fixed = assign_logical_shape_types(fixed)
        by_id = {c.get("id"): c for c in ET.fromstring(fixed).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["cognito"].get("shape_type"), "aws_cognito")
        self.assertEqual(by_id["apigw"].get("shape_type"), "aws_api_gateway")
        self.assertEqual(by_id["lambda_fn"].get("shape_type"), "aws_lambda")
        self.assertEqual(by_id["ddb"].get("shape_type"), "aws_dynamodb")
        self.assertIn("DynamoDB", by_id["ddb"].get("value") or "")
        # Flat storyboard: no domain columns; cloud services under one boundary.
        for domain in ("domain_experience", "domain_core", "domain_data", "actors_left"):
            self.assertNotIn(domain, by_id)
        self.assertIn("cloud_boundary", by_id)
        self.assertEqual(by_id["react"].get("parent"), "1")
        self.assertEqual(by_id["cognito"].get("parent"), "cloud_boundary")
        self.assertEqual(by_id["user"].get("parent"), "1")
        self.assertEqual(by_id["stripe"].get("parent"), "1")
        # No VPC / subnet vocabulary on Logical.
        blob = fixed.lower()
        self.assertNotIn("subnet", blob)
        self.assertNotIn("vpc", blob)

    def test_view_score_accepts_solution_cloud_icons(self) -> None:
        xml = compile_corridor_ir(serverless_auth_logical_ir(), page_name="Logical")
        repaired, _ = repair_xml(xml)
        fixed = assign_logical_shape_types(apply_view_fixers(repaired, view="logical"))
        scored = score_view(fixed, view="logical", min_score=60)
        self.assertFalse(any(v.code == "cloud_shape_on_logical" for v in scored.violations))

    def test_diagnose_clean_enough(self) -> None:
        xml = compile_corridor_ir(serverless_auth_logical_ir(), page_name="Logical")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        diag = diagnose_xml(repaired)
        self.assertEqual(diag.errors, [])


if __name__ == "__main__":
    unittest.main()
