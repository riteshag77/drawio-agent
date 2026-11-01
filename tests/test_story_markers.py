"""Numbered step badges that match the overview panel's own numbering.

Named regression: the IR path always sent ``step_markers=[]``
(``agent.py``'s ``generate_xml_diagram_from_ir``), so ``synthesize_ir_
overview_body`` numbered a prose story ("1. Users → Okta...") in the panel
while nothing on the canvas ever carried those numbers — a broken cross-
reference between the one part of the page telling the reader "look at step
3" and the diagram that had no step 3 marked. ``derive_story_markers`` and
``synthesize_ir_overview_body`` now read the same ``_ir_story_steps``
extraction over the same xml snapshot, so panel step *i* and canvas badge
*i* are guaranteed to name the same cell — that guarantee is what these
tests exist to pin.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    derive_story_markers,
    ensure_ir_overview_panel,
    synthesize_ir_overview_body,
)
from app.adapters.outbound.diagram.compiler import compile_corridor_ir, compile_network_ir
from app.adapters.outbound.diagram.xml_diagnostics import apply_step_markers
from app.domain.ir import (
    AvailabilityZone,
    CloudBoundary,
    Corridor,
    DataFlowIR,
    ExternalActor,
    Flow,
    Leaf,
    LogicalIR,
    NetworkIR,
    Region,
    SecurityIR,
    Subnet,
    Vpc,
)


def _leaf(i: str, label: str) -> Leaf:
    return Leaf(id=i, shape_type="aws_lambda", label=label)


def _security_ir() -> SecurityIR:
    return SecurityIR(
        corridors=[
            Corridor(id="zone_public", label="Public", leaves=[_leaf("dns", "DNS")]),
            Corridor(id="zone_dmz", label="DMZ", leaves=[_leaf("waf", "WAF")]),
            Corridor(id="zone_identity", label="Identity", leaves=[_leaf("okta", "Okta")]),
            Corridor(id="zone_private", label="Private", leaves=[_leaf("app", "App")]),
            Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds", "RDS")]),
        ],
        flows=[
            Flow(source="dns", target="waf"), Flow(source="waf", target="okta"),
            Flow(source="okta", target="app"), Flow(source="app", target="rds"),
        ],
    )


def _logical_ir() -> LogicalIR:
    return LogicalIR(
        corridors=[
            Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "Portal")]),
            Corridor(id="domain_core", label="Core", leaves=[_leaf("orders", "Orders")]),
            Corridor(id="domain_data", label="Data", leaves=[_leaf("store", "Store")]),
        ],
        external_actors=[ExternalActor(id="persona", kind="users", label="Employee")],
        flows=[Flow(source="persona", target="portal"), Flow(source="portal", target="orders"),
               Flow(source="orders", target="store")],
    )


def _data_flow_ir() -> DataFlowIR:
    return DataFlowIR(
        corridors=[
            Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("api", "API")]),
            Corridor(id="phase_process", label="Process", leaves=[_leaf("fn", "Function")]),
            Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "DB")]),
        ],
        flows=[Flow(source="api", target="fn"), Flow(source="fn", target="db")],
    )


def _network_ir() -> NetworkIR:
    vpc = Vpc(
        id="vpc", zones=[AvailabilityZone(id="az1", label="AZ-1", subnets=[
            Subnet(id="app1", tier="app", leaves=[_leaf("ecs", "ECS")]),
            Subnet(id="data1", tier="data", leaves=[_leaf("rds", "RDS")]),
        ])],
        gateway=_leaf("alb", "ALB"),
    )
    cloud = CloudBoundary(id="cloud", label="AWS", regions=[Region(id="region", label="us-east-1", vpcs=[vpc])])
    return NetworkIR(
        cloud=cloud,
        external_actors=[ExternalActor(id="users", kind="users", label="Users")],
        flows=[Flow(source="users", target="alb"), Flow(source="alb", target="ecs"),
               Flow(source="ecs", target="rds")],
    )


class BadgeMatchesPanelNumberingTests(unittest.TestCase):
    """For every view, panel step i and step_badge_i must anchor the same cell."""

    def _assert_consistent(self, xml: str, title: str) -> None:
        xml = ensure_ir_overview_panel(xml, title, "Test System")
        markers = derive_story_markers(xml)
        self.assertTrue(markers, "expected at least one derived story marker")

        panel_body = synthesize_ir_overview_body(xml, title, "Test System")
        applied = apply_step_markers(xml, markers, None)
        tree = ET.fromstring(applied)
        by_id = {c.get("id"): c for c in tree.iter("mxCell") if c.get("id")}

        for marker in markers:
            badge = by_id.get(f"step_badge_{marker.step_number}")
            self.assertIsNotNone(badge, f"no badge emitted for step {marker.step_number}")
            self.assertEqual(
                badge.get("anchor"), marker.cell_id,
                f"step_badge_{marker.step_number} anchors {badge.get('anchor')!r}, "
                f"not the derived target {marker.cell_id!r}",
            )
            # The panel prose numbers the same step — its target's label must
            # appear in that numbered line, so a reader can find the same cell
            # by reading the sentence or by looking at the badge.
            target_label = by_id[marker.cell_id].get("value") or ""
            self.assertIn(
                f"{marker.step_number}. ", panel_body,
                f"panel body has no numbered line {marker.step_number}",
            )
            line_start = panel_body.index(f"{marker.step_number}. ")
            line_end = panel_body.index("&lt;/div&gt;", line_start)
            self.assertIn(
                target_label, panel_body[line_start:line_end],
                f"panel step {marker.step_number} text does not mention {target_label!r}, "
                f"the same cell step_badge_{marker.step_number} anchors on",
            )

    def test_security(self) -> None:
        xml = compile_corridor_ir(_security_ir(), page_name="Security")
        self._assert_consistent(xml, "Security")

    def test_logical(self) -> None:
        xml = compile_corridor_ir(_logical_ir(), page_name="Logical")
        self._assert_consistent(xml, "Logical")

    def test_data_flow(self) -> None:
        xml = compile_corridor_ir(_data_flow_ir(), page_name="Data flow")
        self._assert_consistent(xml, "Data flow")

    def test_network(self) -> None:
        xml = compile_network_ir(_network_ir(), page_name="Network")
        self._assert_consistent(xml, "Network")


class BadgeCountTests(unittest.TestCase):
    def test_never_exceeds_five_badges(self) -> None:
        """Matches apply_step_markers' own cap — neither side should ever
        need to silently truncate what the other already committed to."""
        many_leaves = [_leaf(f"l{i}", f"Leaf {i}") for i in range(10)]
        ir = DataFlowIR(
            corridors=[Corridor(id="phase_ingest", label="Ingest", leaves=many_leaves)],
            flows=[Flow(source=f"l{i}", target=f"l{i + 1}") for i in range(9)],
        )
        xml = compile_corridor_ir(ir, page_name="Data flow")
        markers = derive_story_markers(ensure_ir_overview_panel(xml, "Data flow", "Test"))
        self.assertLessEqual(len(markers), 5)


class NoDoubleDerivationTests(unittest.TestCase):
    def test_page_that_already_has_a_panel_gets_no_new_markers(self) -> None:
        """If a panel already exists (e.g. a repair-loop re-finalization),
        deriving again would duplicate badges on an already-badged page."""
        xml = compile_corridor_ir(_security_ir(), page_name="Security")
        xml = ensure_ir_overview_panel(xml, "Security", "Test System")
        from app.adapters.outbound.diagram.architecture_docs import page_has_overview_panel
        self.assertTrue(page_has_overview_panel(xml))


if __name__ == "__main__":
    unittest.main()
