"""Data-flow view battle-test coverage — the fixes from the Data-flow
correctness audit (phase_control/phase_store shelf-stack overlap, the actor
infra-noise safety net, the required-companions trigger fix, the new
design_tradeoffs nudge, and the shared corridor flows/external-actors check).
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, validate_layout
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


def _leaf(i: str, st: str = "aws_ecs", label: str = "Svc", **kwargs) -> Leaf:
    return Leaf(id=i, shape_type=st, label=label, **kwargs)


def _cells(xml: str) -> dict[str, ET.Element]:
    return {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}


def _geom(cell: ET.Element) -> tuple[float, float, float, float]:
    g = cell.find("mxGeometry")
    return (
        float(g.get("x") or 0), float(g.get("y") or 0),
        float(g.get("width") or 0), float(g.get("height") or 0),
    )


def _overlap_warnings(xml: str) -> list[str]:
    return [w for w in validate_layout(xml) if "overlap" in w and "sibling containers" in w]


class ShelfStackOverlapTests(unittest.TestCase):
    """1.1 — the headline fix: phase_control must never overlap the
    phase_store shelf stack."""

    def _base_corridors(self) -> list[Corridor]:
        return [
            Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
            Corridor(id="phase_process", label="Process", leaves=[_leaf("lam", "aws_lambda", "Lambda")]),
            Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "DB")]),
        ]

    def test_phase_control_does_not_overlap_full_store_shelf_stack(self) -> None:
        anchored = [
            AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store",
                              leaves=[_leaf("dash1"), _leaf("dash2")]),
            AnchoredCorridor(id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                              leaves=[_leaf("sf", "saas_salesforce", "Salesforce"), _leaf("wd", "saas_workday", "Workday")]),
            AnchoredCorridor(id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                              leaves=[_leaf("stripe", "saas_stripe", "Stripe"), _leaf("cb", "saas_chargebee", "Chargebee")]),
            AnchoredCorridor(id="phase_control", label="Control", anchor_id=None,
                              leaves=[_leaf(f"ctrl{i}", "aws_glue", f"Registry{i}") for i in range(8)]),
        ]
        ir = DataFlowIR(
            corridors=self._base_corridors(), anchored=anchored,
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        self.assertEqual(_overlap_warnings(xml), [])

        cells = _cells(xml)
        store_shelf_bottoms = [
            _geom(cells[cid])[1] + _geom(cells[cid])[3]
            for cid in ("phase_consume", "phase_saas_producer", "phase_saas_consumer")
        ]
        control_y = _geom(cells["phase_control"])[1]
        self.assertGreater(control_y, max(store_shelf_bottoms))

    def test_phase_control_below_main_row_only_when_no_store_shelves(self) -> None:
        """Regression: the fix must not move phase_control unnecessarily far
        down when there's nothing in phase_store's shelf stack to avoid."""
        ir = DataFlowIR(
            corridors=self._base_corridors(),
            anchored=[AnchoredCorridor(id="phase_control", label="Control", anchor_id=None, leaves=[_leaf("sched")])],
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        cells = _cells(xml)
        row_bottom = max(_geom(cells[cid])[1] + _geom(cells[cid])[3] for cid in ("phase_ingest", "phase_process", "phase_store"))
        control_y = _geom(cells["phase_control"])[1]
        # Should sit just below the main row (one INTER_CONTAINER_GAP), not
        # padded further down by a store-shelf stack that doesn't exist.
        self.assertLess(control_y, row_bottom + 200)
        self.assertGreater(control_y, row_bottom)

    def test_phase_control_clears_tallest_shelf_regardless_of_declaration_order(self) -> None:
        """Order-independence: phase_control must clear the tallest
        phase_store shelf, not the first one processed or an average."""
        anchored = [
            AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store", leaves=[_leaf("dash1")]),
            AnchoredCorridor(id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                              leaves=[_leaf(f"prod{i}", "saas_salesforce", f"Producer{i}") for i in range(6)]),
            AnchoredCorridor(id="phase_control", label="Control", anchor_id=None, leaves=[_leaf("sched")]),
        ]
        ir = DataFlowIR(
            corridors=self._base_corridors(), anchored=anchored,
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        self.assertEqual(_overlap_warnings(xml), [])
        cells = _cells(xml)
        tallest_bottom = max(
            _geom(cells[cid])[1] + _geom(cells[cid])[3] for cid in ("phase_consume", "phase_saas_producer")
        )
        self.assertGreater(_geom(cells["phase_control"])[1], tallest_bottom)


class ActorInfraNoiseSafetyNetTests(unittest.TestCase):
    """1.2 — actor infra-noise safety net + extended denylist."""

    def _raw(self, actor_label: str) -> dict:
        return {
            "view": "data_flow", "title": "Test",
            "corridors": [
                {"id": "phase_ingest", "label": "Ingest", "leaves": [{"id": "apigw", "shape_type": "aws_api_gateway", "label": "API GW"}]},
                {"id": "phase_process", "label": "Process", "leaves": [{"id": "lam", "shape_type": "aws_lambda", "label": "Lambda"}]},
                {"id": "phase_store", "label": "Store", "leaves": [{"id": "db", "shape_type": "aws_rds", "label": "DB"}]},
            ],
            "external_actors": [
                {"id": "actor1", "kind": "users", "label": actor_label},
                {"id": "user1", "kind": "users", "label": "End User"},
            ],
            "flows": [
                {"source": "user1", "target": "apigw"}, {"source": "apigw", "target": "lam"}, {"source": "lam", "target": "db"},
            ],
        }

    def test_infra_noise_actor_is_dropped(self) -> None:
        for label in ("CloudTrail", "NAT Gateway", "CloudFront", "Route 53"):
            with self.subTest(label=label):
                ir = DataFlowIR.model_validate(self._raw(label))
                self.assertEqual([a.label for a in ir.external_actors], ["End User"])

    def test_non_infra_actor_is_left_alone(self) -> None:
        ir = DataFlowIR.model_validate(self._raw("Salesforce"))
        self.assertEqual(
            sorted(a.label for a in ir.external_actors), sorted(["Salesforce", "End User"]),
        )

    def test_extended_denylist_strips_network_plumbing_leaves(self) -> None:
        data = {
            "view": "data_flow", "title": "Test",
            "corridors": [
                {"id": "phase_ingest", "label": "Ingest", "leaves": [
                    {"id": "apigw", "shape_type": "aws_api_gateway", "label": "API GW"},
                    {"id": "alb1", "shape_type": "aws_alb", "label": "Application Load Balancer"},
                    {"id": "cf1", "shape_type": "aws_cloudfront", "label": "CloudFront"},
                ]},
                {"id": "phase_process", "label": "Process", "leaves": [{"id": "lam", "shape_type": "aws_lambda", "label": "Lambda"}]},
                {"id": "phase_store", "label": "Store", "leaves": [{"id": "db", "shape_type": "aws_rds", "label": "DB"}]},
            ],
            "flows": [{"source": "apigw", "target": "lam"}, {"source": "lam", "target": "db"}],
        }
        ir = DataFlowIR.model_validate(data)
        leaf_ids = {leaf.id for c in ir.corridors for leaf in c.leaves}
        self.assertEqual(leaf_ids, {"apigw", "lam", "db"})


class RequiredCompanionsTests(unittest.TestCase):
    """1.3 — nudges must match the single documented SaaS+store trigger."""

    def _corridors(self, *, with_companions: bool) -> list[Corridor]:
        process_leaves = [_leaf("lam")]
        store_leaves = [_leaf("db")]
        if with_companions:
            process_leaves.append(_leaf("dlq", "aws_sqs", "Dead-Letter Queue"))
            store_leaves.extend([_leaf("archive", "aws_s3", "S3 Archive"), _leaf("replica", "aws_rds", "Read Replica")])
        return [
            Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
            Corridor(id="phase_process", label="Process", leaves=process_leaves),
            Corridor(id="phase_store", label="Store", leaves=store_leaves),
        ]

    def test_saas_and_store_with_no_companions_warns_all_four(self) -> None:
        ir = DataFlowIR(
            corridors=self._corridors(with_companions=False),
            anchored=[AnchoredCorridor(id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                                        leaves=[_leaf("stripe", "saas_stripe", "Stripe")])],
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db"), Flow(source="db", target="stripe")],
        )
        warnings = ir.semantic_warnings()
        self.assertTrue(any("DLQ" in w for w in warnings), warnings)
        self.assertTrue(any("archive" in w for w in warnings), warnings)
        self.assertTrue(any("replica" in w for w in warnings), warnings)
        self.assertTrue(any("schema-registry" in w for w in warnings), warnings)

    def test_saas_and_store_with_all_companions_present_stays_silent(self) -> None:
        ir = DataFlowIR(
            corridors=self._corridors(with_companions=True),
            anchored=[
                AnchoredCorridor(id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                                  leaves=[_leaf("stripe", "saas_stripe", "Stripe")]),
                AnchoredCorridor(id="phase_control", label="Control", anchor_id=None,
                                  leaves=[_leaf("registry", "aws_glue", "Schema Registry")]),
            ],
            flows=[
                Flow(source="apigw", target="lam"), Flow(source="lam", target="dlq"),
                Flow(source="lam", target="db"), Flow(source="db", target="archive"),
                Flow(source="db", target="replica"), Flow(source="db", target="stripe"),
            ],
        )
        companion_warnings = [w for w in ir.semantic_warnings() if "required-companions" in w]
        self.assertEqual(companion_warnings, [])

    def test_store_only_no_saas_stays_sparse_anti_padding_regression(self) -> None:
        """The old bug's exact false-positive shape: a bare phase_store with
        no SaaS shelf must never get pushed toward an unwanted archive leaf."""
        ir = DataFlowIR(
            corridors=self._corridors(with_companions=False),
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db")],
        )
        companion_warnings = [w for w in ir.semantic_warnings() if "required-companions" in w]
        self.assertEqual(companion_warnings, [])


class DesignTradeoffsNudgeTests(unittest.TestCase):
    """1.4 — design_tradeoffs nudge for Data-flow."""

    def _ir(self, *, design_tradeoffs: list[str]) -> DataFlowIR:
        return DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw"), _leaf("apigw2")]),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lam")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db")]),
            ],
            flows=[Flow(source="apigw", target="lam"), Flow(source="apigw2", target="lam"), Flow(source="lam", target="db")],
            design_tradeoffs=design_tradeoffs,
        )

    def test_empty_design_tradeoffs_warns_at_leaf_threshold(self) -> None:
        warnings = self._ir(design_tradeoffs=[]).semantic_warnings()
        self.assertTrue(any("design_tradeoffs is empty" in w for w in warnings), warnings)

    def test_populated_design_tradeoffs_does_not_warn(self) -> None:
        warnings = self._ir(design_tradeoffs=["SQS over direct calls: decouples services."]).semantic_warnings()
        self.assertFalse(any("design_tradeoffs is empty" in w for w in warnings), warnings)


class SharedFlowsActorsCheckTests(unittest.TestCase):
    """1.5 — CorridorIR.semantic_errors() now counts external_actors too,
    across all three corridor views."""

    def test_one_leaf_two_actors_zero_flows_errors_on_all_three_views(self) -> None:
        common_kwargs = dict(
            external_actors=[
                ExternalActor(id="u1", kind="users", label="User1"),
                ExternalActor(id="u2", kind="users", label="User2"),
            ],
            flows=[],
        )
        data_flow_ir = DataFlowIR(
            corridors=[Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("a1")])], **common_kwargs,
        )
        logical_ir = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[_leaf("a1")])], **common_kwargs,
        )
        security_ir = SecurityIR(
            corridors=[Corridor(id="zone_public", label="Public", leaves=[_leaf("a1")])], **common_kwargs,
        )
        for ir in (data_flow_ir, logical_ir, security_ir):
            with self.subTest(view=ir.view):
                errors = ir.semantic_errors()
                self.assertTrue(any("flows is empty" in e for e in errors), errors)

    def test_below_both_thresholds_does_not_error(self) -> None:
        ir = DataFlowIR(corridors=[Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("a1")])], flows=[])
        self.assertFalse(any("flows is empty" in e for e in ir.semantic_errors()))


class EnterpriseScaleDensityTests(unittest.TestCase):
    """3.1 #9 — all 4 shelves at realistic enterprise leaf counts."""

    def test_all_four_shelves_at_realistic_scale_compiles_clean(self) -> None:
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
                Corridor(id="phase_process", label="Process", leaves=[
                    _leaf("lam1", "aws_lambda", "Order Service"), _leaf("lam2", "aws_lambda", "Billing Service"),
                    _leaf("dlq", "aws_sqs", "Dead-Letter Queue"),
                ]),
                Corridor(id="phase_store", label="Store", leaves=[
                    _leaf("db", "aws_rds", "Orders DB"), _leaf("archive", "aws_s3", "S3 Archive"),
                    _leaf("replica", "aws_rds", "Read Replica"),
                ]),
            ],
            anchored=[
                AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store",
                                  leaves=[_leaf("dash1", "aws_quicksight", "Sales Dashboard"), _leaf("dash2", "aws_quicksight", "Ops Dashboard")]),
                AnchoredCorridor(id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                                  leaves=[_leaf("sf", "saas_salesforce", "Salesforce"), _leaf("wd", "saas_workday", "Workday"), _leaf("gw", "saas_guidewire", "Guidewire")]),
                AnchoredCorridor(id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                                  leaves=[_leaf("stripe", "saas_stripe", "Stripe"), _leaf("cb", "saas_chargebee", "Chargebee")]),
                AnchoredCorridor(id="phase_control", label="Control", anchor_id=None,
                                  leaves=[_leaf("registry", "aws_glue", "Schema Registry"), _leaf("catalog", "aws_glue", "Data Catalog"),
                                          _leaf("sched", "aws_eventbridge", "Scheduler")]),
            ],
            flows=[
                Flow(source="apigw", target="lam1"), Flow(source="apigw", target="lam2"),
                Flow(source="lam1", target="dlq", primary=False), Flow(source="lam1", target="db"),
                Flow(source="lam2", target="db"), Flow(source="db", target="archive"),
                Flow(source="db", target="replica"), Flow(source="dash1", target="db"),
                Flow(source="dash2", target="db"), Flow(source="sf", target="apigw"),
                Flow(source="wd", target="apigw"), Flow(source="gw", target="apigw"),
                Flow(source="db", target="stripe"), Flow(source="db", target="cb"),
            ],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(_overlap_warnings(xml), [])


class OverviewPanelFieldsRenderTests(unittest.TestCase):
    """3.1 #10 — all four overview-panel fields render for Data-flow.

    The compiler stamps these as "Label: text" bullets into the page root's
    ``business_notes`` JSON attribute (see corridor.py::_overview_notes) —
    the ``overview_panel`` cell itself is synthesized from that attribute by
    a later pipeline step (``refresh_ir_overview_panel``), not by the raw
    compiler, so this checks the attribute the compiler is actually
    responsible for.
    """

    def test_all_overview_fields_render_on_compiled_page(self) -> None:
        import json

        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lam")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db")]),
            ],
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db")],
            data_classification="PII, Financial",
            compliance_frameworks="SOC2, GDPR",
            trust_zones_referenced="public, internal",
            key_assumptions="mTLS assumed between all internal services",
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        cells = _cells(xml)
        root = cells["1"]
        notes = json.loads(root.get("business_notes"))
        joined = " | ".join(notes)
        for expected in ("PII, Financial", "SOC2, GDPR", "public, internal", "mTLS assumed"):
            self.assertIn(expected, joined)


class CriticalityPreservationTests(unittest.TestCase):
    """3.1 #12 — a critical store leaf's red border survives
    assign_saas_shape_types (mirrors Network's/Logical's own regression)."""

    def test_critical_store_leaf_survives_assign_saas_shape_types(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import assign_saas_shape_types

        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lam")]),
                Corridor(id="phase_store", label="Store", leaves=[
                    _leaf("db", "logical_saas", "Critical Vendor DB", criticality="critical"),
                ]),
            ],
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db")],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        rewritten = assign_saas_shape_types(xml)
        cells = _cells(rewritten)
        self.assertEqual(cells["db"].get("criticality"), "critical")
        self.assertIn("B85450", cells["db"].get("style"))


if __name__ == "__main__":
    unittest.main()
