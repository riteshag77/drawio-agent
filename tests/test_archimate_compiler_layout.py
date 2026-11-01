"""Compiler/layout coverage for ArchimateIR -> draw.io XML.

``compile_archimate_ir``, ``_plan_band`` and ``elk_layout_archimate`` had zero
test coverage before this file — which is exactly how the cross-band grouping
corruption and the grouping-endpoint hole (both fixed in ``archimate.py``'s
IR validators) survived. Modelled directly on
``tests/test_corridor_ir_layout.py``'s equivalence tests: same "raw compiler
output has zero diagnostics" / "layout=ir marker" / "repair_xml touches
nothing" shape, adapted to ArchiMate's band -> grouping -> element containment
instead of corridor -> leaf.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import compile_archimate_ir
from app.adapters.outbound.diagram.compiler.shared import CompilerError
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.ir.archimate import ArchimateIR


def _non_routing_warnings(warnings: list[str]) -> list[str]:
    """Routing warnings (box/parallel overlap) are fix_edge_anchors_from_geometry's
    job at repair time, never the compiler's — same filter test_corridor_ir_layout.py
    uses for the cloud views' raw-compiler-output assertions."""
    return [
        w for w in warnings
        if "passes through non-endpoint node" not in w
        and not ("overlap" in w and "segment" in w)
    ]


def realistic_archimate_ir() -> ArchimateIR:
    """A layered diagram dense enough to exercise every rendering path:
    a plain business band, an application band crowded enough (9 elements,
    2 groups, every element grouped) to draw real grouping boxes, and a plain
    technology band — plus one legal cross-layer relationship per hop."""
    elements = [
        # business: plain, no groups
        {"id": "customer", "archimate_type": "business-actor", "name": "Customer", "source_confidence": "explicit"},
        {"id": "quote_bind", "archimate_type": "business-process", "name": "Quote & Bind", "source_confidence": "explicit"},
        {"id": "policy", "archimate_type": "business-object", "name": "Policy", "source_confidence": "explicit"},
        # application: 9 elements, 2 groups, every element grouped -> boxes render
        {"id": "portal", "archimate_type": "application-component", "name": "Agent Portal", "source_confidence": "explicit", "group": "underwriting"},
        {"id": "quote_svc", "archimate_type": "application-service", "name": "Quote Service", "source_confidence": "explicit", "group": "underwriting"},
        {"id": "rating_svc", "archimate_type": "application-service", "name": "Rating Service", "source_confidence": "inferred", "notes": "Implied by quote-and-bind.", "group": "underwriting"},
        {"id": "underwriting_fn", "archimate_type": "application-function", "name": "Underwriting", "source_confidence": "explicit", "group": "underwriting"},
        {"id": "quote_data", "archimate_type": "data-object", "name": "Quote", "source_confidence": "explicit", "group": "underwriting"},
        {"id": "billing_svc", "archimate_type": "application-service", "name": "Billing Service", "source_confidence": "explicit", "group": "billing"},
        {"id": "invoice_data", "archimate_type": "data-object", "name": "Invoice", "source_confidence": "explicit", "group": "billing"},
        {"id": "payment_comp", "archimate_type": "application-component", "name": "Payment Component", "source_confidence": "explicit", "group": "billing"},
        {"id": "payment_iface", "archimate_type": "application-interface", "name": "Payment API", "source_confidence": "explicit", "group": "billing"},
        # technology: plain, no groups
        {"id": "rating_engine", "archimate_type": "technology-service", "name": "Rating Engine", "source_confidence": "assumed", "notes": "Hosting model not stated."},
        {"id": "policy_store", "archimate_type": "technology-node", "name": "Policy Store", "source_confidence": "explicit"},
    ]
    relationships = [
        {"id": "r1", "type": "assignment", "source": "customer", "target": "quote_bind"},
        {"id": "r2", "type": "access", "source": "quote_bind", "target": "policy"},
        {"id": "r3", "type": "realization", "source": "quote_svc", "target": "quote_bind"},
        {"id": "r4", "type": "serving", "source": "portal", "target": "quote_svc"},
        {"id": "r5", "type": "assignment", "source": "underwriting_fn", "target": "rating_svc"},
        {"id": "r6", "type": "access", "source": "rating_svc", "target": "quote_data"},
        {"id": "r7", "type": "serving", "source": "billing_svc", "target": "quote_bind"},
        {"id": "r8", "type": "access", "source": "billing_svc", "target": "invoice_data"},
        {"id": "r9", "type": "assignment", "source": "payment_comp", "target": "billing_svc"},
        {"id": "r10", "type": "realization", "source": "payment_iface", "target": "payment_comp"},
        {"id": "r11", "type": "serving", "source": "rating_engine", "target": "rating_svc"},
        {"id": "r12", "type": "serving", "source": "policy_store", "target": "quote_svc"},
    ]
    groupings = [
        {"id": "underwriting", "label": "Underwriting", "layer_hint": "application",
         "rationale": "Quoting and rating form one functional area."},
        {"id": "billing", "label": "Billing", "layer_hint": "application",
         "rationale": "Payment and invoicing form a second functional area."},
    ]
    return ArchimateIR.model_validate({
        "viewpoint": "layered",
        "elements": elements,
        "relationships": relationships,
        "groupings": groupings,
        "panel_summary": "Shows the quote-and-bind capability across business, application and technology.",
        "viewpoint_rationale": "The brief asks how the capability is delivered end to end.",
        "walkthrough": ["Quote & Bind is the entry point.", "Realized by the Quote Service.", "Served by the rating engine."],
        "coaching_note": "Confirming the rating engine's hosting model would sharpen the technology layer.",
    })


class RawCompilerOutputTests(unittest.TestCase):
    def test_raw_compiler_output_has_zero_diagnostics(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(_non_routing_warnings(diag.warnings), [])

    def test_layout_authoritative_marker_is_set(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["1"].get("layout"), "ir")

    def test_repair_xml_does_not_touch_band_or_element_geometry(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(_non_routing_warnings(post.warnings), [])
        before = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        after = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        for cid, geom in before.items():
            if geom is None or cid not in after or after[cid] is None:
                continue
            if geom.get("relative") == "1":
                continue  # edge geometry, not a band/grouping/element
            self.assertEqual(
                (geom.get("x"), geom.get("y"), geom.get("width"), geom.get("height")),
                (after[cid].get("x"), after[cid].get("y"), after[cid].get("width"), after[cid].get("height")),
                f"{cid}'s geometry changed under repair_xml",
            )


class ElementSizingTests(unittest.TestCase):
    def test_every_element_is_exactly_archimate_size(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        ir = realistic_archimate_ir()
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        for element in ir.elements:
            geom = cells[element.id].find("mxGeometry")
            self.assertEqual(float(geom.get("width")), geo.ARCHIMATE_ELEMENT_W, element.id)
            self.assertEqual(float(geom.get("height")), geo.ARCHIMATE_ELEMENT_H, element.id)

    def test_no_element_overflows_its_parent(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        ir = realistic_archimate_ir()
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        for element in ir.elements:
            cell = cells[element.id]
            parent_id = cell.get("parent")
            parent_geom = cells[parent_id].find("mxGeometry")
            geom = cell.find("mxGeometry")
            x, y = float(geom.get("x")), float(geom.get("y"))
            w, h = float(geom.get("width")), float(geom.get("height"))
            pw, ph = float(parent_geom.get("width")), float(parent_geom.get("height"))
            self.assertGreaterEqual(x, 0, element.id)
            self.assertGreaterEqual(y, 0, element.id)
            self.assertLessEqual(x + w, pw + 1e-6, f"{element.id} overflows parent {parent_id} width")
            self.assertLessEqual(y + h, ph + 1e-6, f"{element.id} overflows parent {parent_id} height")


class GroupingRenderTests(unittest.TestCase):
    def test_crowded_grouped_band_draws_two_grouping_boxes(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertIn("underwriting", cells)
        self.assertIn("billing", cells)
        # Both grouping boxes are parented to the application band, not to
        # each other and not to the root — proves they rendered as real
        # nested containers, not just panel text.
        band_ids = [cid for cid in cells if cid.startswith("archimate_band_")]
        self.assertEqual(len(band_ids), 3)  # business, application, technology
        underwriting_parent = cells["underwriting"].get("parent")
        billing_parent = cells["billing"].get("parent")
        self.assertEqual(underwriting_parent, billing_parent)
        self.assertIn(underwriting_parent, band_ids)

    def test_grouped_elements_are_parented_to_their_grouping_not_the_band(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["portal"].get("parent"), "underwriting")
        self.assertEqual(cells["payment_comp"].get("parent"), "billing")

    def test_ungrouped_bands_have_no_grouping_boxes(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        # business/technology elements sit directly under their band.
        band_ids = {cid for cid in cells if cid.startswith("archimate_band_")}
        for leaf_id in ("customer", "quote_bind", "policy", "rating_engine", "policy_store"):
            self.assertIn(cells[leaf_id].get("parent"), band_ids)


class BandLayoutTests(unittest.TestCase):
    def test_bands_share_a_left_edge_and_do_not_overlap_vertically(self) -> None:
        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        band_ids = [cid for cid in cells if cid.startswith("archimate_band_")]
        self.assertEqual(len(band_ids), 3)
        geoms = {cid: cells[cid].find("mxGeometry") for cid in band_ids}
        lefts = {float(g.get("x")) for g in geoms.values()}
        self.assertEqual(len(lefts), 1, f"bands do not share a left edge: {lefts}")
        # Reading order top to bottom is business, application, technology —
        # verify by non-overlapping Y ranges in that order.
        ranges = sorted(
            (float(g.get("y")), float(g.get("y")) + float(g.get("height"))) for g in geoms.values()
        )
        for (_, prev_bottom), (next_top, _) in zip(ranges, ranges[1:]):
            self.assertLessEqual(prev_bottom, next_top + 1e-6)


class VisualQaHazardTests(unittest.TestCase):
    """DIAGRAM_VLM_QA's validate_layout/diagnose_xml checklist is calibrated
    for the cloud views' port-anchored, repair-routed edges — it flags a lot
    of noise on ArchiMate's plain root-layer relationship edges (verified: a
    structurally sound page trips a dozen "exitX anchor disagrees"/"overlap"
    warnings there). It is fail-open and never blocks shipping, but it does
    apply one corrective pass (fix_edge_anchors_from_geometry) when it finds
    issues — this confirms that pass only ever touches edge routing hints,
    never the band/grouping/element geometry ELK owns."""

    def test_visual_qa_pass_does_not_move_vertex_geometry(self) -> None:
        from app.adapters.outbound.diagram.diagram_vlm_qa import run_visual_qa

        xml = compile_archimate_ir(realistic_archimate_ir(), page_name="ArchiMate")
        qa_xml = run_visual_qa(xml)
        before = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        after = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(qa_xml).iter("mxCell") if c.get("id")}
        for cid, geom in before.items():
            if geom is None or geom.get("relative") == "1":
                continue  # edge geometry, not a band/grouping/element
            after_geom = after.get(cid)
            if after_geom is None:
                continue
            self.assertEqual(
                (geom.get("x"), geom.get("y"), geom.get("width"), geom.get("height")),
                (after_geom.get("x"), after_geom.get("y"), after_geom.get("width"), after_geom.get("height")),
                f"{cid}'s geometry changed under DIAGRAM_VLM_QA's corrective pass",
            )


class RejectedInputRegressionTests(unittest.TestCase):
    """The IR validators reject cross-band groupings and grouping-id
    relationship endpoints (see tests/test_ir_archimate.py's
    GroupingEndpointAndCrossBandTests for the underlying rule). This confirms
    the compiler's own contract — never silently compile an invalid IR —
    holds for both, since compile_archimate_ir calls semantic_errors() before
    doing any layout work."""

    def test_cross_band_grouping_raises_compiler_error_not_corrupt_xml(self) -> None:
        ir = ArchimateIR.model_validate({
            "viewpoint": "layered",
            "elements": [
                {"id": "a", "archimate_type": "business-actor", "name": "A",
                 "source_confidence": "explicit", "group": "g1"},
                {"id": "b", "archimate_type": "application-component", "name": "B",
                 "source_confidence": "explicit", "group": "g1"},
            ],
            "groupings": [{"id": "g1", "label": "G1"}],
            "relationships": [{"id": "r1", "type": "serving", "source": "b", "target": "a"}],
        })
        with self.assertRaises(CompilerError):
            compile_archimate_ir(ir, page_name="ArchiMate")

    def test_relationship_pointing_at_grouping_raises_compiler_error(self) -> None:
        ir = ArchimateIR.model_validate({
            "viewpoint": "layered",
            "elements": [
                {"id": "a", "archimate_type": "application-component", "name": "A",
                 "source_confidence": "explicit", "group": "g1"},
                {"id": "b", "archimate_type": "application-component", "name": "B",
                 "source_confidence": "explicit", "group": "g1"},
            ],
            "groupings": [{"id": "g1", "label": "G1"}],
            "relationships": [{"id": "r1", "type": "serving", "source": "a", "target": "g1"}],
        })
        with self.assertRaises(CompilerError):
            compile_archimate_ir(ir, page_name="ArchiMate")


if __name__ == "__main__":
    unittest.main()
