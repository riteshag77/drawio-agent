"""Generic (as-is image replication) battle-test coverage.

Every case here was a measured defect before the Generic correctness audit,
and they share one shape: the view whose whole contract is "reproduce what the
user drew" was silently changing or discarding it — deleting edges, relabelling
blank shapes, rewiring connections, or exploding the page.
"""

from __future__ import annotations

import json
import re
import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.compiler.generic import (
    _place_proportional,
    _place_topological,
    compile_generic_ir,
)
from app.adapters.outbound.diagram.xml_diagnostics import repair_xml
from app.domain.ir.generic import (
    GenericDiagramIR,
    GenericEdge,
    GenericGroup,
    GenericNode,
)

_PROMPT = "config/xml_gen/ir/generic.txt"


def _node(nid: str, **kw) -> GenericNode:
    kw.setdefault("shape", "rectangle")
    kw.setdefault("label", nid.title())
    return GenericNode(id=nid, **kw)


def _cells(xml: str) -> dict[str, ET.Element]:
    return {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}


def _edges(xml: str) -> list[tuple[str, str, str]]:
    return [
        (c.get("id"), c.get("source"), c.get("target"))
        for c in ET.fromstring(xml).iter("mxCell") if c.get("edge") == "1"
    ]


def _geom(cell: ET.Element) -> tuple[float, float, float, float]:
    g = cell.find("mxGeometry")
    return tuple(float(g.get(k) or 0) for k in ("x", "y", "width", "height"))  # type: ignore[return-value]


def _rows(nodes: list[GenericNode], edges: list[GenericEdge]) -> dict[str, int]:
    """Node id -> its layout row, for the unhinted topological fallback."""
    positions = _place_topological(nodes, edges)
    return {n.id: int(positions[n.id][1] // 130) for n in nodes}


class RepairMustNotEatGenericContentTests(unittest.TestCase):
    """The two cloud-semantic repair steps, which silently destroyed content
    on this view. Both sides are asserted: Generic preserved, cloud unchanged.
    """

    def _org_chart(self) -> GenericDiagramIR:
        return GenericDiagramIR(
            title="Org", nodes=[
                _node("cto", label="CTO", group="mgmt"),
                _node("vp", label="", group="mgmt"),
            ],
            groups=[GenericGroup(id="mgmt", label="Management Team")],
            edges=[GenericEdge(source="cto", target="vp", label="reports to")],
        )

    def test_management_group_keeps_its_edges(self) -> None:
        """remove_cross_cutting_edges matches the *first word* of a container
        label against security/monitoring/integration/management — a Network
        rule. An ordinary org-chart swimlane lost every edge, silently."""
        xml = compile_generic_ir(self._org_chart())
        repaired, diagnostics = repair_xml(xml, view="generic")
        self.assertEqual(len(_edges(repaired)), 1)
        self.assertEqual(diagnostics.errors, [])

    def test_blank_label_is_not_renamed_to_service(self) -> None:
        """generic.txt permits an unlabeled shape; ensure_leaf_labels filled
        it with the cloud vocabulary word "Service"."""
        repaired, _ = repair_xml(compile_generic_ir(self._org_chart()), view="generic")
        self.assertEqual(_cells(repaired)["vp"].get("value"), "")

    def test_cloud_pages_keep_both_behaviours(self) -> None:
        """The guard is view-scoped: without view="generic" the cloud rules
        must still fire, or this fix would have weakened four other views."""
        xml = compile_generic_ir(self._org_chart())
        repaired, _ = repair_xml(xml)
        self.assertEqual(_edges(repaired), [])
        self.assertEqual(_cells(repaired)["vp"].get("value"), "Service")


class EdgeIdCollisionTests(unittest.TestCase):
    """1.2 — generated edge ids used to collide with model-authored node ids."""

    def test_node_named_edge_0_keeps_its_id_and_its_connection(self) -> None:
        ir = GenericDiagramIR(
            title="T", nodes=[_node("edge_0", label="Real Node"), _node("b")],
            edges=[GenericEdge(source="edge_0", target="b")],
        )
        self.assertEqual(ir.semantic_errors(), [])
        repaired, _ = repair_xml(compile_generic_ir(ir), view="generic")
        cells = _cells(repaired)
        self.assertEqual(cells["edge_0"].get("vertex"), "1")
        self.assertEqual(cells["edge_0"].get("value"), "Real Node")
        edge_ids = [eid for eid, _s, _t in _edges(repaired)]
        self.assertEqual(len(edge_ids), 1)
        self.assertNotEqual(edge_ids[0], "edge_0")
        self.assertEqual(_edges(repaired)[0][1:], ("edge_0", "b"))


class CanvasClampTests(unittest.TestCase):
    """1.3 — near-identical hints used to produce a six-figure page."""

    def _page_width(self, nodes: list[GenericNode]) -> float:
        xml = compile_generic_ir(GenericDiagramIR(title="T", nodes=nodes, edges=[]))
        return float(re.search(r'pageWidth="([0-9.]+)"', xml).group(1))

    def test_near_identical_hints_do_not_explode_the_page(self) -> None:
        width = self._page_width([
            _node("a", x=0.5, y=0.1), _node("b", x=0.5001, y=0.1),
        ])
        self.assertLess(width, 5000, f"page blew up to {width}px")

    def test_genuinely_spread_hints_still_keep_rows_distinct(self) -> None:
        """The widening this clamp bounds exists for a real reason — a 6-node
        flowchart over 4 y-levels must not collapse or scatter sideways."""
        nodes = [
            _node("n1", shape="ellipse", x=0.5, y=0.05),
            _node("n2", x=0.5, y=0.3), _node("n3", shape="diamond", x=0.5, y=0.55),
            _node("n4", x=0.25, y=0.8), _node("n5", x=0.75, y=0.8),
            _node("n6", shape="ellipse", x=0.5, y=0.95),
        ]
        positions = _place_proportional(nodes, {n.id: (78.0, 78.0) for n in nodes})
        self.assertEqual(len({round(p[1]) for p in positions.values()}), 5)
        self.assertGreater(len({round(p[0]) for p in positions.values()}), 1)


class IconFidelityTests(unittest.TestCase):
    """1.4 — MCP wins on fill/stroke in merge_drawio_styles, so resolving an
    icon silently replaced a colour traced from the source image."""

    def _shape_type(self, node: GenericNode) -> str | None:
        xml = compile_generic_ir(GenericDiagramIR(title="T", nodes=[node], edges=[]))
        return _cells(xml)[node.id].get("shape_type")

    def test_icon_hint_resolves_on_a_plain_rectangle(self) -> None:
        self.assertEqual(
            self._shape_type(_node("a", icon_hint="aws s3 bucket")), "aws s3 bucket",
        )

    def test_traced_colour_wins_over_the_icon(self) -> None:
        self.assertIsNone(
            self._shape_type(_node("a", icon_hint="aws s3 bucket", fill_color="#3B82F6")),
        )

    def test_keyword_styled_shapes_skip_resolution(self) -> None:
        """rhombus;/ellipse;/text; are bare keywords, so an MCP `shape=` lands
        alongside them instead of replacing — icon doesn't render and a
        borderless text cell gains a fill."""
        for shape in ("diamond", "ellipse", "cloud", "text", "actor"):
            with self.subTest(shape=shape):
                self.assertIsNone(self._shape_type(_node("a", shape=shape, icon_hint="slack")))


class SelfLoopTests(unittest.TestCase):
    """1.5 — a retry arrow is ordinary flowchart content, not an error."""

    def test_self_loop_survives_validation_compile_and_repair(self) -> None:
        ir = GenericDiagramIR(
            title="Retry", nodes=[_node("validate", shape="diamond"), _node("done", shape="ellipse")],
            edges=[
                GenericEdge(source="validate", target="validate", label="retry"),
                GenericEdge(source="validate", target="done", label="ok"),
            ],
        )
        self.assertEqual(ir.semantic_errors(), [])
        repaired, diagnostics = repair_xml(compile_generic_ir(ir), view="generic")
        self.assertIn(("validate", "validate"), [(s, t) for _e, s, t in _edges(repaired)])
        self.assertEqual(diagnostics.errors, [])


class TopologicalLayoutTests(unittest.TestCase):
    """2.1 — the unhinted fallback, on the graphs this view actually targets."""

    def test_fully_cyclic_graph_still_layers(self) -> None:
        """No indegree-0 node meant nothing seeded the walk and every node
        collapsed onto row 0."""
        nodes = [_node(i) for i in "abcd"]
        edges = [GenericEdge(source=s, target=t) for s, t in (("a", "b"), ("b", "c"), ("c", "d"), ("d", "a"))]
        self.assertEqual(_rows(nodes, edges), {"a": 0, "b": 1, "c": 2, "d": 3})

    def test_retry_loop_mid_flow_layers_forward(self) -> None:
        nodes = [_node("start"), _node("check"), _node("done")]
        edges = [GenericEdge(source=s, target=t) for s, t in
                 (("start", "check"), ("check", "start"), ("check", "done"))]
        self.assertEqual(_rows(nodes, edges), {"start": 0, "check": 1, "done": 2})

    def test_reconvergent_branch_puts_children_below_parents(self) -> None:
        """A depth increase must re-propagate, or d shares a row with its own
        parent b."""
        nodes = [_node(i) for i in "abcd"]
        edges = [GenericEdge(source=s, target=t) for s, t in
                 (("a", "b"), ("a", "c"), ("c", "b"), ("b", "d"))]
        rows = _rows(nodes, edges)
        self.assertGreater(rows["d"], rows["b"])
        self.assertGreater(rows["b"], rows["c"])

    def test_disconnected_components_both_start_at_the_top(self) -> None:
        nodes = [_node(i) for i in "abcd"]
        edges = [GenericEdge(source="a", target="b"), GenericEdge(source="c", target="d")]
        rows = _rows(nodes, edges)
        self.assertEqual((rows["a"], rows["c"]), (0, 0))

    def test_long_chain_does_not_exhaust_the_stack(self) -> None:
        """The back-edge sweep is iterative; nothing caps node count here."""
        nodes = [_node(f"n{i}") for i in range(2000)]
        edges = [GenericEdge(source=f"n{i}", target=f"n{i+1}") for i in range(1999)]
        self.assertEqual(_rows(nodes, edges)["n1999"], 1999)


class PartialHintTests(unittest.TestCase):
    """2.2 — x-only and y-only nodes all shared one fallback slot."""

    def test_partially_hinted_nodes_get_distinct_slots(self) -> None:
        nodes = [_node("a", x=0.1), _node("b", x=0.5), _node("c", x=0.9), _node("d", y=0.9)]
        positions = _place_proportional(nodes, {n.id: (78.0, 78.0) for n in nodes})
        self.assertEqual(len({(round(p[0]), round(p[1])) for p in positions.values()}), 4)


class GroupPositionTests(unittest.TestCase):
    """2.3 — GenericGroup had no x/y, so groups were laid out left to right
    at a fixed Y no matter where the boundary sat in the source image."""

    def _origins(self, ir: GenericDiagramIR) -> dict[str, tuple[float, float]]:
        cells = _cells(compile_generic_ir(ir))
        ids = [g.id for g in ir.groups] + [n.id for n in ir.nodes if not n.group]
        return {cid: _geom(cells[cid])[:2] for cid in ids}

    def test_bottom_of_image_group_renders_below_a_top_node(self) -> None:
        ir = GenericDiagramIR(
            title="Org",
            nodes=[_node("cto", label="CTO", x=0.5, y=0.05),
                   _node("e1", group="team"), _node("e2", group="team")],
            groups=[GenericGroup(id="team", label="Platform Team", x=0.1, y=0.85)],
        )
        origins = self._origins(ir)
        self.assertGreater(origins["team"][1], origins["cto"][1])

    def test_group_hints_order_boxes_in_both_axes(self) -> None:
        ir = GenericDiagramIR(
            title="T", nodes=[_node("a", group="tl"), _node("b", group="br")],
            groups=[GenericGroup(id="tl", label="Top Left", x=0.0, y=0.0),
                    GenericGroup(id="br", label="Bottom Right", x=1.0, y=1.0)],
        )
        origins = self._origins(ir)
        self.assertGreater(origins["br"][0], origins["tl"][0])
        self.assertGreater(origins["br"][1], origins["tl"][1])

    def test_unhinted_groups_keep_the_left_to_right_fallback(self) -> None:
        ir = GenericDiagramIR(
            title="T", nodes=[_node("a", group="g1"), _node("b", group="g2")],
            groups=[GenericGroup(id="g1", label="One"), GenericGroup(id="g2", label="Two")],
        )
        origins = self._origins(ir)
        self.assertEqual(origins["g1"][1], origins["g2"][1])
        self.assertGreater(origins["g2"][0], origins["g1"][0])

    def test_grouped_nodes_still_nest_under_their_box(self) -> None:
        ir = GenericDiagramIR(
            title="T", nodes=[_node("a", group="g1"), _node("loose")],
            groups=[GenericGroup(id="g1", label="One", x=0.9, y=0.9)],
        )
        cells = _cells(compile_generic_ir(ir))
        self.assertEqual(cells["a"].get("parent"), "g1")
        self.assertEqual(cells["loose"].get("parent"), "1")


class PromptExampleTests(unittest.TestCase):
    """Every worked example in generic.txt must be something the code can
    actually produce — the same self-inconsistency found in all four prior
    prompt packs."""

    def _examples(self) -> list[dict]:
        text = open(_PROMPT, encoding="utf-8").read()
        return [json.loads(b) for b in re.findall(r"```json\n(\{.*?\n\})\n```", text, re.S)]

    def test_examples_parse_compile_and_survive_repair(self) -> None:
        examples = self._examples()
        self.assertEqual(len(examples), 3)
        for raw in examples:
            with self.subTest(title=raw.get("title")):
                ir = GenericDiagramIR.model_validate(raw)
                self.assertEqual(ir.semantic_errors(), [])
                repaired, diagnostics = repair_xml(compile_generic_ir(ir), view="generic")
                self.assertEqual(diagnostics.errors, [])
                # Nothing the example declares may be dropped on the way out.
                self.assertEqual(len(_edges(repaired)), len(ir.edges))

    def test_prompt_documents_the_id_rules_it_hard_fails_on(self) -> None:
        """A malformed or duplicate id is a fatal ValidationError with no
        corrective retry, and the pack used to cite a schema section that did
        not exist."""
        text = open(_PROMPT, encoding="utf-8").read()
        self.assertIn("Field reference", text)
        self.assertNotIn("schema below", text)


if __name__ == "__main__":
    unittest.main()
