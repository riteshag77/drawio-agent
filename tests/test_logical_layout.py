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


if __name__ == "__main__":
    unittest.main()
