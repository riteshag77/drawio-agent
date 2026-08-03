"""Placement fixer unit tests — surviving public API only."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.edge_routing import (
    _is_saas_leaf_cell,
    fix_edge_anchors_from_geometry,
)


def _page(name: str, cells: str) -> str:
    return (
        f'<mxfile><diagram name="{name}" id="p1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


def _cell(id_: str, *, shape_type: str = "", value: str = "") -> ET.Element:
    attrs = {"id": id_, "vertex": "1"}
    if shape_type:
        attrs["shape_type"] = shape_type
    if value:
        attrs["value"] = value
    return ET.Element("mxCell", attrs)


class SaasLeafCellExcludesAnnotationsTests(unittest.TestCase):
    def test_overview_panel_with_stray_saas_shape_type_is_not_a_saas_leaf(self) -> None:
        cell = _cell("overview_panel", shape_type="saas_okta", value="Some overview text")
        self.assertFalse(_is_saas_leaf_cell(cell))

    def test_legend_box_with_stray_saas_shape_type_is_not_a_saas_leaf(self) -> None:
        cell = _cell("legend_box", shape_type="saas_stripe")
        self.assertFalse(_is_saas_leaf_cell(cell))

    def test_real_saas_leaf_still_recognized(self) -> None:
        cell = _cell("okta", shape_type="saas_okta", value="Okta")
        self.assertTrue(_is_saas_leaf_cell(cell))


class FocusedViewRoutingTests(unittest.TestCase):
    def test_security_page_gets_obstacle_routing(self) -> None:
        page = _page(
            "Security",
            '<mxCell id="src" value="Src" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="mid" value="Mid" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="100" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="dst" value="Dst" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="560" y="100" width="160" height="60" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="Controls" style="html=1;" edge="1" parent="1" '
            'source="src" target="dst"><mxGeometry relative="1" as="geometry"/></mxCell>',
        )
        out = fix_edge_anchors_from_geometry(page)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        pts = [p for p in edge.iter("mxPoint") if p.get("as") is None]
        self.assertTrue(pts, "Security pages should route around intervening icons")

    def test_same_row_edge_actually_clears_the_intervening_obstacle(self) -> None:
        page = _page(
            "Security",
            '<mxCell id="src" value="Src" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="mid" value="Mid" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dst" value="Dst" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="560" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="Controls" style="html=1;" edge="1" parent="1" '
            'source="src" target="dst"><mxGeometry relative="1" as="geometry"/></mxCell>',
        )
        out = fix_edge_anchors_from_geometry(page)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        pts = [(float(p.get("x")), float(p.get("y"))) for p in edge.iter("mxPoint") if p.get("as") is None]
        full_path = [(79.0, 139.0), *pts, (560.0, 139.0)]
        mid_box = (300.0, 100.0, 78.0, 78.0)
        for (x1, y1), (x2, y2) in zip(full_path, full_path[1:]):
            seg_lo_x, seg_hi_x = sorted((x1, x2))
            seg_lo_y, seg_hi_y = sorted((y1, y2))
            mx, my, mw, mh = mid_box
            overlaps = seg_lo_x < mx + mw and mx < seg_hi_x and seg_lo_y < my + mh and my < seg_hi_y
            self.assertFalse(overlaps, f"segment {(x1, y1)}-{(x2, y2)} clips the intervening icon")

    def test_wide_corridor_detour_stays_local_not_off_page(self) -> None:
        page = _page(
            "Security",
            '<mxCell id="bg" value="" style="fillColor=#F5F5F5;strokeColor=none;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="2400" height="1400" as="geometry"/></mxCell>'
            '<mxCell id="src" value="Src" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="200" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="a" value="A" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="230" width="78" height="106" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="360" width="78" height="106" as="geometry"/></mxCell>'
            '<mxCell id="dst" value="Dst" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="600" y="200" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="Controls" style="html=1;" edge="1" parent="1" '
            'source="src" target="dst"><mxGeometry relative="1" as="geometry"/></mxCell>',
        )
        out = fix_edge_anchors_from_geometry(page)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        pts = [(float(p.get("x")), float(p.get("y"))) for p in edge.iter("mxPoint") if p.get("as") is None]
        for _x, y in pts:
            self.assertGreater(y, -400.0, "detour should not fly off the top of a ~1400px-tall page")
            self.assertLess(y, 1800.0, "detour should not fly off the bottom of a ~1400px-tall page")


if __name__ == "__main__":
    unittest.main()
