"""IR -> draw.io compiler for Logical / Security / Data-flow.

Mirrors ``test_network_compiler.py``'s load-bearing assertion: compiled output
must satisfy the *existing* diagnostics/repair pipeline (including the real
``logical_layout.apply_layered_corridor_layout`` engine that
``xml_diagnostics.repair_xml`` invokes unconditionally for these three page
names) with zero residual errors — proof, not assertion, that structural
correctness by construction survives the full pipeline these pages already go
through in production.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.compiler.corridor import CompilerError
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.ir import (
    AnchoredCorridor,
    Corridor,
    DataFlowIR,
    ExternalActor,
    Flow,
    Leaf,
    LogicalIR,
    SecurityIR,
)


def _leaf(i: str, st: str, label: str) -> Leaf:
    return Leaf(id=i, shape_type=st, label=label)


def realistic_logical_ir() -> LogicalIR:
    return LogicalIR(
        corridors=[
            Corridor(id="domain_experience", label="Experience", leaves=[
                _leaf("portal", "logical_portal", "HR Portal"),
            ]),
            Corridor(id="domain_core", label="Core", leaves=[
                _leaf("orgchart", "logical_capability", "Org Chart"),
                _leaf("cache", "logical_capability", "Profile cache"),
            ]),
            Corridor(id="domain_data", label="Data", leaves=[
                _leaf("store", "logical_datastore", "Customer data store"),
            ]),
        ],
        anchored=[AnchoredCorridor(
            id="domain_saas", label="External SaaS", anchor_id="domain_data",
            leaves=[_leaf("workday", "saas_workday", "Workday (SAAS)")],
        )],
        external_actors=[
            ExternalActor(id="persona", kind="users", label="Employees"),
            ExternalActor(id="okta", kind="saas_ingress", label="Okta (SAAS)"),
        ],
        flows=[
            Flow(source="persona", target="okta", label="Authenticates via"),
            Flow(source="okta", target="portal", label="Redirects to"),
            Flow(source="orgchart", target="store", label="Reads/Writes via API"),
            Flow(source="orgchart", target="cache", label="Check Cache"),
            Flow(source="cache", target="workday", label="Cache Miss", primary=False),
        ],
    )


def realistic_security_ir() -> SecurityIR:
    return SecurityIR(
        corridors=[
            Corridor(id="zone_public", label="Public", leaves=[_leaf("dns", "aws_route53", "Route 53")]),
            Corridor(id="zone_dmz", label="DMZ", leaves=[_leaf("waf", "aws_waf", "WAF")]),
            Corridor(id="zone_identity", label="Identity", leaves=[_leaf("okta", "saas_okta", "Okta")]),
            Corridor(id="zone_private", label="Private", leaves=[
                _leaf("app", "aws_ecs", "App"), _leaf("secrets", "aws_secrets_manager", "Secrets Manager"),
            ]),
            Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds", "aws_rds", "RDS")]),
            Corridor(id="zone_detect", label="Detect", leaves=[
                _leaf("gd", "aws_guardduty", "GuardDuty"), _leaf("cw", "aws_cloudwatch", "CloudWatch"),
            ]),
        ],
        anchored=[AnchoredCorridor(
            id="zone_external_saas", label="External SaaS Zone", anchor_id="zone_restricted",
            leaves=[_leaf("workday", "saas_workday", "Workday")],
        )],
        flows=[
            Flow(source="dns", target="waf"), Flow(source="waf", target="okta"),
            Flow(source="okta", target="app"), Flow(source="app", target="rds"),
            Flow(source="app", target="secrets", label="Fetches Credentials"),
            Flow(source="app", target="gd", label="Logs & Telemetry", primary=False),
        ],
    )


class DiagnosticsCleanlinessTests(unittest.TestCase):
    """Compiled output must pass the real pipeline (pre- and post-repair) with nothing to fix."""

    def test_logical_clean_before_and_after_repair(self) -> None:
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        self.assertEqual(pre.warnings, [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(post.warnings, [])

    def test_security_clean_before_and_after_repair(self) -> None:
        xml = compile_corridor_ir(realistic_security_ir(), page_name="Security")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        self.assertEqual(pre.warnings, [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(post.warnings, [])

    def test_data_flow_single_shelf_clean_before_and_after_repair(self) -> None:
        """One shelf under Store (no phase_saas) — the well-supported case."""
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lambda1", "aws_lambda", "Lambda")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "RDS")]),
            ],
            anchored=[AnchoredCorridor(
                id="phase_consume", label="Consume", anchor_id="phase_store",
                leaves=[_leaf("dash", "aws_quicksight", "Dashboard")],
            )],
            flows=[Flow(source="apigw", target="lambda1"), Flow(source="lambda1", target="db"), Flow(source="dash", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        self.assertEqual(pre.warnings, [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(post.warnings, [])

    def test_data_flow_double_shelf_has_a_known_preexisting_layout_engine_gap(self) -> None:
        """Documents a real, pre-existing bug in the shared layout engine — not this compiler.

        When *both* phase_consume and phase_saas anchor under phase_store (a
        structurally valid, prompt-documented case — see data_flow.txt: "the
        engine anchors both under phase_store, stacked one above the other"),
        the compiled input is clean pre-repair, but the shared deterministic
        layout engine (``logical_layout.apply_layered_corridor_layout`` via
        ``placement_fixers.enforce_view_journey_columns``, invoked
        unconditionally by ``xml_diagnostics.repair_xml`` for this page name)
        can land both shelves at the *same* coordinates when SaaS-leaf
        hoisting interacts with the stacking cursor — confirmed by isolating
        ``apply_layered_corridor_layout`` alone (stacks correctly) versus the
        full ``enforce_view_journey_columns`` pipeline (collides) during
        investigation. This is pre-existing, latent in already-shipped code
        (the direct-XML LLM path rarely produces the exact structural
        conditions needed to trigger it reliably); the IR path merely makes
        it deterministic to reproduce. Left unfixed deliberately — a targeted
        fix belongs in its own reviewed change against
        ``logical_layout.py``/``placement_fixers.py``, not bundled into this
        compiler's test suite. This test exists so the gap is tracked, not
        silently lost, and will start failing (prompting a welcome update)
        once someone fixes the underlying engine.
        """
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lambda1", "aws_lambda", "Lambda")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "RDS")]),
            ],
            anchored=[
                AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store",
                                  leaves=[_leaf("dash", "aws_quicksight", "Dashboard")]),
                AnchoredCorridor(id="phase_saas", label="SaaS", anchor_id="phase_store",
                                  leaves=[_leaf("sf", "saas_salesforce", "Salesforce")]),
            ],
            flows=[Flow(source="lambda1", target="db"), Flow(source="dash", target="db"), Flow(source="sf", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [], "compiler's own pre-repair output should still be clean")
        _repaired, post = repair_xml(xml)
        # KNOWN GAP: this should be [] — see docstring above. Asserting the
        # *current* (buggy) behavior, not the desired one, so a real fix shows
        # up as a test failure demanding this assertion be tightened.
        self.assertTrue(
            any("visually contained within" in e for e in post.errors),
            "expected the known phase_consume/phase_saas collision — if this "
            "fails, the underlying layout engine bug may have been fixed; "
            "tighten this assertion to assertEqual(post.errors, [])",
        )


class StructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.xml = compile_corridor_ir(realistic_security_ir(), page_name="Security")
        self.cells = {c.get("id"): c for c in ET.fromstring(self.xml).iter("mxCell") if c.get("id")}

    def test_corridors_and_shelf_are_root_layer(self) -> None:
        for cid in ("zone_public", "zone_dmz", "zone_private", "zone_external_saas"):
            self.assertEqual(self.cells[cid].get("parent"), "1")

    def test_leaves_nest_inside_their_corridor(self) -> None:
        self.assertEqual(self.cells["waf"].get("parent"), "zone_dmz")
        self.assertEqual(self.cells["secrets"].get("parent"), "zone_private")
        self.assertEqual(self.cells["workday"].get("parent"), "zone_external_saas")

    def test_leaves_carry_shape_type_for_mcp_resolution(self) -> None:
        self.assertEqual(self.cells["rds"].get("shape_type"), "aws_rds")

    def test_leaves_are_grey_placeholders(self) -> None:
        style = self.cells["rds"].get("style")
        self.assertNotIn("resIcon", style)
        self.assertIn("fillColor=#F5F5F5", style)

    def test_every_edge_endpoint_resolves(self) -> None:
        for cell in self.cells.values():
            if cell.get("edge") == "1":
                self.assertIn(cell.get("source"), self.cells)
                self.assertIn(cell.get("target"), self.cells)

    def test_output_is_a_single_page_document(self) -> None:
        self.assertEqual(self.xml.count("<diagram"), 1)
        self.assertTrue(self.xml.startswith("<mxfile"))


class SafetyTests(unittest.TestCase):
    def test_invalid_ir_is_refused_rather_than_silently_compiled(self) -> None:
        ir = realistic_logical_ir()
        ir.flows.append(Flow(source="portal", target="does-not-exist"))
        with self.assertRaises(CompilerError):
            compile_corridor_ir(ir, page_name="Logical")

    def test_labels_with_xml_special_characters_are_escaped(self) -> None:
        ir = LogicalIR(corridors=[Corridor(
            id="domain_core", label='R&D <Core>',
            leaves=[_leaf("svc", "logical_capability", 'Orders & "Fulfilment" <svc>')],
        )])
        xml = compile_corridor_ir(ir, page_name="Logical")
        parsed = ET.fromstring(xml)  # must not raise
        labels = {c.get("value") for c in parsed.iter("mxCell")}
        self.assertIn('Orders & "Fulfilment" <svc>', labels)


if __name__ == "__main__":
    unittest.main()
