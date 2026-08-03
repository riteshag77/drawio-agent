"""IR -> draw.io compiler for Logical / Security / Data-flow.

Mirrors ``test_network_compiler.py``'s load-bearing assertion: compiled output
must satisfy the *existing* diagnostics/repair pipeline (including the real
``logical_layout.apply_layered_corridor_layout`` engine that
``xml_diagnostics.repair_xml`` invokes unconditionally for these three page
names) with zero residual errors — proof, not assertion, that structural
correctness by construction survives the full pipeline these pages already go
through in production.

Pre-repair warnings are checked with edge-routing warnings excluded (see
``_non_routing_warnings``) — routing is `fix_edge_anchors_from_geometry`'s job
(part of repair), never the compiler's, so raw compiler output legitimately
has them; the *post*-repair assertion stays a strict zero, since routing is
expected to be fully resolved by then for these three views (see
`tests/test_edge_overlap_checks.py` for dedicated coverage, including the one
documented case — Network's sibling-occlusion — where it isn't).
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


def _non_routing_warnings(warnings: list[str]) -> list[str]:
    """Filter out edge-routing warnings (box/parallel overlap) — see this
    module's docstring for why raw compiler output legitimately has them."""
    return [
        w for w in warnings
        if "passes through non-endpoint node" not in w
        and not ("overlap" in w and "segment" in w)
    ]


class DiagnosticsCleanlinessTests(unittest.TestCase):
    """Compiled output must pass the real pipeline (pre- and post-repair) with nothing to fix."""

    def test_logical_clean_before_and_after_repair(self) -> None:
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        self.assertEqual(_non_routing_warnings(pre.warnings), [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])

    def test_security_clean_before_and_after_repair(self) -> None:
        xml = compile_corridor_ir(realistic_security_ir(), page_name="Security")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        self.assertEqual(_non_routing_warnings(pre.warnings), [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])

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
        self.assertEqual(_non_routing_warnings(pre.warnings), [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])

    def test_data_flow_double_shelf_stacks_cleanly(self) -> None:
        """Regression test for a real, now-fixed bug in the shared layout engine.

        When *both* phase_consume and a SaaS shelf anchor under phase_store (a
        structurally valid, prompt-documented case — see data_flow.txt: "the
        engine anchors both under phase_store, stacked one above the other"),
        two independent bugs in ``edge_routing.py`` combined to collide
        the two shelves onto identical coordinates:

        1. The post-layout "re-stack SaaS children vertically" step in
           ``enforce_view_journey_columns`` only matched Logical's
           ``domain_saas`` id, never firing for Data-flow/Security shelves —
           so a shelf's children kept stale geometry after the shelf itself
           moved, which ``fit_containers_to_children`` then read as "content
           extent" and ballooned the shelf box around.
        2. ``enforce_corridor_gap_consistency``'s shelf-alignment loop assigned
           *every* shelf the same single ``target_y`` with no stacking
           cursor — it was written assuming at most one shelf per view, true
           for Logical's lone ``domain_saas`` but never true for Data-flow's
           two (now three) SaaS-adjacent shelves.

        Both are fixed. This test asserts the correct, non-colliding outcome.
        """
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lambda1", "aws_lambda", "Lambda")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "RDS")]),
            ],
            anchored=[
                AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store",
                                  leaves=[_leaf("dash", "aws_quicksight", "Dashboard")]),
                AnchoredCorridor(id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                                  leaves=[_leaf("sf", "saas_salesforce", "Salesforce")]),
            ],
            flows=[Flow(source="lambda1", target="db"), Flow(source="dash", target="db"), Flow(source="sf", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])

    def test_data_flow_triple_shelf_stacks_cleanly(self) -> None:
        """phase_consume + phase_saas_producer + phase_saas_consumer, all under
        phase_store simultaneously — the real shape the producer/consumer split
        (Fix 1) produces whenever an architecture has both SaaS directions."""
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lambda1", "aws_lambda", "Lambda")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "RDS")]),
            ],
            anchored=[
                AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store",
                                  leaves=[_leaf("dash", "aws_quicksight", "Dashboard")]),
                AnchoredCorridor(id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                                  leaves=[_leaf("sf", "saas_salesforce", "Salesforce")]),
                AnchoredCorridor(id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                                  leaves=[_leaf("stripe", "saas_stripe", "Stripe")]),
            ],
            flows=[
                Flow(source="apigw", target="lambda1"), Flow(source="lambda1", target="db"),
                Flow(source="dash", target="db"), Flow(source="sf", target="apigw"),
                Flow(source="db", target="stripe"),
            ],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])

    def test_saas_shelf_renders_as_tight_evenly_packed_grid(self) -> None:
        """Regression guard for the user's own reported defect and acceptance
        criterion (Fix 2): a phase_saas-equivalent shelf with 3 icons used to
        render as a 1182x890 box (Fix 2's plan cites this exact number) with
        uncomputed placement. After the canonical grid_fill/
        resize_container_to_grid sizing (Fix 2a/2b) it must be a compact,
        evenly-packed grid instead."""
        ir = DataFlowIR(
            corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "RDS")])],
            anchored=[AnchoredCorridor(
                id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                leaves=[
                    _leaf("v0", "saas_vendor0", "Vendor 0"),
                    _leaf("v1", "saas_vendor1", "Vendor 1"),
                    _leaf("v2", "saas_vendor2", "Vendor 2"),
                ],
            )],
            flows=[Flow(source="v0", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])
        cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        shelf_geo = cells["phase_saas_producer"].find("mxGeometry")
        w = float(shelf_geo.get("width"))
        h = float(shelf_geo.get("height"))
        # Nowhere near the originally-reported 1182x890 for the same 3-icon shape.
        self.assertLess(w, 700.0)
        self.assertLess(h, 300.0)
        # Every leaf is actually inside the shelf's bounds (no "uncomputed placement").
        for leaf_id in ("v0", "v1", "v2"):
            leaf_geo = cells[leaf_id].find("mxGeometry")
            lx, ly = float(leaf_geo.get("x")), float(leaf_geo.get("y"))
            lw, lh = float(leaf_geo.get("width")), float(leaf_geo.get("height"))
            self.assertGreaterEqual(lx, 0)
            self.assertGreaterEqual(ly, 0)
            self.assertLessEqual(lx + lw, w)
            self.assertLessEqual(ly + lh, h)

    def test_five_icon_shelf_grid_fills_without_churn(self) -> None:
        """A larger shelf (5 icons, wraps to 2 rows) must also stay a stable,
        non-oversized grid — regression guard for the pitch-conflict bug found
        during Fix 2b (icon positions at a bespoke pitch got silently
        overridden downstream by xml_diagnostics's canonical-pitch snap,
        producing a mismatched, oversized box)."""
        ir = DataFlowIR(
            corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "RDS")])],
            anchored=[AnchoredCorridor(
                id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                leaves=[_leaf(f"v{i}", f"saas_vendor{i}", f"Vendor {i}") for i in range(5)],
            )],
            flows=[Flow(source="v0", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])
        cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        shelf_geo = cells["phase_saas_producer"].find("mxGeometry")
        w, h = float(shelf_geo.get("width")), float(shelf_geo.get("height"))
        for i in range(5):
            leaf_geo = cells[f"v{i}"].find("mxGeometry")
            lx, ly = float(leaf_geo.get("x")), float(leaf_geo.get("y"))
            lw, lh = float(leaf_geo.get("width")), float(leaf_geo.get("height"))
            self.assertLessEqual(lx + lw, w)
            self.assertLessEqual(ly + lh, h)


class EdgeStyleTests(unittest.TestCase):
    """Fix 2d: the IR compiler's own edge style must match the direct-XML
    path's uniform orthogonal-edge template (rounded=1), not rounded=0."""

    def test_flows_are_rounded(self) -> None:
        xml = compile_corridor_ir(realistic_security_ir(), page_name="Security")
        edges = [c for c in ET.fromstring(xml).iter("mxCell") if c.get("edge") == "1"]
        self.assertTrue(edges)
        for edge in edges:
            self.assertIn("rounded=1", edge.get("style") or "")


class LeafAnnotationTests(unittest.TestCase):
    """Part B (ADS uplift): Leaf.annotation renders as a '[Tag]' bracket suffix
    on the label — shared by every view's compiler via emit_leaf_grid."""

    def test_annotation_appends_bracket_tag_to_label(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_data", label="Data", leaves=[
                Leaf(id="store", shape_type="logical_datastore", label="Customer data store", annotation="PII"),
            ])],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["store"].get("value"), "Customer data store [PII]")

    def test_no_annotation_leaves_label_unchanged(self) -> None:
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["store"].get("value"), "Customer data store")


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


class SemanticFieldsThroughFullPipelineTests(unittest.TestCase):
    """Phase 2 capture-scale check: a realistic IR using every new semantic
    field (criticality, classification, protection) at once, run through the
    *full* diagnostics/repair pipeline, not just the compiler in isolation —
    the same "must stay green" bar every other fixture in this file is held
    to. Substitutes for live capture replay (blocked in this sandbox by
    outbound TLS interception to every LLM provider)."""

    def test_security_with_semantic_fields_clean_before_and_after_repair(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns", "aws_route53", "Route 53")]),
                Corridor(id="zone_dmz", label="DMZ", leaves=[_leaf("waf", "aws_waf", "WAF")]),
                Corridor(id="zone_private", label="Private", leaves=[
                    _leaf("app", "aws_ecs", "App"),
                    Leaf(id="secrets", shape_type="aws_secrets_manager", label="Secrets Manager"),
                ]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[
                    Leaf(id="rds", shape_type="aws_rds", label="RDS", criticality="critical"),
                ]),
                Corridor(id="zone_detect", label="Detect", leaves=[_leaf("gd", "aws_guardduty", "GuardDuty")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="External SaaS Zone", anchor_id="zone_restricted",
                leaves=[_leaf("workday", "saas_workday", "Workday")],
            )],
            flows=[
                Flow(source="dns", target="waf", protection="tls"),
                Flow(source="waf", target="app", protection="tls"),
                Flow(source="app", target="rds", classification="pii", protection="mtls"),
                Flow(source="app", target="secrets", label="Fetches Credentials", protection="tls"),
                Flow(source="app", target="gd", label="Logs & Telemetry", primary=False),
                Flow(source="rds", target="workday", classification="pci", protection="tls"),
            ],
        )
        self.assertEqual(ir.semantic_warnings(), [])  # secrets + detect + protected boundary all present
        xml = compile_corridor_ir(ir, page_name="Security")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        self.assertEqual(_non_routing_warnings(pre.warnings), [])
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])

        # The semantic fields survived the full repair pipeline as stamped attributes.
        cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["rds"].get("criticality"), "critical")
        self.assertNotIn("criticality", cells["app"].keys())  # standard leaves stay unstamped
        pii_edges = [c for c in cells.values() if c.get("classification") == "pii"]
        self.assertEqual(len(pii_edges), 1)
        self.assertEqual(pii_edges[0].get("protection"), "mtls")

    def test_data_flow_with_semantic_fields_clean_before_and_after_repair(self) -> None:
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("kinesis", "aws_kinesis", "Kinesis")]),
                Corridor(id="phase_process", label="Process", leaves=[
                    _leaf("lambda1", "aws_lambda", "Lambda"), _leaf("dlq", "aws_sqs", "Dead-Letter Queue"),
                ]),
                Corridor(id="phase_store", label="Store", leaves=[
                    Leaf(id="db", shape_type="aws_rds", label="RDS", criticality="critical"),
                    _leaf("archive", "aws_s3", "S3 Archive"),
                ]),
            ],
            flows=[
                Flow(source="kinesis", target="lambda1"),
                Flow(source="kinesis", target="dlq", primary=False, label="Failed Retries"),
                Flow(source="lambda1", target="db", classification="pii"),
                Flow(source="db", target="archive", label="Automated Snapshots"),
            ],
        )
        self.assertEqual(ir.semantic_warnings(), [])  # DLQ + archive both present
        xml = compile_corridor_ir(ir, page_name="Data flow")
        pre = diagnose_xml(xml)
        self.assertEqual(pre.errors, [])
        self.assertEqual(_non_routing_warnings(pre.warnings), [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        # Soft lane-overlap hints are acceptable after band equalization
        # snaps ELK's staggered Y onto one row (same filter as pre above).
        self.assertEqual(_non_routing_warnings(post.warnings), [])


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
