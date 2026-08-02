"""Key-facts line in the IR-synthesized overview panel.

Load-bearing property: every number in the line must be recomputable from the
compiled graph — never invented — since this is prose a reader will trust at
face value. Covers both the original topology counts (corridor/service/flow)
and the Phase 2 semantic counts (business-critical services, TLS/mTLS
coverage, PII/PCI-carrying flows) derived from the ``criticality``/
``protection``/``classification`` attributes stamped on the compiled XML.
"""

from __future__ import annotations

import re
import unittest

from app.adapters.outbound.diagram.architecture_docs import synthesize_ir_overview_body
from app.adapters.outbound.diagram.compiler import compile_corridor_ir, compile_network_ir
from app.domain.ir import (
    AnchoredCorridor,
    AvailabilityZone,
    CloudBoundary,
    Corridor,
    Flow,
    Leaf,
    NetworkIR,
    Region,
    SecurityIR,
    Subnet,
    Vpc,
)


def _leaf(i: str) -> Leaf:
    return Leaf(id=i, shape_type="aws_ecs", label=i.title())


def _facts_line(body: str) -> str | None:
    match = re.search(r'color:#555555;margin-top:6px;&quot;&gt;(.*?)&lt;/div&gt;', body)
    return match.group(1) if match else None


class KeyFactsCountsTests(unittest.TestCase):
    def _security_xml(self) -> str:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns"), _leaf("cdn")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="SaaS", anchor_id="zone_restricted", leaves=[_leaf("workday")],
            )],
            flows=[Flow(source="dns", target="cdn"), Flow(source="cdn", target="rds"),
                   Flow(source="rds", target="workday")],
        )
        return compile_corridor_ir(ir, page_name="Security")

    def test_counts_are_recomputable_from_the_graph(self) -> None:
        body = synthesize_ir_overview_body(self._security_xml(), "Security", "Test")
        facts = _facts_line(body)
        self.assertIsNotNone(facts)
        # 2 row zones + 1 shelf = 3 corridors, 1 of them a shelf; 4 leaves; 3 flows.
        self.assertIn("3 zones", facts)
        self.assertIn("1 shelf", facts)
        self.assertIn("4 services", facts)
        self.assertIn("3 flows", facts)

    def test_singular_wording(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds")]),
            ],
            flows=[Flow(source="dns", target="rds")],
        )
        body = synthesize_ir_overview_body(compile_corridor_ir(ir, page_name="Security"), "Security", "Test")
        facts = _facts_line(body)
        self.assertIsNotNone(facts)
        self.assertIn("2 zones", facts)  # corridor count itself is plural
        self.assertIn("2 services", facts)
        self.assertIn("1 flow", facts)
        self.assertNotIn("1 flows", facts)
        self.assertNotIn("shelf", facts)  # no anchored corridor at all

    def test_semantic_clauses_appear_when_present(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[
                    Leaf(id="rds", shape_type="aws_rds", label="Rds", criticality="critical"),
                ]),
            ],
            flows=[
                Flow(source="dns", target="rds", classification="pii", protection="tls"),
            ],
        )
        body = synthesize_ir_overview_body(compile_corridor_ir(ir, page_name="Security"), "Security", "Test")
        facts = _facts_line(body)
        self.assertIsNotNone(facts)
        self.assertIn("1 business-critical service", facts)
        self.assertIn("1/1 flows protected (TLS/mTLS)", facts)
        self.assertIn("1 flow carrying PII/PCI", facts)

    def test_semantic_clauses_absent_when_all_standard(self) -> None:
        """No critical leaves, no classified/protected flows — the line stays
        exactly the original three clauses, no empty/zero clauses appended."""
        body = synthesize_ir_overview_body(self._security_xml(), "Security", "Test")
        facts = _facts_line(body)
        self.assertIsNotNone(facts)
        self.assertNotIn("business-critical", facts)
        self.assertNotIn("protected", facts)
        self.assertNotIn("PII/PCI", facts)

    def test_stub_fallback_has_no_facts_line(self) -> None:
        """No edges means no story steps, so the generic static stub is used
        instead — the facts line only ever appears alongside a real
        synthesized body, never as a bare add-on to the stub."""
        body = synthesize_ir_overview_body(compile_corridor_ir_or_stub(None), "Security", "Test")
        self.assertIsNone(_facts_line(body))

    def test_network_view_has_no_facts_line_yet(self) -> None:
        """Network isn't a corridor view (no domain_/zone_/phase_ prefix) —
        the facts line is corridor-only until Network gets its own topology
        summary, which is out of scope here."""
        vpc = Vpc(
            id="vpc", zones=[AvailabilityZone(id="az1", label="AZ-1", subnets=[
                Subnet(id="app1", tier="app", leaves=[_leaf("ecs")]),
            ])],
            gateway=_leaf("alb"),
        )
        cloud = CloudBoundary(id="cloud", label="AWS", regions=[Region(id="region", label="us-east-1", vpcs=[vpc])])
        ir = NetworkIR(cloud=cloud, flows=[Flow(source="alb", target="ecs")])
        xml = compile_network_ir(ir, page_name="Network")
        body = synthesize_ir_overview_body(xml, "Network", "Test")
        self.assertIsNone(_facts_line(body))


def compile_corridor_ir_or_stub(ir):
    """SecurityIR with zero flows fails semantic validation before it ever
    reaches the compiler (flows is empty but there are leaves) — build the
    minimal not-yet-a-real-page XML the stub path actually sees instead."""
    return "<mxGraphModel><root><mxCell id=\"0\"/><mxCell id=\"1\" parent=\"0\"/></root></mxGraphModel>"


if __name__ == "__main__":
    unittest.main()
