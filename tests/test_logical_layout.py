"""Unit tests for dependency-driven layered layout helpers."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.logical_layout import (
    apply_layered_corridor_layout,
    barycenter_order,
    break_cycles,
    build_edge_pairs,
    longest_path_ranks,
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
