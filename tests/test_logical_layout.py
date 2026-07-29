"""Unit tests for dependency-driven layered layout helpers."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.logical_layout import (
    CONTAINER_MIN_H,
    CONTAINER_MIN_W,
    apply_layered_corridor_layout,
    barycenter_order,
    break_cycles,
    build_edge_pairs,
    grid_cols,
    grid_fill,
    longest_path_ranks,
    resize_container_to_grid,
)
from app.adapters.outbound.diagram.placement_fixers import fix_edge_anchors_from_geometry


def _page(name: str, cells: str) -> str:
    return (
        f'<mxfile><diagram name="{name}" id="p1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class GraphHelpersTests(unittest.TestCase):
    def test_break_cycles_drops_back_edge(self) -> None:
        edges = [("a", "b"), ("b", "c"), ("c", "a"), ("a", "d")]
        dag = break_cycles(edges)
        self.assertEqual(len(dag), 3)
        # Result must be a DAG — longest-path ranking must terminate.
        ranks = longest_path_ranks(["a", "b", "c", "d"], dag)
        self.assertEqual(len(ranks), 4)

    def test_longest_path_ranks(self) -> None:
        ranks = longest_path_ranks(
            ["a", "b", "c"],
            [("a", "b"), ("b", "c")],
        )
        self.assertEqual(ranks["a"], 0)
        self.assertEqual(ranks["b"], 1)
        self.assertEqual(ranks["c"], 2)

    def test_barycenter_reduces_crossing_order(self) -> None:
        # Two ranks; crossed edges a1→b2, a2→b1 — barycenter should swap b.
        ranks = {"a1": 0, "a2": 0, "b1": 1, "b2": 1}
        edges = [("a1", "b2"), ("a2", "b1")]
        order = barycenter_order(ranks, edges, sweeps=4)
        # After sweeps, b2 should sit near a1 (row 0) and b1 near a2 (row 1)
        self.assertLessEqual(order["b2"], order["b1"])


class GridFillTests(unittest.TestCase):
    """Unit tests for grid_fill/resize_container_to_grid — the single canonical
    sizing function that replaced three independent grow-only formulas (Fix 2b)."""

    def test_grid_fill_wraps_at_grid_cols(self) -> None:
        cols, rows, w, h = grid_fill(5)
        self.assertEqual(cols, grid_cols(5))
        self.assertEqual(rows, 2)  # 5 items at grid_cols(5)=3 -> 3 + 2
        # Content box uses the canonical geometry_spec pitch, not a bespoke one.
        self.assertAlmostEqual(w, (cols - 1) * geo.DATA_CANONICAL_PITCH + geo.LEAF_W)
        self.assertAlmostEqual(h, (rows - 1) * geo.ROW_PITCH_Y + geo.LEAF_H)

    def test_grid_fill_zero_count(self) -> None:
        self.assertEqual(grid_fill(0), (0, 0, 0.0, 0.0))

    def test_grid_fill_single_row_for_small_counts(self) -> None:
        # <=3 icons never wrap (grid_cols returns count itself for count<=3).
        cols, rows, _w, _h = grid_fill(3)
        self.assertEqual(cols, 3)
        self.assertEqual(rows, 1)

    def _geom(self, w: float, h: float) -> ET.Element:
        return ET.fromstring(f'<mxGeometry x="0" y="0" width="{w}" height="{h}"/>')

    def test_resize_container_to_grid_shrinks_oversized_box(self) -> None:
        geom = self._geom(1182.0, 890.0)  # the originally-reported defect shape
        changed = resize_container_to_grid(geom, 3)
        self.assertTrue(changed)
        self.assertLess(float(geom.get("width")), 1182.0)
        self.assertLess(float(geom.get("height")), 890.0)

    def test_resize_container_to_grid_grows_undersized_box(self) -> None:
        geom = self._geom(50.0, 40.0)
        changed = resize_container_to_grid(geom, 5)
        self.assertTrue(changed)
        self.assertGreater(float(geom.get("width")), 50.0)
        self.assertGreater(float(geom.get("height")), 40.0)

    def test_resize_container_to_grid_noop_when_already_correct(self) -> None:
        _cols, _rows, content_w, content_h = grid_fill(4)
        from app.adapters.outbound.diagram.logical_layout import (
            CONTAINER_LABEL_HEIGHT,
            CONTAINER_PADDING_X,
            CONTAINER_PADDING_Y,
        )
        w = content_w + CONTAINER_PADDING_X * 2
        h = content_h + CONTAINER_LABEL_HEIGHT + CONTAINER_PADDING_Y
        geom = self._geom(w, h)
        changed = resize_container_to_grid(geom, 4)
        self.assertFalse(changed)

    def test_resize_container_to_grid_respects_floor(self) -> None:
        geom = self._geom(10.0, 10.0)
        resize_container_to_grid(geom, 1)
        self.assertGreaterEqual(float(geom.get("width")), CONTAINER_MIN_W)
        self.assertGreaterEqual(float(geom.get("height")), CONTAINER_MIN_H)


class LayeredCorridorTests(unittest.TestCase):
    def test_domains_ordered_by_dependency(self) -> None:
        page = _page(
            "Logical",
            '<mxCell id="domain_data" value="Data" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="100" y="80" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="ds" value="Store" style="rounded=1;" vertex="1" parent="domain_data">'
            '<mxGeometry x="20" y="50" width="120" height="40" as="geometry"/></mxCell>'
            '<mxCell id="domain_core" value="Core" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="80" width="200" height="140" as="geometry"/></mxCell>'
            '<mxCell id="cap" value="Orders capability" style="rounded=1;" vertex="1" parent="domain_core">'
            '<mxGeometry x="20" y="50" width="120" height="40" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="cap" target="ds" value="Writes">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        tree = ET.fromstring(page)
        root = tree.find(".//root")
        assert root is not None
        cells = [c for c in root.findall("mxCell") if c.get("id")]
        corridors = [c for c in cells if (c.get("id") or "").startswith("domain_")]
        apply_layered_corridor_layout(
            cells,
            corridors=corridors,
            leaf_filter=lambda c: c.get("vertex") == "1" and c.get("edge") != "1"
            and "container=1" not in (c.get("style") or ""),
            keyword_key=lambda c: (0, c.get("id") or ""),
        )
        by_id = {c.get("id"): c for c in cells}
        core_x = float(by_id["domain_core"].find("mxGeometry").get("x") or 0)
        data_x = float(by_id["domain_data"].find("mxGeometry").get("x") or 0)
        self.assertLess(core_x, data_x, "producer domain must sit left of consumer")


class TileSizingMatchesPlacementTests(unittest.TestCase):
    """Regression: `_layout_tiles_in_container` computed leaf *placement*
    columns (`cols_n`, capped by flow-derived rank depth) separately from
    the shelf/container *resize* call (`resize_container_to_grid(shelf_geo,
    len(kids))`, which recomputed its own, wider default column count) —
    when they disagreed, leaves were placed into more rows than the
    container was resized for, and rendered outside it. Reproduced with 6
    leaves forming three independent 2-node flow chains, the same condition
    that exposed the equivalent bug in the IR path's `_place_leaves`."""

    def test_leaves_never_overflow_container_after_layout(self) -> None:
        cells_xml = (
            '<mxCell id="domain_experience" value="Experience" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="160" as="geometry"/></mxCell>'
        )
        for i in range(6):
            cells_xml += (
                f'<mxCell id="l{i}" value="L{i}" style="rounded=1;" vertex="1" parent="domain_experience">'
                f'<mxGeometry x="24" y="30" width="78" height="78" as="geometry"/></mxCell>'
            )
        for s, t in (("l0", "l1"), ("l2", "l3"), ("l4", "l5")):
            cells_xml += (
                f'<mxCell id="e_{s}_{t}" edge="1" parent="1" source="{s}" target="{t}">'
                '<mxGeometry relative="1" as="geometry"/></mxCell>'
            )
        page = _page("Logical", cells_xml)
        tree = ET.fromstring(page)
        root = tree.find(".//root")
        assert root is not None
        cells = [c for c in root.findall("mxCell") if c.get("id")]
        corridors = [c for c in cells if (c.get("id") or "") == "domain_experience"]
        apply_layered_corridor_layout(
            cells,
            corridors=corridors,
            leaf_filter=lambda c: c.get("vertex") == "1" and c.get("edge") != "1"
            and "container=1" not in (c.get("style") or ""),
            keyword_key=lambda c: (0, c.get("id") or ""),
        )
        by_id = {c.get("id"): c for c in cells}
        container_geo = by_id["domain_experience"].find("mxGeometry")
        container_w = float(container_geo.get("width"))
        container_h = float(container_geo.get("height"))
        for i in range(6):
            leaf_geo = by_id[f"l{i}"].find("mxGeometry")
            x, y = float(leaf_geo.get("x")), float(leaf_geo.get("y"))
            w, h = float(leaf_geo.get("width")), float(leaf_geo.get("height"))
            self.assertLessEqual(x + w, container_w, f"l{i} right edge exceeds container width")
            self.assertLessEqual(y + h, container_h, f"l{i} bottom edge exceeds container height")


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
            'source="src" target="dst"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(page)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        pts = [p for p in edge.iter("mxPoint") if p.get("as") is None]
        self.assertTrue(pts, "Security pages should route around intervening icons")

    def test_same_row_edge_actually_clears_the_intervening_obstacle(self) -> None:
        """Regression for a real bug found via data/captures replay: when
        source and target are exactly level, the routed path used to include
        a "straight line through the obstacle" candidate that could win ties
        against every genuine detour, so the edge was drawn straight through
        the middle icon's box instead of jogging around it."""
        page = _page(
            "Security",
            '<mxCell id="src" value="Src" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="mid" value="Mid" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dst" value="Dst" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="560" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="Controls" style="html=1;" edge="1" parent="1" '
            'source="src" target="dst"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(page)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        pts = [(float(p.get("x")), float(p.get("y"))) for p in edge.iter("mxPoint") if p.get("as") is None]
        full_path = [(79.0, 139.0), *pts, (560.0, 139.0)]  # src right-mid -> ... -> dst left-mid
        mid_box = (300.0, 100.0, 78.0, 78.0)  # x, y, w, h
        for (x1, y1), (x2, y2) in zip(full_path, full_path[1:]):
            seg_lo_x, seg_hi_x = sorted((x1, x2))
            seg_lo_y, seg_hi_y = sorted((y1, y2))
            mx, my, mw, mh = mid_box
            overlaps = seg_lo_x < mx + mw and mx < seg_hi_x and seg_lo_y < my + mh and my < seg_hi_y
            self.assertFalse(overlaps, f"segment {(x1,y1)}-{(x2,y2)} clips the intervening icon")

    def test_wide_corridor_detour_stays_local_not_off_page(self) -> None:
        """Regression: the under-corridor detour band used to be computed from
        every obstacle/frame sharing the edge's X-range regardless of how far
        away it sat vertically — a same-row edge on a wide page could get
        routed to y=1444 on a ~600px-tall canvas because an unrelated
        full-page background rect got counted as an obstacle."""
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
            'source="src" target="dst"><mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = fix_edge_anchors_from_geometry(page)
        edge = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "e1")
        pts = [(float(p.get("x")), float(p.get("y"))) for p in edge.iter("mxPoint") if p.get("as") is None]
        for _x, y in pts:
            self.assertGreater(y, -400.0, "detour should not fly off the top of a ~1400px-tall page")
            self.assertLess(y, 1800.0, "detour should not fly off the bottom of a ~1400px-tall page")


if __name__ == "__main__":
    unittest.main()
