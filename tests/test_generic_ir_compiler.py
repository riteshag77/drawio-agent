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
