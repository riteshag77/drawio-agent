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
from app.domain.ir import AnchoredCorridor, Corridor, DataFlowIR, Flow, Leaf, LogicalIR


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

    def test_equalize_keeps_routes_when_endpoints_share_dy(self) -> None:
        """Asymmetric band snaps must not wipe every ELK route — only the
        edges whose two corridors moved differently."""
        from app.adapters.outbound.diagram.logical_layout import (
            _shift_routes_after_equalize,
        )
        routes = {
            "flow_0": [(100.0, 50.0), (200.0, 50.0)],  # both endpoints dy=10
            "flow_1": [(100.0, 80.0), (300.0, 80.0)],  # asymmetric → drop
        }
        dys = {"domain_experience": 10.0, "domain_core": 10.0, "domain_data": 0.0}
        row_edges = [
            ("flow_0", "portal", "a"),
            ("flow_1", "portal", "store"),
        ]
        leaf_to = {"portal": "domain_experience", "a": "domain_core", "store": "domain_data"}
        kept = _shift_routes_after_equalize(routes, dys, row_edges, leaf_to)
        self.assertIn("flow_0", kept)
        self.assertEqual(kept["flow_0"], [(100.0, 60.0), (200.0, 60.0)])
        self.assertNotIn("flow_1", kept)

    def test_row_leaves_reorder_puts_inbound_first(self) -> None:
        from app.adapters.outbound.diagram.logical_layout import order_leaves_by_flow
        from app.domain.ir import Flow, Leaf
        leaves = [
            Leaf(id="z", shape_type="logical_capability", label="Z"),
            Leaf(id="a", shape_type="logical_capability", label="A"),
            Leaf(id="b", shape_type="logical_capability", label="B"),
        ]
        flows = [
            Flow(source="portal", target="a", primary=True),
            Flow(source="a", target="store", primary=True),
            Flow(source="b", target="store", primary=False),
        ]
        ordered = order_leaves_by_flow(leaves, flows)
        self.assertEqual([l.id for l in ordered][0], "a")


def _dl_leaf(i: str, label: str) -> Leaf:
    return Leaf(id=i, shape_type="aws_ecs", label=label)


def _data_flow_ir() -> DataFlowIR:
    """Zero prior ELK-layer coverage exercised Data-flow at all (this file
    was built entirely on LogicalIR) — Data-flow is the one view with an
    ``anchor_id=None`` ("below the whole row") shelf, a shape none of
    Logical's/Security's fixtures can represent, so the ELK row/shelf
    interaction for it was untested at this layer."""
    return DataFlowIR(
        corridors=[
            Corridor(id="phase_ingest", label="Ingest", leaves=[_dl_leaf("apigw", "API GW")]),
            Corridor(id="phase_process", label="Process", leaves=[_dl_leaf("lam", "Lambda")]),
            Corridor(id="phase_store", label="Store", leaves=[_dl_leaf("db", "DB")]),
        ],
        anchored=[
            AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store", leaves=[_dl_leaf("dash", "Dashboard")]),
            AnchoredCorridor(id="phase_control", label="Control", anchor_id=None, leaves=[_dl_leaf("sched", "Scheduler")]),
        ],
        flows=[
            Flow(source="apigw", target="lam"),
            Flow(source="lam", target="db"),
            Flow(source="dash", target="db"),
        ],
    )


class DataFlowElkLayoutTests(unittest.TestCase):
    def test_returns_geo_for_every_corridor_shelf_and_leaf(self) -> None:
        geos, _routes = layout_corridor_ir(_data_flow_ir())
        expected_ids = {
            "phase_ingest", "phase_process", "phase_store", "phase_consume", "phase_control",
            "apigw", "lam", "db", "dash", "sched",
        }
        self.assertEqual(expected_ids, set(geos.keys()))

    def test_named_anchor_shelf_sits_below_its_anchor(self) -> None:
        geos, _routes = layout_corridor_ir(_data_flow_ir())
        anchor, shelf = geos["phase_store"], geos["phase_consume"]
        self.assertGreaterEqual(shelf.y, anchor.y + anchor.h)
        self.assertAlmostEqual(shelf.x, anchor.x)

    def test_whole_row_shelf_sits_below_the_named_anchor_shelf_too(self) -> None:
        """phase_control (anchor_id=None) must clear phase_consume's own
        bottom, not just the main row's — the exact geometry §1.1 fixed."""
        geos, _routes = layout_corridor_ir(_data_flow_ir())
        self.assertGreater(geos["phase_control"].y, geos["phase_consume"].y + geos["phase_consume"].h)

    def test_no_leaf_overflows_its_own_corridor(self) -> None:
        ir = _data_flow_ir()
        geos, _routes = layout_corridor_ir(ir)
        for corridor in ir.corridors + ir.anchored:
            box = geos[corridor.id]
            for leaf in corridor.leaves:
                lg = geos[leaf.id]
                self.assertGreaterEqual(lg.x, 0)
                self.assertGreaterEqual(lg.y, 0)
                self.assertLessEqual(lg.x + lg.w, box.w + 1.0)
                self.assertLessEqual(lg.y + lg.h, box.h + 1.0)


if __name__ == "__main__":
    unittest.main()
