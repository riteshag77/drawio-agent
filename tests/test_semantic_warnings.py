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
            design_tradeoffs=["Secrets Manager over env vars: credentials rotate without a redeploy."],
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

    def test_partner_zone_with_gateway_does_not_warn(self) -> None:
        """The positive case above had no negative counterpart, so a broken
        "gateway" needle would still have passed green."""
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_partner", label="Partner", leaves=[
                    _leaf("pgw", "Partner API Gateway"), _leaf("broker", "Broker Feed"),
                ]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app", "App")]),
            ],
            flows=[Flow(source="pgw", target="app", label="Scoped Partner API", protection="mtls")],
        )
        self.assertFalse([w for w in ir.semantic_warnings() if "Partner API Gateway" in w])

    def test_blank_purpose_warns_and_filled_purpose_does_not(self) -> None:
        """The purpose nudge was only ever exercised incidentally, by other
        fixtures setting the field to keep their own assertions clean."""
        def _ir(purpose: str) -> SecurityIR:
            return SecurityIR(
                corridors=[Corridor(id="zone_private", label="Private", leaves=[
                    _leaf("app", "App"), _leaf("db", "DB"),
                ])],
                flows=[Flow(source="app", target="db")],
                purpose=purpose,
            )
        self.assertTrue([w for w in _ir("").semantic_warnings() if "purpose is empty" in w])
        self.assertFalse([w for w in _ir("Shows the app-to-data trust boundary.").semantic_warnings()
                          if "purpose is empty" in w])

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


class NarrativeSchemaTests(unittest.TestCase):
    """The Overview panel's narrative is *asked for* in the schema, not just
    the prose.

    A field with a Python default is never in a JSON schema's ``required``
    list, and models skip what they are not asked for: across the stored
    corpus ``design_tradeoffs`` was empty on 264 views, ``business_notes`` on
    145 and ``purpose`` on 68, none of which the prompt or the corrective pass
    shifted. The two halves below are the whole fix — ask in the schema, but
    keep the Python default so a model that still declines cannot cost a page.
    """

    def test_every_view_asks_for_its_narrative(self) -> None:
        from app.domain.ir import ArchimateIR, DataFlowIR, NetworkIR

        for ir_type, expected in (
            (LogicalIR, {"business_notes", "design_tradeoffs"}),
            (DataFlowIR, {"business_notes", "design_tradeoffs"}),
            (NetworkIR, {"business_notes", "design_tradeoffs"}),
            (SecurityIR, {"business_notes", "design_tradeoffs", "purpose"}),
            (ArchimateIR, {"design_tradeoffs"}),
        ):
            with self.subTest(ir=ir_type.__name__):
                required = set(ir_type.model_json_schema().get("required", []))
                self.assertTrue(expected <= required, f"missing {expected - required}")

    def test_a_model_that_still_omits_them_does_not_lose_its_page(self) -> None:
        """Required-in-schema is a request, not a gate — the default remains."""
        ir = LogicalIR.model_validate({
            "view": "logical",
            "corridors": [{"id": "domain_core", "label": "C", "leaves": [
                {"id": "a", "shape_type": "aws_ecs", "label": "A"},
                {"id": "b", "shape_type": "aws_rds", "label": "B"},
            ]}],
            "flows": [{"source": "a", "target": "b"}],
        })
        self.assertEqual(ir.business_notes, [])
        self.assertEqual(ir.design_tradeoffs, [])
        self.assertEqual(ir.semantic_errors(), [], "narrative must never be a hard failure")

    def test_the_never_ask_fields_stay_optional(self) -> None:
        """"Leave empty if none" fields must not be demanded — asking invites
        the literal "N/A" their own descriptions forbid."""
        from app.domain.ir import DataFlowIR

        required = set(DataFlowIR.model_json_schema().get("required", []))
        self.assertNotIn("data_classification", required)
        self.assertNotIn("compliance_frameworks", required)


class DataClassificationDerivationTests(unittest.TestCase):
    """The panel's data classification is summarised from the flows.

    The model marks each flow as it draws it and then routinely leaves the
    summary field blank, because an optional field is never in the schema's
    required set. That produced diagrams with PII-labelled edges above an
    Overview panel silent about data sensitivity — the fact on the page,
    missing from the summary of the page. 63 of 73 such views in the stored
    corpus had the answer in their own flows.
    """

    def _ir(self, classifications, declared=""):
        leaves = [_leaf(f"n{i}", f"Node {i}") for i in range(len(classifications) + 1)]
        return DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=leaves[:1]),
                Corridor(id="phase_store", label="Store", leaves=leaves[1:]),
            ],
            flows=[
                Flow(source="n0", target=f"n{i + 1}", classification=c)
                for i, c in enumerate(classifications)
            ],
            data_classification=declared,
        )

    def test_pii_on_a_flow_reaches_the_panel(self) -> None:
        self.assertEqual(self._ir(["pii"]).data_classification, "PII")

    def test_several_classes_are_listed_most_sensitive_first(self) -> None:
        ir = self._ir(["internal", "pii", "pci"])
        self.assertEqual(ir.data_classification, "PCI, PII, Internal")

    def test_a_declared_value_is_never_overwritten(self) -> None:
        """The model can name what the flows cannot express."""
        ir = self._ir(["pii"], declared="PHI, Actuarial")
        self.assertEqual(ir.data_classification, "PHI, Actuarial")

    def test_unclassified_flows_derive_nothing(self) -> None:
        ir = self._ir(["none", "none"])
        self.assertEqual(ir.data_classification, "")
        self.assertTrue(any("data_classification" in w for w in ir.semantic_warnings()))

    def test_deriving_silences_the_warning_that_asked_for_it(self) -> None:
        ir = self._ir(["pii"])
        self.assertFalse(any("data_classification" in w for w in ir.semantic_warnings()))

    def test_the_derived_value_reaches_the_rendered_panel(self) -> None:
        xml = compile_corridor_ir(self._ir(["pii"]), page_name="Data flow")
        self.assertIn("PII", xml)


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

    def test_a_narrative_only_gap_does_not_buy_a_round_trip(self) -> None:
        """The schema already asks for these; asking again in prose is what
        measurably did not work, on 82% of views, one provider call each."""
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_core", label="Core", leaves=[
                    _leaf("a", "A"), _leaf("b", "B"), _leaf("c", "C"), _leaf("d", "D"),
                ]),
            ],
            flows=[Flow(source="a", target="b"), Flow(source="b", target="c"),
                   Flow(source="c", target="d")],
        )
        self.assertTrue(ir.semantic_warnings(), "precondition: it does warn")
        self.assertTrue(all("empty" in w for w in ir.semantic_warnings()))

        calls = []
        result = _apply_semantic_warning_pass(
            ir, ir_type=LogicalIR, system_prompt="", human_content="", provider_key="claude",
            logger=None, attachment_documents=None,
            llm=lambda *a, **kw: calls.append(1) or ir, view_label="Logical",
        )
        self.assertEqual(calls, [], "no provider call for a missing sentence")
        self.assertIs(result, ir)

    def test_a_structural_warning_still_gets_its_ask(self) -> None:
        """"You are missing a cache" names something the model can add."""
        dirty = self._dirty_ir()
        self.assertTrue(any("cache" in w for w in dirty.semantic_warnings()))
        calls = []
        _apply_semantic_warning_pass(
            dirty, ir_type=LogicalIR, system_prompt="", human_content="", provider_key="claude",
            logger=None, attachment_documents=None,
            llm=lambda *a, **kw: calls.append(1) or dirty, view_label="Logical",
        )
        self.assertEqual(len(calls), 1)

    def test_a_correction_may_not_delete_the_users_components(self) -> None:
        """Adding is the point of this pass; deleting never is.

        The prompt says "keep everything else identical" and the model is free
        to ignore it — the only signal it gets back is a list of criticisms, so
        a run that drops a service to satisfy one looks exactly like a run that
        fixes it. Worth guarding because the trigger is usually trivial: 82% of
        the corpus's views enter this pass solely over an empty narrative
        field, re-rolling the whole drawing to chase a missing sentence.
        """
        dirty = self._dirty_ir()
        lost_the_saas = LogicalIR(
            corridors=[
                Corridor(id="domain_core", label="Core", leaves=[
                    _leaf("orgchart", "Org Chart"), _leaf("cache", "Profile Cache"),
                ]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("store", "Customer data store")]),
            ],
            flows=[Flow(source="orgchart", target="cache"), Flow(source="cache", target="store")],
            business_notes=["Staff look up colleagues; the cache keeps it quick."],
        )
        self.assertEqual(lost_the_saas.semantic_errors(), [], "precondition: otherwise valid")

        result = _apply_semantic_warning_pass(
            dirty, ir_type=LogicalIR, system_prompt="", human_content="", provider_key="claude",
            logger=None, attachment_documents=None,
            llm=lambda *a, **kw: lost_the_saas, view_label="Logical",
        )
        self.assertIs(result, dirty, "Workday vanished — the correction must be refused")

    def test_a_correction_that_only_adds_is_still_adopted(self) -> None:
        """The guard must not block the pass from doing its job."""
        dirty = self._dirty_ir()
        added = LogicalIR(
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
            llm=lambda *a, **kw: added, view_label="Logical",
        )
        self.assertIs(result, added)


if __name__ == "__main__":
    unittest.main()
