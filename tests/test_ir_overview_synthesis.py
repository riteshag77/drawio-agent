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
from app.adapters.outbound.diagram.compiler import compile_network_ir
from app.domain.ir import Leaf, ExternalActor, Flow
from app.domain.ir.network import NetworkIR, CloudBoundary, Region, Vpc, AvailabilityZone, Subnet


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
