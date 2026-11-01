"""GenericDiagramIR -> draw.io compiler (as-is image replication path)."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.compiler import compile_generic_ir
from app.adapters.outbound.diagram.compiler.generic import CompilerError
from app.domain.ir.generic import GenericDiagramIR, GenericEdge, GenericGroup, GenericNode, GenericNodeShape


def _node(i: str, shape: GenericNodeShape = "rectangle", **kwargs) -> GenericNode:
    return GenericNode(id=i, shape=shape, label=i, **kwargs)


class CompileGenericIrTests(unittest.TestCase):
    def test_invalid_ir_raises_compiler_error(self) -> None:
        with self.assertRaises(CompilerError):
            compile_generic_ir(GenericDiagramIR(nodes=[]))

    def test_flat_flowchart_compiles_to_valid_xml(self) -> None:
        ir = GenericDiagramIR(
            title="Approval Flow",
            nodes=[
                _node("start", "rounded_rectangle", x=0.2, y=0.1),
                _node("check", "diamond", x=0.2, y=0.5),
                _node("done", "rounded_rectangle", x=0.2, y=0.9),
            ],
            edges=[
                GenericEdge(source="start", target="check", label="next"),
                GenericEdge(source="check", target="done", label="Yes", dashed=True, line_style="curved"),
            ],
        )
        xml = compile_generic_ir(ir)
        root = ET.fromstring(xml)  # raises if malformed
        cell_ids = {c.get("id") for c in root.iter("mxCell")}
        for node_id in ("start", "check", "done"):
            self.assertIn(node_id, cell_ids)
        edge_cells = [c for c in root.iter("mxCell") if c.get("edge") == "1"]
        self.assertEqual(len(edge_cells), 2)

    def test_layout_is_marked_authoritative(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a")])
        root = ET.fromstring(compile_generic_ir(ir))
        layer = next(c for c in root.iter("mxCell") if c.get("id") == "1")
        self.assertEqual(layer.get("layout"), "ir")

    def test_every_shape_maps_to_a_distinct_style(self) -> None:
        shapes: list[GenericNodeShape] = [
            "rectangle", "rounded_rectangle", "ellipse", "diamond", "parallelogram",
            "hexagon", "cylinder", "cloud", "actor", "text",
        ]
        ir = GenericDiagramIR(nodes=[_node(f"n{i}", shape) for i, shape in enumerate(shapes)])
        root = ET.fromstring(compile_generic_ir(ir))
        styles = {}
        for i, shape in enumerate(shapes):
            cell = next(c for c in root.iter("mxCell") if c.get("id") == f"n{i}")
            styles[shape] = cell.get("style")
        self.assertEqual(len(set(styles.values())), len(shapes), f"duplicate styles: {styles}")

    def test_grid_fallback_places_nodes_without_overlap(self) -> None:
        ir = GenericDiagramIR(nodes=[_node(f"n{i}") for i in range(6)])
        root = ET.fromstring(compile_generic_ir(ir))
        positions = set()
        for i in range(6):
            cell = next(c for c in root.iter("mxCell") if c.get("id") == f"n{i}")
            geom = cell.find("mxGeometry")
            positions.add((geom.get("x"), geom.get("y")))
        self.assertEqual(len(positions), 6)

    def test_unhinted_nodes_follow_edge_order_not_list_order(self) -> None:
        """Regression test: a linear chain with two branch points (the
        classic "lamp doesn't work" flowchart) previously landed the first
        four nodes on one row regardless of which pointed to which, because
        the no-x/y fallback packed nodes in raw list order into a fixed
        <=4-per-row grid. It must now read the edges instead, since vision
        models leave x/y null far more often than not."""
        ir = GenericDiagramIR(
            nodes=[
                _node("start", "rounded_rectangle"),
                _node("plugged_in", "diamond"),
                _node("plug_in", "rounded_rectangle"),
                _node("bulb_burned", "diamond"),
                _node("replace_bulb", "rounded_rectangle"),
                _node("repair_lamp", "rounded_rectangle"),
            ],
            edges=[
                GenericEdge(source="start", target="plugged_in"),
                GenericEdge(source="plugged_in", target="plug_in", label="No"),
                GenericEdge(source="plugged_in", target="bulb_burned", label="Yes"),
                GenericEdge(source="bulb_burned", target="replace_bulb", label="Yes"),
                GenericEdge(source="bulb_burned", target="repair_lamp", label="No"),
            ],
        )
        root = ET.fromstring(compile_generic_ir(ir))

        def y_of(node_id: str) -> str:
            cell = next(c for c in root.iter("mxCell") if c.get("id") == node_id)
            return cell.find("mxGeometry").get("y")

        # Each hop down the chain must land on a strictly later row — the
        # regression put start/plugged_in/plug_in/bulb_burned all on row 0.
        rows = [y_of("start"), y_of("plugged_in"), y_of("plug_in"), y_of("replace_bulb")]
        self.assertEqual(len(set(rows)), 4, f"expected 4 distinct rows, got {rows}")
        # Sibling branches (children of the same diamond) share their row.
        self.assertEqual(y_of("plug_in"), y_of("bulb_burned"))
        self.assertEqual(y_of("replace_bulb"), y_of("repair_lamp"))

    def test_hinted_x_y_are_respected(self) -> None:
        ir = GenericDiagramIR(nodes=[
            _node("a", x=0.1, y=0.1),
            _node("b", x=0.9, y=0.1),
            _node("c", x=0.1, y=0.9),
        ])
        root = ET.fromstring(compile_generic_ir(ir))
        geoms = {
            nid: next(c for c in root.iter("mxCell") if c.get("id") == nid).find("mxGeometry")
            for nid in ("a", "b", "c")
        }
        # Same y hint -> same y; different x hint -> different x.
        self.assertEqual(geoms["a"].get("y"), geoms["b"].get("y"))
        self.assertNotEqual(geoms["a"].get("x"), geoms["b"].get("x"))
        # Different y hint -> different y.
        self.assertNotEqual(geoms["a"].get("y"), geoms["c"].get("y"))

    def test_hinted_nodes_sharing_an_axis_value_stay_aligned(self) -> None:
        """Regression test: a real capture pair on the same source image
        showed a model that committed to x/y hints (a 6-node lamp-troubleshooting
        flowchart, hints spanning y=0.05..0.9) rendering *worse* than one that
        abstained entirely — grid_size's dense <=4-column canvas assumption
        packed the hints so tightly that _resolve_collision's x-only push
        scattered nodes that shared the same x hint onto different columns.
        Two nodes on the same intended column/row must stay aligned."""
        ir = GenericDiagramIR(
            nodes=[
                _node("start", "rounded_rectangle", x=0.35, y=0.05),
                _node("check_plugged", "diamond", x=0.35, y=0.3),
                _node("plug_in", "rounded_rectangle", x=0.75, y=0.3),
                _node("check_bulb", "diamond", x=0.35, y=0.6),
                _node("replace_bulb", "rounded_rectangle", x=0.75, y=0.6),
                _node("repair_lamp", "rounded_rectangle", x=0.35, y=0.9),
            ],
            edges=[
                GenericEdge(source="start", target="check_plugged"),
                GenericEdge(source="check_plugged", target="plug_in", label="No"),
                GenericEdge(source="check_plugged", target="check_bulb", label="Yes"),
                GenericEdge(source="check_bulb", target="replace_bulb", label="Yes"),
                GenericEdge(source="check_bulb", target="repair_lamp", label="No"),
            ],
        )
        root = ET.fromstring(compile_generic_ir(ir))

        def x_of(node_id: str) -> str:
            cell = next(c for c in root.iter("mxCell") if c.get("id") == node_id)
            return cell.find("mxGeometry").get("x")

        left_column = {x_of("start"), x_of("check_plugged"), x_of("check_bulb"), x_of("repair_lamp")}
        right_column = {x_of("plug_in"), x_of("replace_bulb")}
        self.assertEqual(len(left_column), 1, f"left column (x=0.35) scattered: {left_column}")
        self.assertEqual(len(right_column), 1, f"right column (x=0.75) scattered: {right_column}")
        self.assertNotEqual(left_column, right_column)

    def test_mixed_hinted_and_unhinted_nodes_do_not_collide(self) -> None:
        ir = GenericDiagramIR(nodes=[
            _node("a", x=0.5, y=0.5),
            _node("b", x=0.5, y=0.5),  # same hint as "a" — must be nudged apart
            _node("c"),  # no hint at all, alongside hinted siblings
        ])
        root = ET.fromstring(compile_generic_ir(ir))
        boxes = []
        for nid in ("a", "b", "c"):
            geom = next(c for c in root.iter("mxCell") if c.get("id") == nid).find("mxGeometry")
            boxes.append((
                float(geom.get("x")), float(geom.get("y")),
                float(geom.get("width")), float(geom.get("height")),
            ))
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ax, ay, aw, ah = boxes[i]
                bx, by, bw, bh = boxes[j]
                overlap = ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by
                self.assertFalse(overlap, f"boxes {i} and {j} overlap: {boxes[i]} vs {boxes[j]}")

    def test_icon_hint_stamps_shape_type_attribute(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a", icon_hint="aws s3 bucket")])
        root = ET.fromstring(compile_generic_ir(ir))
        cell = next(c for c in root.iter("mxCell") if c.get("id") == "a")
        self.assertEqual(cell.get("shape_type"), "aws s3 bucket")

    def test_no_icon_hint_omits_shape_type_attribute(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a")])
        root = ET.fromstring(compile_generic_ir(ir))
        cell = next(c for c in root.iter("mxCell") if c.get("id") == "a")
        self.assertIsNone(cell.get("shape_type"))

    def test_size_hint_changes_emitted_dimensions(self) -> None:
        ir = GenericDiagramIR(nodes=[
            _node("small", size_hint="small", x=0.1, y=0.5),
            _node("normal", size_hint="normal", x=0.5, y=0.5),
            _node("large", size_hint="large", x=0.9, y=0.5),
        ])
        root = ET.fromstring(compile_generic_ir(ir))
        widths = {}
        for nid in ("small", "normal", "large"):
            geom = next(c for c in root.iter("mxCell") if c.get("id") == nid).find("mxGeometry")
            widths[nid] = float(geom.get("width"))
        self.assertLess(widths["small"], widths["normal"])
        self.assertLess(widths["normal"], widths["large"])

    def test_fill_color_is_used_verbatim_in_style(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a", fill_color="#3B82F6")])
        root = ET.fromstring(compile_generic_ir(ir))
        cell = next(c for c in root.iter("mxCell") if c.get("id") == "a")
        self.assertIn("fillColor=#3B82F6", cell.get("style"))

    def test_stroke_color_defaults_to_a_darker_shade_of_fill(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a", fill_color="#3B82F6")])
        root = ET.fromstring(compile_generic_ir(ir))
        style = next(c for c in root.iter("mxCell") if c.get("id") == "a").get("style")
        self.assertNotIn("strokeColor=#B3B3B3", style)  # not the neutral placeholder default

    def test_explicit_stroke_color_overrides_derived_shade(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a", fill_color="#3B82F6", stroke_color="#FF0000")])
        root = ET.fromstring(compile_generic_ir(ir))
        style = next(c for c in root.iter("mxCell") if c.get("id") == "a").get("style")
        self.assertIn("strokeColor=#FF0000", style)

    def test_dark_fill_gets_white_font_for_contrast(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a", fill_color="#000000")])
        root = ET.fromstring(compile_generic_ir(ir))
        style = next(c for c in root.iter("mxCell") if c.get("id") == "a").get("style")
        self.assertIn("fontColor=#FFFFFF", style)

    def test_no_color_uses_neutral_placeholder(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a")])
        root = ET.fromstring(compile_generic_ir(ir))
        style = next(c for c in root.iter("mxCell") if c.get("id") == "a").get("style")
        self.assertIn("fillColor=#F5F5F5", style)

    def test_text_shape_uses_fill_color_as_font_color(self) -> None:
        ir = GenericDiagramIR(nodes=[_node("a", "text", fill_color="#FF0000")])
        root = ET.fromstring(compile_generic_ir(ir))
        style = next(c for c in root.iter("mxCell") if c.get("id") == "a").get("style")
        self.assertIn("fontColor=#FF0000", style)
        self.assertNotIn("fillColor", style)

    def test_edge_color_is_used_verbatim(self) -> None:
        ir = GenericDiagramIR(
            nodes=[_node("a"), _node("b")],
            edges=[GenericEdge(source="a", target="b", color="#FF0000")],
        )
        root = ET.fromstring(compile_generic_ir(ir))
        edge_cell = next(c for c in root.iter("mxCell") if c.get("edge") == "1")
        self.assertIn("strokeColor=#FF0000", edge_cell.get("style"))

    def test_grouped_nodes_nest_under_their_group_container(self) -> None:
        ir = GenericDiagramIR(
            groups=[GenericGroup(id="team", label="Team", boundary="solid")],
            nodes=[_node("lead", group="team"), _node("eng", group="team"), _node("outsider")],
        )
        root = ET.fromstring(compile_generic_ir(ir))
        lead = next(c for c in root.iter("mxCell") if c.get("id") == "lead")
        outsider = next(c for c in root.iter("mxCell") if c.get("id") == "outsider")
        self.assertEqual(lead.get("parent"), "team")
        self.assertEqual(outsider.get("parent"), "1")
        group_cell = next(c for c in root.iter("mxCell") if c.get("id") == "team")
        self.assertEqual(group_cell.get("parent"), "1")


if __name__ == "__main__":
    unittest.main()
