"""Tests for synthesize_ir_overview_body/ensure_ir_overview_panel — the
request-specific Overview Panel the IR compiler path needs since the
compiler itself never emits one (Gap A / Fix 3, see xml_pipeline.py)."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    ensure_ir_overview_panel,
    page_has_overview_panel,
    synthesize_ir_overview_body,
)
from app.adapters.outbound.diagram.compiler import compile_corridor_ir, compile_network_ir
from app.domain.ir import Corridor, Leaf, ExternalActor, Flow
from app.domain.ir.data_flow import DataFlowIR
from app.domain.ir.network import NetworkIR, CloudBoundary, Region, Vpc, AvailabilityZone, Subnet
from app.domain.ir.security import SecurityIR


def _leaf(i: str, st: str, label: str) -> Leaf:
    return Leaf(id=i, shape_type=st, label=label)


def _realistic_network_ir() -> NetworkIR:
    return NetworkIR(
        title="HR Employee Portal",
        cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc",
                zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                    Subnet(id="app", tier="app", leaves=[Leaf(id="portal", shape_type="aws_ecs", label="Portal App")]),
                    Subnet(id="data", tier="data", leaves=[Leaf(id="db", shape_type="aws_rds", label="Aurora")]),
                ])],
            )])],
        ),
        external_actors=[ExternalActor(id="users", kind="users", label="Employees")],
        flows=[
            Flow(source="users", target="portal", label="HTTPS"),
            Flow(source="portal", target="db", label="SQL"),
        ],
    )


class SynthesizeIrOverviewBodyTests(unittest.TestCase):
    def test_derives_steps_from_real_graph_labels(self) -> None:
        xml = compile_network_ir(_realistic_network_ir())
        body = synthesize_ir_overview_body(xml, "Network", "HR Employee Portal")
        self.assertIn("Employees", body)
        self.assertIn("Portal App", body)
        self.assertIn("Aurora", body)
        self.assertIn("HTTPS", body)
        self.assertIn("HR Employee Portal", body)

    def test_includes_lens_line_for_known_view(self) -> None:
        xml = compile_network_ir(_realistic_network_ir())
        body = synthesize_ir_overview_body(xml, "Network", "System")
        self.assertIn("Platform/SRE", body)

    def test_includes_footer(self) -> None:
        xml = compile_network_ir(_realistic_network_ir())
        body = synthesize_ir_overview_body(xml, "Network", "System")
        self.assertIn("Out of scope", body)

    def test_strips_annotation_and_saas_suffix_from_labels(self) -> None:
        ir = _realistic_network_ir()
        ir.cloud.regions[0].vpcs[0].zones[0].subnets[0].leaves[0].annotation = "Node.js"
        xml = compile_network_ir(ir)
        body = synthesize_ir_overview_body(xml, "Network", "System")
        # The bracketed annotation must not leak into the narrative prose.
        self.assertNotIn("[Node.js]", body)

    def test_falls_back_to_generic_stub_when_no_edges(self) -> None:
        ir = NetworkIR(
            cloud=CloudBoundary(
                id="cloud", label="AWS Cloud",
                regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                    id="vpc",
                    zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                        Subnet(id="app", tier="app", leaves=[Leaf(id="svc", shape_type="aws_ecs", label="Service")]),
                    ])],
                )])],
            ),
        )
        xml = compile_network_ir(ir)
        body = synthesize_ir_overview_body(xml, "Network", "System")
        # Generic stub content, not a crash and not an empty body.
        self.assertIn("Review the services", body)

    def test_unparseable_xml_falls_back_to_stub(self) -> None:
        body = synthesize_ir_overview_body("not xml at all <<<", "Logical", "System")
        self.assertIn("Logical architecture", body)

    def test_network_business_notes_render_as_a_business_impact_section(self) -> None:
        """The LLM's own business_notes bullets — stamped through compiled XML
        by CellWriter.set_business_notes, read back by _read_business_notes —
        must reach the Network panel. Network previously had no business
        section at all."""
        ir = _realistic_network_ir()
        ir.business_notes = [
            "The load balancer spreads traffic across app servers so a spike doesn't take the portal down.",
            "Employee records sit in a private data subnet with no direct path from the internet.",
        ]
        xml = compile_network_ir(ir)
        body = synthesize_ir_overview_body(xml, "Network", "System")
        self.assertIn("Business impact", body)
        self.assertIn("spreads traffic across app servers", body)
        self.assertIn("private data subnet", body)
        self.assertIn('data-section=&quot;biz&quot;', body)

    def test_network_has_no_business_section_when_ir_left_it_empty(self) -> None:
        """business_notes defaults to [] — Network must degrade cleanly to
        today's behavior (no Business section) rather than emitting an empty
        header, for any IR that doesn't populate it."""
        xml = compile_network_ir(_realistic_network_ir())
        body = synthesize_ir_overview_body(xml, "Network", "System")
        self.assertNotIn("Business impact", body)


class SecurityDataFlowOverviewNotesTests(unittest.TestCase):
    """Security/Data-flow don't author a flat business_notes list — the
    compiler builds it from named, independently-optional fields (see
    compiler/corridor.py's _overview_notes) so the panel always presents
    them in the same fixed order/labels regardless of what order the model
    filled them in."""

    def _security_ir(self, **overrides) -> SecurityIR:
        return SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("waf", "aws_waf", "WAF")]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app", "aws_ecs", "App")]),
            ],
            flows=[Flow(source="waf", target="app", label="TLS", protection="tls")],
            **overrides,
        )

    def test_security_overview_renders_all_populated_named_fields(self) -> None:
        ir = self._security_ir(
            purpose="Shows the public-to-private trust boundary for the claims API.",
            scope_included="IAM, network segmentation, WAF",
            scope_excluded="Physical security controls not shown — see facilities documentation.",
            threat_model_reference="STRIDE",
            data_classification="PII, PCI",
            compliance_frameworks="HIPAA, SOC2",
            control_types="authN/authZ, encryption in transit",
            key_assumptions="Assumes all internal traffic is mTLS-encrypted — not shown as separate edges.",
        )
        xml = compile_corridor_ir(ir, page_name="Security")
        body = synthesize_ir_overview_body(xml, "Security", "Claims Platform")
        self.assertIn("Security posture", body)
        self.assertIn("Purpose: Shows the public-to-private trust boundary", body)
        self.assertIn("Scope included: IAM, network segmentation, WAF", body)
        self.assertIn("Scope excluded: Physical security controls not shown", body)
        self.assertIn("Security zones: Public, Private", body)
        self.assertIn("Threat model: STRIDE", body)
        self.assertIn("Data classification: PII, PCI", body)
        self.assertIn("Compliance frameworks: HIPAA, SOC2", body)
        self.assertIn("Control types: authN/authZ", body)
        self.assertIn("Key assumptions: Assumes all internal traffic is mTLS", body)

    def test_security_zones_bullet_always_present_even_with_no_named_fields_filled(self) -> None:
        """Zones are structural fact derived from real corridors/anchored,
        not LLM-authored prose — must show regardless of whether the model
        filled in any of the judgment-call fields."""
        xml = compile_corridor_ir(self._security_ir(), page_name="Security")
        body = synthesize_ir_overview_body(xml, "Security", "System")
        self.assertIn("Security zones: Public, Private", body)

    def test_security_skips_blank_fields_without_empty_bullets(self) -> None:
        ir = self._security_ir(purpose="Shows the trust boundary.")
        xml = compile_corridor_ir(ir, page_name="Security")
        body = synthesize_ir_overview_body(xml, "Security", "System")
        self.assertNotIn("Threat model:", body)
        self.assertNotIn("Scope excluded:", body)

    def test_data_flow_overview_renders_populated_named_fields(self) -> None:
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("api", "aws_api_gateway", "API")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "DB")]),
            ],
            flows=[Flow(source="api", target="db", label="SQL")],
            data_classification="PHI, Financial",
            compliance_frameworks="NAIC, HIPAA",
            trust_zones_referenced="public, internal, restricted",
            key_assumptions="mTLS assumed between all internal services.",
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        body = synthesize_ir_overview_body(xml, "Data flow", "Claims Pipeline")
        self.assertIn("Data governance", body)
        self.assertIn("Data classification: PHI, Financial", body)
        self.assertIn("Compliance frameworks: NAIC, HIPAA", body)
        self.assertIn("Trust zones referenced: public, internal, restricted", body)
        self.assertIn("Key assumptions: mTLS assumed between all internal services.", body)


class EnsureIrOverviewPanelTests(unittest.TestCase):
    def test_injects_panel_when_missing(self) -> None:
        xml = compile_network_ir(_realistic_network_ir())
        self.assertFalse(page_has_overview_panel(xml))
        out = ensure_ir_overview_panel(xml, "Network", "HR Employee Portal")
        self.assertTrue(page_has_overview_panel(out))
        tree = ET.fromstring(out)
        panel = next(c for c in tree.iter("mxCell") if c.get("id") == "overview_panel")
        self.assertIn("Employees", panel.get("value") or "")

    def test_noop_when_panel_already_present(self) -> None:
        xml = compile_network_ir(_realistic_network_ir())
        with_panel = ensure_ir_overview_panel(xml, "Network", "System")
        again = ensure_ir_overview_panel(with_panel, "Network", "System")
        self.assertEqual(with_panel.count('id="overview_panel"'), again.count('id="overview_panel"'))
        self.assertEqual(again.count('id="overview_panel"'), 1)


if __name__ == "__main__":
    unittest.main()
