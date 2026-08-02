"""Deterministic legend (``compiler/legend.py``) for the corridor views.

Load-bearing property: the legend must only describe conventions the specific
page actually uses, so a reader is never told about a distinction that isn't
in front of them, and a page with too little going on doesn't pay a "clean
canvas" tax for a legend nobody needs.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.compiler.legend import LEGEND_ID, build_legend
from app.adapters.outbound.diagram.xml_diagnostics import repair_xml
from app.domain.ir import AnchoredCorridor, Corridor, DataFlowIR, Flow, Leaf, SecurityIR


def _leaf(i: str) -> Leaf:
    return Leaf(id=i, shape_type="aws_ecs", label=i.title())


class LegendContentTests(unittest.TestCase):
    def test_no_legend_for_a_single_convention(self) -> None:
        """One row of guidance is not worth a box on the canvas."""
        ir = SecurityIR(
            corridors=[Corridor(id="zone_public", label="Public", leaves=[_leaf("a"), _leaf("b")])],
            flows=[Flow(source="a", target="b")],  # all primary, no shelves, not enough for 2 rows
        )
        self.assertIsNone(build_legend(ir))

    def test_solid_dashed_row_only_when_both_kinds_of_flow_exist(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("a"), _leaf("b")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("d")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="SaaS", anchor_id="zone_restricted", leaves=[_leaf("c")],
            )],
            flows=[Flow(source="a", target="b", primary=True)],
        )
        body = build_legend(ir)
        self.assertIsNotNone(body)
        self.assertNotIn("Solid line", body)  # no secondary flow present
        self.assertIn("shelf", body)

    def test_shelf_row_absent_when_no_anchored_corridors(self) -> None:
        ir = SecurityIR(
            corridors=[Corridor(id="zone_public", label="Public", leaves=[_leaf("a"), _leaf("b")])],
            flows=[Flow(source="a", target="b", primary=True), Flow(source="b", target="a", primary=False)],
        )
        body = build_legend(ir)
        self.assertIsNotNone(body)
        self.assertIn("Solid line", body)
        self.assertNotIn("shelf", body)

    def test_trust_row_only_on_security(self) -> None:
        data_flow_ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("a"), _leaf("b")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("d")]),
            ],
            anchored=[AnchoredCorridor(
                id="phase_consume", label="Consume", anchor_id="phase_store", leaves=[_leaf("c")],
            )],
            flows=[Flow(source="a", target="b", primary=True), Flow(source="b", target="c", primary=False)],
        )
        body = build_legend(data_flow_ir)
        self.assertIsNotNone(body)
        self.assertNotIn("trust level", body)

    def test_singular_vs_plural_shelf_wording(self) -> None:
        one_shelf = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("a"), _leaf("b")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("d")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="SaaS", anchor_id="zone_restricted", leaves=[_leaf("c")],
            )],
            flows=[Flow(source="a", target="b", primary=True), Flow(source="b", target="a", primary=False)],
        )
        self.assertIn("shelf ", build_legend(one_shelf))
        self.assertNotIn("shelves", build_legend(one_shelf))


class LegendPositioningTests(unittest.TestCase):
    def test_legend_survives_full_repair_with_zero_diagnostics(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="SaaS", anchor_id="zone_restricted", leaves=[_leaf("workday")],
            )],
            flows=[Flow(source="dns", target="rds", primary=True),
                   Flow(source="rds", target="workday", primary=False)],
        )
        xml = compile_corridor_ir(ir, page_name="Security")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(post.warnings, [])
        cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        self.assertIn(LEGEND_ID, cells)

    def test_legend_pins_directly_below_the_overview_panel(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import ensure_ir_overview_panel

        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="SaaS", anchor_id="zone_restricted", leaves=[_leaf("workday")],
            )],
            flows=[Flow(source="dns", target="rds", primary=True),
                   Flow(source="rds", target="workday", primary=False)],
        )
        xml = compile_corridor_ir(ir, page_name="Security")
        xml = ensure_ir_overview_panel(xml, "Security", "Test")
        repaired, _post = repair_xml(xml)
        cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        panel_geo = cells["overview_panel"].find("mxGeometry")
        legend_geo = cells[LEGEND_ID].find("mxGeometry")
        self.assertAlmostEqual(float(legend_geo.get("x")), float(panel_geo.get("x")), delta=0.5)
        expected_y = float(panel_geo.get("y")) + float(panel_geo.get("height")) + 20.0
        self.assertAlmostEqual(float(legend_geo.get("y")), expected_y, delta=0.5)


if __name__ == "__main__":
    unittest.main()
