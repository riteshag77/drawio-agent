"""Audience-clear diagram clarity — geometry + flow graph helpers."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import story_step_cap
from app.adapters.outbound.diagram.edge_routing import (
    _even_fractions,
    fix_edge_anchors_from_geometry,
)
from app.adapters.outbound.diagram.logical_layout import (
    align_leaves_across_containers,
    order_leaves_by_flow,
)
from app.domain.ir.base import Flow, Leaf


def _leaf(i: str) -> Leaf:
    return Leaf(id=i, shape_type="logical_capability", label=i)


class StoryStepCapTests(unittest.TestCase):
    def test_matches_view_story_caps(self) -> None:
        self.assertEqual(story_step_cap("logical"), 6)
        self.assertEqual(story_step_cap("network"), 7)
        # Security has the most zones of any view and security.txt's own
        # worked journey is 6 hops (public -> partner -> identity -> private
        # -> restricted -> detect); at the default 5 the panel steps and the
        # canvas badges both stopped before the restricted/detect end.
        self.assertEqual(story_step_cap("security"), 7)
        self.assertEqual(story_step_cap(""), 5)


class EvenFanoutTests(unittest.TestCase):
    def test_fractions_even_for_n_2_to_6(self) -> None:
        for n in range(2, 7):
            fracs = [float(f) for f in _even_fractions(n)]
            self.assertEqual(len(fracs), n)
            self.assertAlmostEqual(fracs[0], 1 / (n + 1), places=4)
            self.assertAlmostEqual(fracs[-1], n / (n + 1), places=4)
            gaps = [fracs[i + 1] - fracs[i] for i in range(n - 1)]
            self.assertTrue(all(abs(g - gaps[0]) < 1e-3 for g in gaps))


class SpineBeforeWrapTests(unittest.TestCase):
    def test_primary_chain_leads_leaf_order(self) -> None:
        leaves = [_leaf("a"), _leaf("b"), _leaf("c"), _leaf("x")]
        flows = [
            Flow(source="a", target="b", primary=True),
            Flow(source="b", target="c", primary=True),
            Flow(source="x", target="c", primary=False),
        ]
        ordered = [leaf.id for leaf in order_leaves_by_flow(leaves, flows)]
        self.assertEqual(ordered[:3], ["a", "b", "c"])
        self.assertIn("x", ordered)


class CrossContainerAlignTests(unittest.TestCase):
    def test_primary_edges_prefer_matching_indices(self) -> None:
        left = [_leaf("l0"), _leaf("l1")]
        right = [_leaf("r1"), _leaf("r0")]  # declaration order opposite of primary
        flows = [
            Flow(source="l0", target="r0", primary=True),
            Flow(source="l1", target="r1", primary=True),
        ]
        aligned = align_leaves_across_containers([left, right], flows)
        self.assertEqual([leaf.id for leaf in aligned[0]], ["l0", "l1"])
        self.assertEqual([leaf.id for leaf in aligned[1]], ["r0", "r1"])


class PortFanoutIntegrationTests(unittest.TestCase):
    def test_hub_exits_use_distinct_even_ports(self) -> None:
        page = (
            '<mxfile><diagram name="Logical" id="p1">'
            '<mxGraphModel pageWidth="1600" pageHeight="900"><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0" layout="ir"/>'
            '<mxCell id="hub" value="Hub" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="100" y="100" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="a" value="A" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="140" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="c" value="C" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="240" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" value="1" style="html=1;" edge="1" parent="1" source="hub" target="a">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" value="2" style="html=1;dashed=1;" edge="1" parent="1" source="hub" target="b">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e3" value="3" style="html=1;" edge="1" parent="1" source="hub" target="c">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = fix_edge_anchors_from_geometry(page, ports_only=True)
        exits = []
        for cell in ET.fromstring(out).iter("mxCell"):
            if cell.get("edge") != "1":
                continue
            style = cell.get("style") or ""
            kv = dict(p.split("=", 1) for p in style.split(";") if "=" in p)
            exits.append(float(kv.get("exitY", "0.5")))
        self.assertEqual(len(exits), 3)
        self.assertEqual(len(set(round(y, 4) for y in exits)), 3)
        # Even spacing: 0.25 / 0.5 / 0.75 for n=3
        self.assertAlmostEqual(sorted(exits)[0], 0.25, places=2)
        self.assertAlmostEqual(sorted(exits)[1], 0.5, places=2)
        self.assertAlmostEqual(sorted(exits)[2], 0.75, places=2)


if __name__ == "__main__":
    unittest.main()
