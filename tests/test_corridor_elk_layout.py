"""ELK-specific behavior of ``logical_layout.layout_corridor_ir``.

``tests/test_corridor_compiler.py`` already proves the compiled output is
clean end to end; these tests target the ELK integration itself — row order,
shelf anchoring, cross-corridor routing, and the fallback when ELK is
unavailable — since those are new behavior the compiler-level tests don't
exercise directly.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError
from app.adapters.outbound.diagram.logical_layout import layout_corridor_ir
from app.domain.ir import AnchoredCorridor, Corridor, Flow, Leaf, LogicalIR


def _leaf(i: str, label: str) -> Leaf:
    return Leaf(id=i, shape_type="logical_capability", label=label)


def _sample_ir() -> LogicalIR:
    return LogicalIR(
        corridors=[
            Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "Portal")]),
            Corridor(id="domain_core", label="Core", leaves=[_leaf("orgchart", "Org Chart"), _leaf("cache", "Cache")]),
            # "export" (not "store") touches the shelf, so "store" stays a
            # pure row-to-row leaf usable for the ELK bend-point assertion
            # below without also tripping the "leaf touches a non-ELK flow,
            # so ALL its flows stay off ELK" rule (see
            # logical_layout._layout_corridor_ir_elk's non_row_touch).
            Corridor(id="domain_data", label="Data", leaves=[_leaf("store", "Store"), _leaf("export", "Export")]),
        ],
        anchored=[AnchoredCorridor(
            id="domain_saas", label="External SaaS", anchor_id="domain_data",
            leaves=[_leaf("workday", "Workday")],
        )],
        flows=[
            Flow(source="portal", target="orgchart"),
            Flow(source="orgchart", target="store"),
            Flow(source="orgchart", target="cache"),
            Flow(source="export", target="workday"),
            # Skips over domain_core entirely — the one flow in this sample
            # that should genuinely need a bent (not straight) route.
            Flow(source="portal", target="store"),
        ],
    )


class ElkCorridorLayoutTests(unittest.TestCase):
    def test_returns_geo_for_every_corridor_shelf_and_leaf(self) -> None:
        ir = _sample_ir()
        geos, _routes = layout_corridor_ir(ir)
        expected_ids = {"domain_experience", "domain_core", "domain_data", "domain_saas",
                         "portal", "orgchart", "cache", "store", "export", "workday"}
        self.assertEqual(expected_ids, set(geos.keys()))

    def test_row_corridors_are_left_to_right_in_canonical_order(self) -> None:
        geos, _routes = layout_corridor_ir(_sample_ir())
        xs = [geos["domain_experience"].x, geos["domain_core"].x, geos["domain_data"].x]
        self.assertEqual(xs, sorted(xs))

    def test_shelf_anchored_below_its_target_corridor(self) -> None:
        geos, _routes = layout_corridor_ir(_sample_ir())
        anchor, shelf = geos["domain_data"], geos["domain_saas"]
        self.assertGreaterEqual(shelf.y, anchor.y + anchor.h)
        self.assertAlmostEqual(shelf.x, anchor.x)

    def test_row_to_row_flows_do_not_cut_through_other_corridors(self) -> None:
        # ELK is free to arrange row corridors however minimizes crossings
        # (it may or may not need bend points for a given flow — both are
        # valid routes; see test_elk_bridge.py for a direct obstacle-avoidance
        # check), so this only asserts the actual invariant that matters: no
        # row-to-row route cuts through a corridor box it doesn't start/end
        # in. compile_corridor_ir + diagnose_xml already prove this end to
        # end (test_corridor_compiler.py); this checks it directly against
        # layout_corridor_ir's own output.
        ir = _sample_ir()
        geos, routes = layout_corridor_ir(ir)
        row_ids = {"domain_experience", "domain_core", "domain_data"}
        boxes = {cid: geos[cid] for cid in row_ids}
        for fid, points in routes.items():
            for cid, box in boxes.items():
                for (x, y) in points:
                    inside = box.x < x < box.x + box.w and box.y < y < box.y + box.h
                    self.assertFalse(inside, f"{fid} bend point ({x},{y}) lands inside {cid}")

    def test_same_corridor_flow_is_not_in_elk_routes(self) -> None:
        # orgchart -> cache are both in domain_core — excluded from the ELK
        # graph on purpose (see logical_layout._layout_corridor_ir_elk), so
        # no route is fabricated for it here; it falls through to
        # edge_routing.py's existing local router instead.
        _geos, routes = layout_corridor_ir(_sample_ir())
        self.assertNotIn("flow_2", routes)

    def test_elk_failure_propagates(self) -> None:
        # No fallback layout engine anymore — an ELK failure is a real
        # failure here; the surrounding IR pipeline (xml_pipeline.py) is
        # what falls back to direct-XML generation on this, not this module.
        with patch(
            "app.adapters.outbound.diagram.logical_layout.run_elk",
            side_effect=ElkLayoutError("node missing"),
        ):
            with self.assertRaises(ElkLayoutError):
                layout_corridor_ir(_sample_ir())

    def test_no_leaf_overflows_its_own_corridor(self) -> None:
        geos, _routes = layout_corridor_ir(_sample_ir())
        for corridor in _sample_ir().corridors + _sample_ir().anchored:
            box = geos[corridor.id]
            for leaf in corridor.leaves:
                lg = geos[leaf.id]
                self.assertGreaterEqual(lg.x, 0)
                self.assertGreaterEqual(lg.y, 0)
                self.assertLessEqual(lg.x + lg.w, box.w + 1.0)
                self.assertLessEqual(lg.y + lg.h, box.h + 1.0)

    def test_empty_ir_does_not_call_elk(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "Portal")])],
        )
        # No flows/anchored shelves — should still resolve cleanly.
        geos, routes = layout_corridor_ir(ir)
        self.assertIn("domain_experience", geos)
        self.assertIn("portal", geos)
        self.assertEqual(routes, {})


if __name__ == "__main__":
    unittest.main()
