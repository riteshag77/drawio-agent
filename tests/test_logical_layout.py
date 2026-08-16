"""Unit tests for dependency-driven layered layout helpers + layout_corridor_ir."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.logical_layout import (
    CONTAINER_MIN_H,
    CONTAINER_MIN_W,
    _tile_order,
    barycenter_order,
    break_cycles,
    grid_cols,
    grid_fill,
    layout_corridor_ir,
    longest_path_ranks,
    resize_container_to_grid,
)
from app.domain.ir import AnchoredCorridor, Corridor, Flow, Leaf, LogicalIR


class GraphHelpersTests(unittest.TestCase):
    def test_break_cycles_drops_back_edge(self) -> None:
        edges = [("a", "b"), ("b", "c"), ("c", "a"), ("a", "d")]
        dag = break_cycles(edges)
        self.assertEqual(len(dag), 3)
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
        ranks = {"a1": 0, "a2": 0, "b1": 1, "b2": 1}
        edges = [("a1", "b2"), ("a2", "b1")]
        order = barycenter_order(ranks, edges, sweeps=4)
        self.assertLessEqual(order["b2"], order["b1"])


class TileOrderTests(unittest.TestCase):
    """What decides a leaf's place in its corridor's grid."""

    def test_declaration_order_survives_an_unrelated_edge(self) -> None:
        """One internal edge used to switch the whole corridor over to
        ordering by *id text*: declared [checkout, auth, cart] with a single
        checkout->auth edge put `cart` first, because it has no edges (so it
        took barycenter 0.0, the top of its rank) and "ca" sorts before "ch".
        A leaf must not be placed by the spelling of its identifier."""
        declared = ["checkout", "auth", "cart"]
        self.assertEqual(_tile_order(declared, [])[0], declared)
        ordered, _ = _tile_order(declared, [("checkout", "auth")])
        self.assertEqual(ordered[0], "checkout")
        # `cart` is unconnected: nothing is known about where it belongs, so
        # it stays where it was declared rather than being moved anywhere.
        self.assertEqual(ordered, ["checkout", "cart", "auth"])

    def test_a_real_flow_still_overrides_declaration_order(self) -> None:
        """Declaration order is the tie-break, not a veto — an edge saying
        checkout comes before auth still reverses a list declared the other
        way round."""
        self.assertEqual(
            _tile_order(["auth", "checkout"], [("checkout", "auth")])[0],
            ["checkout", "auth"],
        )

    def test_crossings_are_still_minimised(self) -> None:
        ordered, _ = _tile_order(["a", "b", "c", "d"], [("a", "d"), ("b", "c")])
        self.assertEqual(ordered.index("d"), 2)
        self.assertEqual(ordered.index("c"), 3)


class LayoutIdempotencyTests(unittest.TestCase):
    """``layout_corridor_ir`` rewrites ``corridor.leaves`` into flow order —
    Logical's emitter reads that list order rather than the geometry. The
    rewrite has to be a fixed point, or a second layout pass over the same IR
    would draw a different diagram than the first."""

    def _backwards_ir(self) -> LogicalIR:
        """Leaves declared in reverse of the flow, so the first pass must move
        them — otherwise this asserts nothing."""
        core = Corridor(id="domain_core", label="Core", leaves=[
            Leaf(id=f"c{i}", shape_type="logical_capability", label=f"C{i}")
            for i in (4, 3, 2, 1, 0)
        ])
        portal = Corridor(id="domain_experience", label="E", leaves=[
            Leaf(id="p", shape_type="logical_portal", label="Portal"),
        ])
        ir = LogicalIR(corridors=[portal, core])
        ir.flows = [Flow(source="p", target="c0")] + [
            Flow(source=f"c{i}", target=f"c{i + 1}") for i in range(4)
        ]
        return ir

    def test_reorder_settles_after_one_pass(self) -> None:
        ir = self._backwards_ir()
        declared = [leaf.id for leaf in ir.corridors[1].leaves]
        layout_corridor_ir(ir)
        after_first = [leaf.id for leaf in ir.corridors[1].leaves]
        layout_corridor_ir(ir)
        after_second = [leaf.id for leaf in ir.corridors[1].leaves]

        self.assertNotEqual(declared, after_first, "fixture must actually reorder")
        self.assertEqual(after_first, after_second)

    def test_geometry_is_identical_on_a_repeat_pass(self) -> None:
        ir = self._backwards_ir()
        first, _ = layout_corridor_ir(ir)
        second, _ = layout_corridor_ir(ir)
        self.assertEqual(set(first), set(second))
        for cell_id, geo_first in first.items():
            geo_second = second[cell_id]
            self.assertAlmostEqual(geo_first.x, geo_second.x, places=3, msg=cell_id)
            self.assertAlmostEqual(geo_first.y, geo_second.y, places=3, msg=cell_id)


class GridFillTests(unittest.TestCase):
    def test_grid_fill_wraps_at_grid_cols(self) -> None:
        cols, rows, w, h = grid_fill(5)
        self.assertEqual(cols, grid_cols(5))
        self.assertEqual(rows, 2)
        self.assertAlmostEqual(w, (cols - 1) * geo.DATA_CANONICAL_PITCH + geo.LEAF_W)
        self.assertAlmostEqual(h, (rows - 1) * geo.ROW_PITCH_Y + geo.LEAF_H)

    def test_grid_fill_zero_count(self) -> None:
        self.assertEqual(grid_fill(0), (0, 0, 0.0, 0.0))

    def test_grid_fill_single_row_for_small_counts(self) -> None:
        cols, rows, _w, _h = grid_fill(3)
        self.assertEqual(cols, 3)
        self.assertEqual(rows, 1)

    def _geom(self, w: float, h: float) -> ET.Element:
        return ET.fromstring(f'<mxGeometry x="0" y="0" width="{w}" height="{h}"/>')

    def test_resize_container_to_grid_shrinks_oversized_box(self) -> None:
        geom = self._geom(1182.0, 890.0)
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


class LayoutCorridorIrTests(unittest.TestCase):
    def test_producer_domain_sits_left_of_consumer(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_data", label="Data", leaves=[
                    Leaf(id="ds", shape_type="logical_datastore", label="Store"),
                ]),
                Corridor(id="domain_core", label="Core", leaves=[
                    Leaf(id="cap", shape_type="logical_capability", label="Orders"),
                ]),
            ],
            flows=[Flow(source="cap", target="ds")],
        )
        geos, _routes = layout_corridor_ir(ir)
        self.assertLess(
            geos["domain_core"].x,
            geos["domain_data"].x,
            "producer domain must sit left of consumer",
        )

    def test_leaves_never_overflow_container(self) -> None:
        leaves = [
            Leaf(id=f"l{i}", shape_type="logical_capability", label=f"L{i}")
            for i in range(6)
        ]
        ir = LogicalIR(
            corridors=[Corridor(id="domain_experience", label="Experience", leaves=leaves)],
            flows=[
                Flow(source="l0", target="l1"),
                Flow(source="l2", target="l3"),
                Flow(source="l4", target="l5"),
            ],
        )
        geos, _routes = layout_corridor_ir(ir)
        container = geos["domain_experience"]
        for i in range(6):
            leaf = geos[f"l{i}"]
            self.assertLessEqual(leaf.x + leaf.w, container.w + 1e-6, f"l{i} overflows width")
            self.assertLessEqual(leaf.y + leaf.h, container.h + 1e-6, f"l{i} overflows height")

    def test_anchored_shelf_sits_below_anchor(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_data", label="Data", leaves=[
                    Leaf(id="ds", shape_type="logical_datastore", label="Store"),
                ]),
            ],
            anchored=[
                AnchoredCorridor(
                    id="domain_saas",
                    label="External SaaS",
                    anchor_id="domain_data",
                    leaves=[Leaf(id="sf", shape_type="saas_salesforce", label="Salesforce")],
                ),
            ],
            flows=[Flow(source="ds", target="sf")],
        )
        geos, _routes = layout_corridor_ir(ir)
        self.assertGreaterEqual(
            geos["domain_saas"].y,
            geos["domain_data"].y + geos["domain_data"].h - 1.0,
        )


if __name__ == "__main__":
    unittest.main()
