"""Phase 2c: CorridorIR.semantic_warnings() — judgment-call rules promoted
from prompt-pack prose, and the non-blocking correction pass in
``agent._apply_semantic_warning_pass`` that consumes them.

Load-bearing distinction from ``semantic_errors()``: a warning must never stop
``compile_corridor_ir`` from succeeding, and the corrective pass must never
retry or reject — one best-effort ask, then proceed regardless.
"""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.core.services.agent import _apply_semantic_warning_pass
from app.domain.ir import AnchoredCorridor, Corridor, DataFlowIR, Flow, Leaf, LogicalIR, SecurityIR


def _leaf(i: str, label: str, **kw) -> Leaf:
    return Leaf(id=i, shape_type="aws_ec2", label=label, **kw)


class SecurityWarningsTests(unittest.TestCase):
    def test_saas_shelf_without_secrets_leaf_warns(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app", "App")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db", "Primary DB")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="SaaS", anchor_id="zone_restricted",
                leaves=[_leaf("workday", "Workday (SAAS)")],
            )],
            flows=[Flow(source="app", target="workday"), Flow(source="app", target="db")],
        )
        warnings = ir.semantic_warnings()
        self.assertTrue(any("secrets" in w for w in warnings))
        self.assertTrue(any("zone_detect" in w for w in warnings))
        # A warning must never block compilation.
        self.assertEqual(ir.semantic_errors(), [])
        compile_corridor_ir(ir, page_name="Security")  # must not raise

    def test_saas_shelf_with_secrets_and_detect_is_clean(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_private", label="Private", leaves=[
                    _leaf("app", "App"), _leaf("secrets", "Secrets Manager"),
                ]),
                Corridor(id="zone_detect", label="Detect", leaves=[_leaf("gd", "GuardDuty")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db", "Primary DB")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="SaaS", anchor_id="zone_restricted",
                leaves=[_leaf("workday", "Workday (SAAS)")],
            )],
            flows=[
                Flow(source="app", target="workday"), Flow(source="app", target="gd", primary=False),
                Flow(source="app", target="db"),
            ],
            purpose="Shows the private app tier's trust boundary and SaaS egress.",
        )
        self.assertEqual(ir.semantic_warnings(), [])

    def test_partner_zone_without_gateway_warns(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_partner", label="Partner", leaves=[_leaf("broker", "Broker Feed")]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app", "App")]),
            ],
            flows=[Flow(source="broker", target="app")],
        )
        self.assertTrue(any("Partner API Gateway" in w for w in ir.semantic_warnings()))

    def test_unprotected_boundary_flow_warns(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("waf", "WAF")]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app", "App")]),
            ],
            flows=[Flow(source="waf", target="app", protection="none")],
        )
        warnings = ir.semantic_warnings()
        self.assertTrue(any("crosses a public/partner boundary" in w for w in warnings))

    def test_protected_boundary_flow_is_clean(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("waf", "WAF")]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app", "App")]),
            ],
            flows=[Flow(source="waf", target="app", protection="tls")],
            purpose="Shows the public ingress path into the private app tier.",
        )
        self.assertEqual(ir.semantic_warnings(), [])


class DataFlowWarningsTests(unittest.TestCase):
    """Required-companions trigger: config/xml_gen/ir/data_flow.txt states
    ONE condition for all four companions (DLQ, backup, CDC replica, schema
    registry) — external SaaS present AND a primary store exists. Previously
    the code enforced two different, undocumented heuristics instead
    (streaming-keyword-in-ingest for DLQ, store-exists-at-all for backup) —
    fixed to match the documented trigger exactly (Data-flow correctness
    audit)."""

    def test_streaming_ingest_without_saas_does_not_warn_for_dlq(self) -> None:
        """Streaming ingest alone (no SaaS shelf) is not the documented
        trigger — this is the old bug's exact false-positive shape."""
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("kinesis", "Kinesis Stream")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "Primary DB")]),
            ],
            flows=[Flow(source="kinesis", target="db")],
        )
        self.assertFalse(any("dead-letter/DLQ" in w for w in ir.semantic_warnings()))

    def test_store_without_saas_does_not_warn_for_snapshot(self) -> None:
        """A bare phase_store with no SaaS shelf is not the documented
        trigger — the old bug padded every thin diagram with this warning."""
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_process", label="Process", leaves=[_leaf("etl", "ETL Job")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "Primary DB")]),
            ],
            flows=[Flow(source="etl", target="db")],
        )
        self.assertFalse(any("snapshot/archive/backup" in w for w in ir.semantic_warnings()))

    def test_saas_and_store_without_companions_warns_all_four(self) -> None:
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "API GW")]),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("lam", "Lambda")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "Primary DB")]),
            ],
            anchored=[AnchoredCorridor(
                id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                leaves=[_leaf("stripe", "Stripe")],
            )],
            flows=[Flow(source="apigw", target="lam"), Flow(source="lam", target="db"), Flow(source="db", target="stripe")],
        )
        warnings = ir.semantic_warnings()
        self.assertTrue(any("dead-letter/DLQ" in w for w in warnings))
        self.assertTrue(any("snapshot/archive/backup" in w for w in warnings))
        self.assertTrue(any("CDC/read-replica" in w for w in warnings))
        self.assertTrue(any("schema-registry" in w for w in warnings))

    def test_saas_and_store_with_all_companions_present_is_clean(self) -> None:
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("kinesis", "Kinesis Stream")]),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("dlq", "Dead-Letter Queue")]),
                Corridor(id="phase_store", label="Store", leaves=[
                    _leaf("db", "Primary DB"), _leaf("archive", "S3 Archive"), _leaf("replica", "Read Replica"),
                ]),
            ],
            anchored=[
                AnchoredCorridor(
                    id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                    leaves=[_leaf("stripe", "Stripe")],
                ),
                AnchoredCorridor(
                    id="phase_control", label="Control", anchor_id=None,
                    leaves=[_leaf("registry", "Schema Registry")],
                ),
            ],
            flows=[
                Flow(source="kinesis", target="dlq", primary=False), Flow(source="kinesis", target="db"),
                Flow(source="db", target="archive"), Flow(source="db", target="replica"),
                Flow(source="db", target="stripe"),
            ],
            data_classification="Financial",
            design_tradeoffs=["Kafka over polling: near-real-time ingest, worth the added operational complexity."],
        )
        self.assertEqual(ir.semantic_warnings(), [])


class LogicalWarningsTests(unittest.TestCase):
    def test_saas_shelf_without_cache_warns(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_core", label="Core", leaves=[_leaf("orgchart", "Org Chart")]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("store", "Customer data store")]),
            ],
            anchored=[AnchoredCorridor(
                id="domain_saas", label="SaaS", anchor_id="domain_data",
                leaves=[_leaf("workday", "Workday (SAAS)")],
            )],
            flows=[Flow(source="orgchart", target="workday"), Flow(source="orgchart", target="store")],
        )
        self.assertTrue(any("cache" in w for w in ir.semantic_warnings()))

    def test_no_saas_shelf_is_clean(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[_leaf("orgchart", "Org Chart")])],
            flows=[],
        )
        # flows is empty with only 1 leaf, which is allowed (semantic_errors requires 2+ leaves).
        self.assertEqual(ir.semantic_warnings(), [])


class SemanticWarningPassTests(unittest.TestCase):
    """Unit tests for the corrective pass itself, independent of any real LLM."""

    def _dirty_ir(self) -> LogicalIR:
        return LogicalIR(
            corridors=[
                Corridor(id="domain_core", label="Core", leaves=[_leaf("orgchart", "Org Chart")]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("store", "Customer data store")]),
            ],
            anchored=[AnchoredCorridor(
                id="domain_saas", label="SaaS", anchor_id="domain_data",
                leaves=[_leaf("workday", "Workday (SAAS)")],
            )],
            flows=[Flow(source="orgchart", target="workday"), Flow(source="orgchart", target="store")],
        )

    def test_clean_ir_skips_the_llm_entirely(self) -> None:
        clean = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[_leaf("orgchart", "Org Chart")])],
            flows=[],
        )
        calls = []
        result = _apply_semantic_warning_pass(
            clean, ir_type=LogicalIR, system_prompt="", human_content="", provider_key="claude",
            logger=None, attachment_documents=None,
            llm=lambda *a, **kw: calls.append(1) or clean, view_label="Logical",
        )
        self.assertEqual(calls, [])
        self.assertIs(result, clean)

    def test_dirty_ir_adopts_a_valid_correction(self) -> None:
        dirty = self._dirty_ir()
        fixed = LogicalIR(
            corridors=[
                Corridor(id="domain_core", label="Core", leaves=[
                    _leaf("orgchart", "Org Chart"), _leaf("cache", "Profile Cache"),
                ]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("store", "Customer data store")]),
            ],
            anchored=[AnchoredCorridor(
                id="domain_saas", label="SaaS", anchor_id="domain_data",
                leaves=[_leaf("workday", "Workday (SAAS)")],
            )],
            flows=[
                Flow(source="orgchart", target="workday"), Flow(source="orgchart", target="cache"),
                Flow(source="orgchart", target="store"),
            ],
        )
        result = _apply_semantic_warning_pass(
            dirty, ir_type=LogicalIR, system_prompt="", human_content="", provider_key="claude",
            logger=None, attachment_documents=None,
            llm=lambda *a, **kw: fixed, view_label="Logical",
        )
        self.assertIs(result, fixed)

    def test_correction_that_breaks_a_hard_rule_is_rejected(self) -> None:
        """A corrected IR that fails semantic_errors() must never be adopted —
        trading a soft warning for a hard failure is strictly worse."""
        dirty = self._dirty_ir()
        broken = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[_leaf("orgchart", "Org Chart")])],
            flows=[Flow(source="orgchart", target="nonexistent")],
        )
        result = _apply_semantic_warning_pass(
            dirty, ir_type=LogicalIR, system_prompt="", human_content="", provider_key="claude",
            logger=None, attachment_documents=None,
            llm=lambda *a, **kw: broken, view_label="Logical",
        )
        self.assertIs(result, dirty)

    def test_llm_failure_keeps_the_original(self) -> None:
        dirty = self._dirty_ir()

        def _boom(*_a, **_kw):
            raise RuntimeError("provider down")

        result = _apply_semantic_warning_pass(
            dirty, ir_type=LogicalIR, system_prompt="", human_content="", provider_key="claude",
            logger=None, attachment_documents=None, llm=_boom, view_label="Logical",
        )
        self.assertIs(result, dirty)


if __name__ == "__main__":
    unittest.main()
