"""Logical L→R order, density IR rules, and dual-audience overview."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from pydantic import ValidationError

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.architecture_docs import (
    assign_logical_shape_types,
    ensure_ir_overview_panel,
    synthesize_ir_overview_body,
)
from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.logical_layout import (
    INTER_CONTAINER_GAP,
    _ROW_START_Y,
    layout_corridor_ir,
)
from app.domain.ir import AnchoredCorridor, Corridor, ExternalActor, Flow, Leaf, LogicalIR
from tests.test_corridor_ir_layout import realistic_logical_ir


def _leaf(lid: str, shape: str, label: str) -> Leaf:
    return Leaf(id=lid, shape_type=shape, label=label)


def _compile(ir: LogicalIR) -> str:
    return compile_corridor_ir(ir, page_name="Logical")


def _abs_xy(cells: dict, cid: str) -> tuple[float, float]:
    """Absolute x/y for a cell (walk parent chain for relative nesting)."""
    x = y = 0.0
    cur = cid
    seen: set[str] = set()
    while cur and cur not in {"0", "1"} and cur not in seen:
        seen.add(cur)
        cell = cells[cur]
        g = cell.find("mxGeometry")
        x += float(g.get("x") or 0)
        y += float(g.get("y") or 0)
        cur = cell.get("parent") or "1"
    return x, y


def _uneven_logical_ir() -> LogicalIR:
    """Tall Core + short Experience/Data with a skip-edge — the shape that
    made ELK stagger corridor Y on real captures (Data dropped ~200px)."""
    return LogicalIR(
        corridors=[
            Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "logical_portal", "Portal")]),
            Corridor(id="domain_core", label="Core", leaves=[
                _leaf("a", "logical_capability", "A"),
                _leaf("b", "logical_capability", "B"),
                _leaf("c", "logical_capability", "C"),
                _leaf("d", "logical_capability", "D"),
            ]),
            Corridor(id="domain_data", label="Data", leaves=[_leaf("store", "logical_datastore", "Store")]),
        ],
        anchored=[AnchoredCorridor(
            id="domain_saas", label="SaaS", anchor_id="domain_data",
            leaves=[_leaf("saas", "logical_saas", "SaaS")],
        )],
        external_actors=[ExternalActor(id="persona", kind="users", label="Persona")],
        flows=[
            Flow(source="persona", target="portal"),
            Flow(source="portal", target="a"),
            Flow(source="a", target="b"),
            Flow(source="b", target="c"),
            Flow(source="c", target="d"),
            Flow(source="d", target="store"),
            Flow(source="portal", target="store"),
            Flow(source="store", target="saas"),
        ],
    )


class LogicalRowOrderTests(unittest.TestCase):
    def test_leaves_read_left_to_right_without_domain_boxes(self) -> None:
        xml = _compile(realistic_logical_ir())
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        for domain in (
            "domain_experience", "domain_core", "domain_data",
            "domain_platform", "domain_saas", "actors_left",
        ):
            self.assertNotIn(domain, cells)
        self.assertIn("cloud_boundary", cells)

        def ax(cid: str) -> float:
            return _abs_xy(cells, cid)[0]

        self.assertLess(ax("portal"), ax("orders"))
        self.assertLess(ax("orders"), ax("warehouse"))
        self.assertLess(ax("persona"), ax("portal"))

    def test_row_corridors_share_one_y_and_height_band(self) -> None:
        geos, _routes = layout_corridor_ir(_uneven_logical_ir())
        row = ("domain_experience", "domain_core", "domain_data")
        ys = [geos[cid].y for cid in row]
        hs = [geos[cid].h for cid in row]
        self.assertTrue(all(abs(y - _ROW_START_Y) < 1.0 for y in ys), ys)
        self.assertTrue(all(abs(h - hs[0]) < 1.0 for h in hs), hs)
        shelf, data = geos["domain_saas"], geos["domain_data"]
        self.assertGreaterEqual(shelf.y, data.y + data.h - 1.0)

    def test_actors_are_loose_and_left_of_storyboard(self) -> None:
        xml = _compile(_uneven_logical_ir())
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertNotIn("actors_left", cells)
        self.assertEqual(cells["persona"].get("parent"), "1")
        # Generic logical_* brief: no cloud_boundary.
        self.assertNotIn("cloud_boundary", cells)
        self.assertEqual(cells["portal"].get("parent"), "1")
        persona_right = float(cells["persona"].find("mxGeometry").get("x") or 0) + geo.LEAF_W
        portal_x = float(cells["portal"].find("mxGeometry").get("x") or 0)
        self.assertLessEqual(persona_right, portal_x)

    def test_leaves_are_leaf_wh_and_fit_parent(self) -> None:
        xml = assign_logical_shape_types(_compile(realistic_logical_ir()))
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        leaf_ids = [
            "portal", "orders", "catalog", "warehouse", "obs", "segment", "persona", "okta",
        ]
        for lid in leaf_ids:
            if lid not in cells:
                continue
            g = cells[lid].find("mxGeometry")
            self.assertAlmostEqual(float(g.get("width") or 0), geo.LEAF_W, delta=1.0, msg=lid)
            self.assertAlmostEqual(float(g.get("height") or 0), geo.LEAF_H, delta=1.0, msg=lid)
            parent = cells[lid].get("parent")
            if parent and parent in cells and parent not in {"1", "0"}:
                pg = cells[parent].find("mxGeometry")
                self.assertLessEqual(
                    float(g.get("x") or 0) + float(g.get("width") or 0),
                    float(pg.get("width") or 0) + 1.0,
                    msg=f"{lid} overflows {parent}",
                )

    def test_peer_corridor_gap_is_routing_channel(self) -> None:
        geos, _ = layout_corridor_ir(realistic_logical_ir())
        ordered = sorted(
            ("domain_experience", "domain_core", "domain_data", "domain_platform"),
            key=lambda cid: geos[cid].x,
        )
        for a, b in zip(ordered, ordered[1:]):
            gap = geos[b].x - (geos[a].x + geos[a].w)
            self.assertGreaterEqual(gap, INTER_CONTAINER_GAP - 15.0, msg=f"{a}->{b} gap={gap}")

    def test_rejects_both_core_and_commerce(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[
                    Corridor(id="domain_experience", label="Experience", leaves=[_leaf("p", "logical_portal", "Portal")]),
                    Corridor(id="domain_core", label="Core", leaves=[_leaf("a", "logical_capability", "A")]),
                    Corridor(id="domain_commerce", label="Commerce", leaves=[_leaf("b", "logical_capability", "B")]),
                    Corridor(id="domain_data", label="Data", leaves=[_leaf("d", "logical_datastore", "Store")]),
                ],
                flows=[Flow(source="p", target="a"), Flow(source="a", target="d")],
            )

    def test_density_is_a_note_not_an_error(self) -> None:
        """A crowded domain is logged, not rejected. It was an error, which
        failed the whole request over a readability preference — and a 24-leaf
        Logical page renders correctly, so the cap was never a real limit."""
        leaves = [_leaf(f"c{i}", "logical_capability", f"Cap{i}") for i in range(6)]
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[_leaf("p", "logical_portal", "Portal")]),
                Corridor(id="domain_core", label="Core", leaves=leaves),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("d", "logical_datastore", "Store")]),
            ],
            flows=[Flow(source="p", target="c0"), Flow(source="c0", target="d")],
        )
        # (c1..c5 are orphans here, which is a separate and still-hard rule —
        # the point is that nothing complains about the *count*.)
        self.assertEqual([e for e in ir.semantic_errors() if "keep ≤" in e], [])
        self.assertTrue(any("domain_core" in n for n in ir.density_notes()), ir.density_notes())
        # And it must not buy a corrective LLM pass either — "fixing" density
        # means dropping capabilities the request named.
        self.assertNotIn("domain_core", " ".join(ir.semantic_warnings()))

    def test_a_long_journey_compiles(self) -> None:
        """24 leaves, 8 per corridor — well past both former caps."""
        corridors = [
            Corridor(
                id=f"domain_{name}", label=name.title(),
                leaves=[_leaf(f"{name[:2]}{i}", "logical_capability", f"Cap {i}") for i in range(8)],
            )
            for name in ("experience", "core", "data")
        ]
        ir = LogicalIR(corridors=corridors)
        ids = [lf.id for c in corridors for lf in c.leaves]
        ir.flows = [Flow(source=a, target=b) for a, b in zip(ids, ids[1:])]
        self.assertEqual(ir.semantic_errors(), [])
        self.assertIn('vertex="1"', _compile(ir))


class LogicalOverviewTests(unittest.TestCase):
    def test_overview_has_business_and_technical(self) -> None:
        xml = _compile(realistic_logical_ir())
        body = synthesize_ir_overview_body(xml, "Logical", "Demo System")
        self.assertIn("Business journey", body)
        self.assertIn("Technical flow", body)
        self.assertIn("Business:", body)
        self.assertIn("Portal", body)
        # realistic_logical_ir() carries its own business_notes now — the
        # panel must prefer that LLM-authored wording over the mechanical
        # "Core work is delivered by..." template (see
        # test_llm_authored_business_notes_win_over_the_mechanical_fallback).
        self.assertIn("Customers sign in through Okta", body)
        self.assertIn("cloud boundary", body.lower())
        # Facts line for flat Logical mentions services/flows (not domain counts).
        self.assertTrue("service" in body.lower() or "flow" in body.lower())
        out = ensure_ir_overview_panel(xml, "Logical", "Demo System")
        self.assertIn("overview_panel", out)

    def test_llm_authored_business_notes_win_over_the_mechanical_fallback(self) -> None:
        """When the LLM populates business_notes, use its own wording instead
        of the generic template sentences ("Users reach the product via X.")
        — richer, per-request, defines terms and explains why, not just what."""
        ir = realistic_logical_ir()
        ir.business_notes = [
            "Customers sign in through the Portal — Cognito confirms who they "
            "are before anything else happens, so no one can act on someone "
            "else's account.",
        ]
        xml = _compile(ir)
        body = synthesize_ir_overview_body(xml, "Logical", "Demo System")
        self.assertIn("Business journey", body)
        self.assertIn("Cognito confirms who they are", body)
        self.assertNotIn("Core work is delivered by", body)

    def test_empty_business_notes_falls_back_to_mechanical_steps(self) -> None:
        """business_notes defaults to [] — Logical must never regress to
        showing nothing just because the LLM omitted it this turn.

        Uses a single-leaf IR (below the 2-leaf threshold semantic_errors()
        now enforces business_notes at) purely so this stays compilable with
        business_notes genuinely empty — real-sized diagrams are now required
        to populate it (see NetworkIR/CorridorIR.semantic_errors), so this
        fallback mainly covers small fixtures and any validation-retry edge
        case, not the common path anymore."""
        ir = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[_leaf("orders", "aws_lambda", "Orders")])],
            external_actors=[ExternalActor(id="persona", kind="users", label="Persona")],
            flows=[Flow(source="persona", target="orders")],
        )
        self.assertEqual(ir.business_notes, [])
        xml = _compile(ir)
        body = synthesize_ir_overview_body(xml, "Logical", "Demo System")
        self.assertIn("Business journey", body)
        self.assertIn("Core work", body)


if __name__ == "__main__":
    unittest.main()
