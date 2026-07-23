"""Tests for draw.io XML coerce / auto-fix helpers."""

from __future__ import annotations

import json
import os
import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.xml_tools import (
    apply_mcp_styles,
    auto_fix_xml,
    choose_fixed_xml,
    coerce_drawio_xml,
    ensure_diagram_document,
    extract_shape_types,
    fit_containers_to_children,
    guarantee_loadable_xml,
    merge_drawio_styles,
    validate_and_fix_xml,
)


SAMPLE_MXFILE = (
    '<mxfile host="app.diagrams.net">'
    '<diagram id="diagram-1" name="Architecture">'
    '<mxGraphModel dx="2800" dy="1600" grid="1" gridSize="10" guides="1" '
    'tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" '
    'pageWidth="2400" pageHeight="1400" math="0" shadow="0">'
    "<root>"
    '<mxCell id="0"/>'
    '<mxCell id="1" parent="0"/>'
    '<mxCell value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;'
    'strokeColor=none;" vertex="1" parent="1">'
    '<mxGeometry x="0" y="0" width="2400" height="1400" as="geometry"/>'
    "</mxCell>"
    '<mxCell id="10" value="CloudFront" style="sketch=0;outlineConnect=0;'
    'fontColor=#232F3E;fillColor=#8C4FFF;strokeColor=#ffffff;html=1;'
    'shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cloudfront;" '
    'vertex="1" parent="1">'
    '<mxGeometry x="590" y="450" width="78" height="78" as="geometry"/>'
    "</mxCell>"
    "</root></mxGraphModel></diagram></mxfile>"
)


class CoerceDrawioXmlTests(unittest.TestCase):
    def test_json_envelope_with_duplicate_preview(self) -> None:
        blob = json.dumps(
            {
                "args": {"xml": SAMPLE_MXFILE, "routing": "libavoid"},
                "xml_preview": SAMPLE_MXFILE,
            }
        )
        out = coerce_drawio_xml(blob)
        self.assertTrue(out.startswith("<mxfile"))
        self.assertTrue(out.endswith("</mxfile>"))
        self.assertEqual(out.count("<mxfile"), 1)
        tree = ET.fromstring(out)
        cells = list(tree.iter("mxCell"))
        self.assertGreaterEqual(len(cells), 3)

    def test_escaped_string_unescapes_and_truncates(self) -> None:
        escaped = SAMPLE_MXFILE.replace('"', '\\"').replace("\n", "\\n")
        blob = escaped + '\\", "xml_preview": "' + escaped
        out = coerce_drawio_xml(blob)
        self.assertNotIn('\\"', out)
        self.assertTrue(out.startswith("<mxfile"))
        self.assertTrue(out.endswith("</mxfile>"))
        ET.fromstring(out)

    def test_ensure_diagram_document_unwraps_json(self) -> None:
        blob = json.dumps({"diagram": {"xml": SAMPLE_MXFILE, "routing": "libavoid"}})
        out = ensure_diagram_document(blob)
        self.assertIn('host="app.diagrams.net"', out)
        self.assertIn('pageWidth="2400"', out)
        ET.fromstring(out)

    def test_markdown_fence_extracted(self) -> None:
        fenced = f"Here you go:\n```xml\n{SAMPLE_MXFILE}\n```\nthanks"
        out = coerce_drawio_xml(fenced)
        self.assertTrue(out.startswith("<mxfile"))
        self.assertTrue(out.endswith("</mxfile>"))
        ET.fromstring(out)


class AutoFixXmlTests(unittest.TestCase):
    def test_preserves_trailing_diagram_closers(self) -> None:
        fixed, fixes = auto_fix_xml(SAMPLE_MXFILE)
        self.assertNotIn("Stripped trailing LLM wrapper tags", fixes)
        self.assertIn("</root>", fixed)
        self.assertIn("</mxGraphModel>", fixed)
        self.assertIn("</mxfile>", fixed)
        self.assertIn('pageWidth="2400"', fixed)
        self.assertIn('host="app.diagrams.net"', fixed)

    def test_style_space_regex_does_not_corrupt_empty_value(self) -> None:
        snippet = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="" style="rounded=0;whiteSpace=wrap;html=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="10" height="10" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        fixed, _ = auto_fix_xml(snippet)
        self.assertIn('style="rounded=0;', fixed)
        self.assertNotIn('style=" rounded=', fixed)

    def test_missing_id_assigned_without_rewrapping(self) -> None:
        result = validate_and_fix_xml(SAMPLE_MXFILE)
        self.assertTrue(result.valid, result.error)
        out = result.fixed if result.fixed is not None else SAMPLE_MXFILE
        self.assertIn('host="app.diagrams.net"', out)
        self.assertIn('pageWidth="2400"', out)
        self.assertNotIn('style=" rounded=', out)
        self.assertNotIn('style=" sketch=', out)
        tree = ET.fromstring(out)
        for cell in tree.iter("mxCell"):
            self.assertTrue(cell.get("id"), "every mxCell must have an id")
        if result.fixes:
            self.assertTrue(
                any(f.startswith("Assigned missing id") for f in result.fixes),
                result.fixes,
            )

    def test_ampersand_fix_recorded_only_when_changed(self) -> None:
        with_bare = SAMPLE_MXFILE.replace('value="CloudFront"', 'value="A & B"')
        fixed, fixes = auto_fix_xml(with_bare)
        self.assertIn("Escaped unescaped & characters", fixes)
        self.assertIn("A &amp; B", fixed)

        clean, clean_fixes = auto_fix_xml(SAMPLE_MXFILE)
        self.assertNotIn("Escaped unescaped & characters", clean_fixes)
        self.assertIn("<mxfile", clean)

    def test_escapes_stray_markup_inside_value_attribute(self) -> None:
        """Overview-panel-style inline HTML in `value` must be XML-escaped or draw.io rejects the file."""
        snippet = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="overview_panel" '
            'value="<div style="font-size:22px;font-weight:bold;">Title</div>'
            '<div style="font-size:15px;">1. Step one</div>" '
            'style="rounded=1;whiteSpace=wrap;html=1;fillColor=#FFF8E1;strokeColor=#F5A623;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="380" height="160" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        fixed, fixes = auto_fix_xml(snippet)
        self.assertIn("Escaped stray markup inside a value attribute", fixes)
        self.assertIn("&lt;div style=", fixed)
        self.assertIn("Title&lt;/div&gt;", fixed)
        self.assertNotIn('value="<div', fixed)
        # Must now be well-formed XML end to end (the actual reported failure mode).
        tree = ET.fromstring(fixed)
        cell = next(c for c in tree.iter("mxCell") if c.get("id") == "overview_panel")
        self.assertIn("<div", cell.get("value") or "")  # decoded back to real HTML by the parser
        # A normal, already-clean value elsewhere must be untouched.
        clean, clean_fixes = auto_fix_xml(SAMPLE_MXFILE)
        self.assertNotIn("Escaped stray markup inside a value attribute", clean_fixes)
        self.assertIn('value="CloudFront"', clean)

    def test_value_span_boundary_survives_shape_type_attribute(self) -> None:
        """Regression: an enumerated attribute-name lookahead missed shape_type,
        merging it into the escaped value and corrupting the cell (recurrence
        of the "Not a diagram file" bug in a new location)."""
        snippet = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="legend_txt" '
            'value="<div style="font-weight:bold">Legend</div><div><b>Solid:</b> sync</div>" '
            'shape_type="" style="text;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="300" height="80" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="next_cell" value="NextService" shape_type="aws_lambda" '
            'style="html=1;" vertex="1" parent="1">'
            '<mxGeometry x="400" y="0" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        fixed, _ = auto_fix_xml(snippet)
        tree = ET.fromstring(fixed)
        legend = next(c for c in tree.iter("mxCell") if c.get("id") == "legend_txt")
        next_cell = next(c for c in tree.iter("mxCell") if c.get("id") == "next_cell")
        # shape_type must survive as its own attribute, not get swallowed into value.
        self.assertEqual(legend.get("shape_type"), "")
        self.assertEqual(legend.get("value"), '<div style="font-weight:bold">Legend</div><div><b>Solid:</b> sync</div>')
        # The following cell must be completely unaffected.
        self.assertEqual(next_cell.get("shape_type"), "aws_lambda")
        self.assertEqual(next_cell.get("value"), "NextService")

    def test_already_escaped_value_survives_unquoted_attr_fixer(self) -> None:
        """Regression: value="..." content that the LLM already escaped
        correctly (real entities like &quot;/&lt;/&gt;, no literal quote
        chars) was getting corrupted by _UNQUOTED_ATTR, which runs earlier in
        the pipeline and has no concept of "inside a value attribute" — it
        saw `style=&quot;font-size:12px;&quot;&gt;...Solid` (no literal `"`
        immediately after `style=`) as a bare/unquoted attribute and wrapped
        it in real quotes, splicing stray quote characters into the middle of
        legitimate escaped text (surfaced to the user as a libxml2
        "AttValue: \" or ' expected" parse error). Fixed by masking value
        spans before the blunt structural fixers run, then restoring them
        verbatim afterward."""
        snippet = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="legend_box" value="Legend / Line Types" '
            'style="rounded=1;container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="500" height="100" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="legend_txt" '
            'value="&lt;div style=&quot;font-size:12px;&quot;&gt;&lt;b&gt;Solid Line:&lt;/b&gt; '
            "Primary Data Path&lt;br/&gt;&lt;b&gt;Dashed Line:&lt;/b&gt; Control&lt;/div&gt;\" "
            'style="text;html=1;align=left;verticalAlign=top;fontSize=12;fontColor=#333333;" '
            'vertex="1" parent="legend_box">'
            '<mxGeometry x="16" y="32" width="460" height="60" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        fixed, _ = auto_fix_xml(snippet)
        tree = ET.fromstring(fixed)
        legend_txt = next(c for c in tree.iter("mxCell") if c.get("id") == "legend_txt")
        # ET already decodes entities on attribute access — must round-trip
        # to exactly the original HTML, with no stray/misplaced quotes.
        self.assertEqual(
            legend_txt.get("value"),
            '<div style="font-size:12px;"><b>Solid Line:</b> Primary Data Path'
            "<br/><b>Dashed Line:</b> Control</div>",
        )

    def test_canvas_background_stays_behind_shapes(self) -> None:
        """Full-page gray backdrop must not paint over icons after id/reorder."""
        # Put background after CloudFront in source order with a high id — classic hide bug
        xml = (
            '<mxfile host="app.diagrams.net">'
            '<diagram id="diagram-1" name="Architecture">'
            '<mxGraphModel pageWidth="2400" pageHeight="1400">'
            "<root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="10" value="CloudFront" style="sketch=0;shape=mxgraph.aws4.resourceIcon;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="590" y="450" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="99" value="" style="rounded=0;fillColor=#F5F5F5;strokeColor=none;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="2400" height="1400" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        result = validate_and_fix_xml(xml)
        self.assertTrue(result.valid, result.error)
        out = result.fixed if result.fixed is not None else xml
        root = ET.fromstring(out).find(".//root")
        assert root is not None
        ids = [c.get("id") for c in root.findall("mxCell")]
        self.assertEqual(ids[:2], ["0", "1"])
        self.assertLess(ids.index("99"), ids.index("10"), ids)


class McpRoundTripRepairTests(unittest.TestCase):
    """Regression: xml_pipeline._run_mcp() feeds the external MCP libavoid-
    routing response through ensure_diagram_document() alone, which repairs
    missing wrapper tags but has no escaping-repair fallback — a malformed
    value="..." attribute coming back from that untrusted external round-trip
    sailed through untouched and reached the user as a "Not a diagram file /
    Unescaped '<'" error, even though auto_fix_xml() (exercised elsewhere in
    this file) already knows how to repair exactly this. The pipeline fix is
    to route the MCP response through validate_and_fix_xml()+choose_fixed_xml()
    instead — the same pair already used for LLM output earlier in the same
    function. This test guards that pairing, not the pipeline's async
    plumbing (which needs the external MCP client mocked to test directly)."""

    def test_ensure_diagram_document_alone_leaves_it_broken(self) -> None:
        routed_xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="legend_txt" value="<div style="font-weight:bold">Legend</div>" '
            'style="text;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="300" height="80" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        with self.assertRaises(ET.ParseError):
            ET.fromstring(ensure_diagram_document(routed_xml))

    def test_validate_and_fix_xml_repairs_the_mcp_round_trip(self) -> None:
        routed_xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="legend_txt" value="<div style="font-weight:bold">Legend</div>" '
            'style="text;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="300" height="80" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        check = validate_and_fix_xml(routed_xml)
        fixed = choose_fixed_xml(routed_xml, check)
        tree = ET.fromstring(fixed)  # must not raise
        legend = next(c for c in tree.iter("mxCell") if c.get("id") == "legend_txt")
        self.assertEqual(legend.get("value"), '<div style="font-weight:bold">Legend</div>')


class GuaranteeLoadableXmlTests(unittest.TestCase):
    """The final circuit breaker: whatever exits the pipeline must parse, no
    matter which upstream step is at fault — never chase the next leak
    reactively again."""

    def test_already_valid_xml_passes_through_unchanged(self) -> None:
        xml, ok = guarantee_loadable_xml(SAMPLE_MXFILE)
        self.assertTrue(ok)
        tree = ET.fromstring(xml)
        self.assertEqual(len(tree.findall(".//mxCell")), 4)

    def test_repairable_corruption_is_fixed(self) -> None:
        broken = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="legend_txt" value="<div style="font-weight:bold">Legend</div>" '
            'style="text;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="300" height="80" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        xml, ok = guarantee_loadable_xml(broken)
        self.assertTrue(ok)
        ET.fromstring(xml)  # must not raise

    def test_unrepairable_input_degrades_to_empty_canvas_not_broken_xml(self) -> None:
        # Deliberately pathological: no amount of regex/ET repair can recover
        # a real diagram from this, so the guarantee is just "still parses".
        hopeless = "<mxfile><diagram><mxGraphModel><root><mxCell id=\"0"
        xml, ok = guarantee_loadable_xml(hopeless)
        self.assertFalse(ok)
        ET.fromstring(xml)  # must not raise — this is the actual guarantee


class ShapeTypeAndStyleTests(unittest.TestCase):
    def test_extract_shape_types_returns_clean_keys(self) -> None:
        xml = (
            '<mxfile><diagram><mxGraphModel><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="2" shape_type="aws_lambda" style="old;" vertex="1" parent="1">'
            '<mxGeometry as="geometry"/>'
            "</mxCell>"
            '<mxCell id="3" shape_type="aws_s3" style="old;" vertex="1" parent="1">'
            '<mxGeometry as="geometry"/>'
            "</mxCell>"
            '<mxCell id="4" shape_type="aws_lambda" style="old;" vertex="1" parent="1">'
            '<mxGeometry as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        self.assertEqual(extract_shape_types(xml), ["aws_lambda", "aws_s3"])

    def test_extract_shape_types_ignores_split_garbage(self) -> None:
        # Old split("shape_type=") approach would keep trailing tag junk
        xml = '<mxCell shape_type="aws_waf" style="x;" vertex="1"/>'
        self.assertEqual(extract_shape_types(xml), ["aws_waf"])

    def test_apply_mcp_styles_merges_and_escapes(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="Fn" shape_type="aws_lambda" '
            'style="rounded=0;fillColor=#F5F5F5;verticalLabelPosition=bottom;'
            'verticalAlign=top;align=center;fontSize=12;aspect=fixed;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        mcp = (
            "sketch=0;html=1;fillColor=#ED7100;strokeColor=#ffffff;"
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;"
            "verticalLabelPosition=middle;label=A & B;"
        )
        out = apply_mcp_styles(xml, {"aws_lambda": mcp})
        tree = ET.fromstring(out)
        cell = next(c for c in tree.iter("mxCell") if c.get("id") == "2")
        style = cell.get("style") or ""
        self.assertIsNone(cell.get("shape_type"))
        self.assertIn("resIcon=mxgraph.aws4.lambda", style)
        self.assertIn("fillColor=#ED7100", style)
        # LLM presentation keys preserved over MCP
        self.assertIn("verticalLabelPosition=bottom", style)
        self.assertIn("fontSize=12", style)
        self.assertIn("aspect=fixed", style)
        self.assertIn("A &amp; B", out)

    def test_merge_drawio_styles_preserves_labels(self) -> None:
        base = "rounded=0;fillColor=#F5F5F5;verticalLabelPosition=bottom;fontSize=12;aspect=fixed;"
        mcp = "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.s3;fillColor=#7AA116;verticalLabelPosition=middle;"
        merged = merge_drawio_styles(base, mcp)
        self.assertIn("resIcon=mxgraph.aws4.s3", merged)
        self.assertIn("fillColor=#7AA116", merged)
        self.assertIn("verticalLabelPosition=bottom", merged)
        self.assertIn("fontSize=12", merged)

    def test_apply_mcp_styles_miss_leaves_cell_unchanged(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="2" value="Fn" shape_type="aws_lambda" '
            'style="fillColor=#F5F5F5;verticalLabelPosition=bottom;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = apply_mcp_styles(xml, {"aws_s3": "shape=mxgraph.aws4.s3;"})
        tree = ET.fromstring(out)
        cell = next(c for c in tree.iter("mxCell") if c.get("id") == "2")
        self.assertEqual(cell.get("shape_type"), "aws_lambda")
        self.assertIn("fillColor=#F5F5F5", cell.get("style") or "")


class FitContainersToChildrenTests(unittest.TestCase):
    def _geom(self, tree: ET.Element, cell_id: str) -> tuple[float, float, float, float]:
        cell = next(c for c in tree.iter("mxCell") if c.get("id") == cell_id)
        g = cell.find("mxGeometry")
        assert g is not None
        return (
            float(g.get("x") or 0),
            float(g.get("y") or 0),
            float(g.get("width") or 0),
            float(g.get("height") or 0),
        )

    def test_overflow_child_grows_parent(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="vpc" value="VPC" style="rounded=1;container=1;fillColor=none;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="100" height="80" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="svc" value="Lambda" style="rounded=0;fillColor=#F5F5F5;" '
            'vertex="1" parent="vpc">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = fit_containers_to_children(xml)
        tree = ET.fromstring(out)
        _, _, w, h = self._geom(tree, "vpc")
        # child right=102, bottom=126 → + pad 12
        self.assertGreaterEqual(w, 102 + 12)
        self.assertGreaterEqual(h, 126 + 12)
        # leaf geometry unchanged
        self.assertEqual(self._geom(tree, "svc"), (24.0, 48.0, 78.0, 78.0))

    def test_bottom_label_band_included(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="subnet" value="App" style="container=1;fillColor=none;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="100" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="leaf" value="S3" style="verticalLabelPosition=bottom;aspect=fixed;" '
            'vertex="1" parent="subnet">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = fit_containers_to_children(xml)
        tree = ET.fromstring(out)
        _, _, w, h = self._geom(tree, "subnet")
        # bottom = 48+78+28 label band = 154; + pad 12 → 166
        self.assertGreaterEqual(h, 154 + 12)
        self.assertGreaterEqual(w, 24 + 78 + 12)

    def test_nested_containers_bottom_up(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="outer" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="120" height="100" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="inner" value="Subnet" style="container=1;" vertex="1" parent="outer">'
            '<mxGeometry x="24" y="48" width="80" height="60" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="leaf" value="EC2" style="verticalLabelPosition=bottom;" '
            'vertex="1" parent="inner">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = fit_containers_to_children(xml)
        tree = ET.fromstring(out)
        _, _, iw, ih = self._geom(tree, "inner")
        _, _, ow, oh = self._geom(tree, "outer")
        self.assertGreaterEqual(iw, 24 + 78 + 12)
        self.assertGreaterEqual(ih, 48 + 78 + 28 + 12)
        # outer must wrap resized inner + padding
        self.assertGreaterEqual(ow, 24 + iw + 12)
        self.assertGreaterEqual(oh, 48 + ih + 12)

    def test_edges_and_non_containers_unchanged(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="a" value="A" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="b" value="B" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="10" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b" '
            'style="edgeStyle=orthogonalEdgeStyle;">'
            '<mxGeometry relative="1" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = fit_containers_to_children(xml)
        tree = ET.fromstring(out)
        self.assertEqual(self._geom(tree, "a"), (10.0, 10.0, 78.0, 78.0))
        self.assertEqual(self._geom(tree, "b"), (200.0, 10.0, 78.0, 78.0))
        edge = next(c for c in tree.iter("mxCell") if c.get("id") == "e1")
        self.assertEqual(edge.get("edge"), "1")
        self.assertEqual(edge.get("source"), "a")

    def test_supporting_group_pushed_below_zone(self) -> None:
        """After zone grow, Security must clear the zone with a sibling gap."""
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="400" height="200" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="az1" value="Availability Zone A" '
            'style="rounded=1;container=1;strokeColor=#6B7280;dashed=1;" '
            'vertex="1" parent="vpc">'
            '<mxGeometry x="24" y="48" width="100" height="80" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="leaf" value="EC2" style="verticalLabelPosition=bottom;" '
            'vertex="1" parent="az1">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="sec" value="Security" '
            'style="rounded=1;container=1;strokeColor=#DD344C;dashed=1;" '
            'vertex="1" parent="vpc">'
            '<mxGeometry x="24" y="100" width="120" height="80" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = fit_containers_to_children(xml)
        tree = ET.fromstring(out)
        _, az_y, _, az_h = self._geom(tree, "az1")
        sec_x, sec_y, _, _ = self._geom(tree, "sec")
        self.assertGreaterEqual(sec_y, az_y + az_h + 24 - 0.5)
        self.assertEqual(sec_x, 24.0)
        az_cell = next(c for c in tree.iter("mxCell") if c.get("id") == "az1")
        self.assertIn("arcSize=6", az_cell.get("style") or "")

    def test_side_by_side_supporting_groups_get_horizontal_gap(self) -> None:
        xml = (
            '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            "<mxGraphModel><root>"
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="sec" value="Security" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="20" y="200" width="100" height="80" as="geometry"/>'
            "</mxCell>"
            '<mxCell id="mon" value="Monitoring" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="90" y="200" width="100" height="80" as="geometry"/>'
            "</mxCell>"
            "</root></mxGraphModel></diagram></mxfile>"
        )
        out = fit_containers_to_children(xml)
        tree = ET.fromstring(out)
        sx, _, sw, _ = self._geom(tree, "sec")
        mx, _, _, _ = self._geom(tree, "mon")
        self.assertGreaterEqual(mx, sx + sw + 24 - 0.5)


class StyleCacheLruTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.adapters.outbound.diagram import style_cache as sc

        self._sc = sc
        sc.clear_style_cache()
        self._orig_max = sc._STYLE_CACHE_MAX
        sc._STYLE_CACHE_MAX = 2
        # Disable durable DB for unit test isolation.
        self._orig_path = os.environ.get("DRAWIO_STYLE_CACHE_PATH")
        os.environ["DRAWIO_STYLE_CACHE_PATH"] = ""
        # Reset config singleton so path is re-read.
        from app.adapters.outbound.config import env_config
        env_config._config_instance = None
        sc._db_ready = False

    def tearDown(self) -> None:
        self._sc._STYLE_CACHE_MAX = self._orig_max
        self._sc.clear_style_cache()
        if self._orig_path is None:
            os.environ.pop("DRAWIO_STYLE_CACHE_PATH", None)
        else:
            os.environ["DRAWIO_STYLE_CACHE_PATH"] = self._orig_path
        from app.adapters.outbound.config import env_config
        env_config._config_instance = None
        self._sc._db_ready = False

    def test_lru_hit_updates_recency_and_overflow_drops_oldest(self) -> None:
        sc = self._sc
        sc.cache_put(("aws", "a"), "style_a;")
        sc.cache_put(("aws", "b"), "style_b;")
        # Hit "a" so it becomes newest; inserting "c" should drop "b"
        self.assertEqual(sc.cache_get(("aws", "a")), "style_a;")
        sc.cache_put(("aws", "c"), "style_c;")
        self.assertEqual(sc.cache_get(("aws", "a")), "style_a;")
        self.assertEqual(sc.cache_get(("aws", "c")), "style_c;")
        self.assertIsNone(sc.cache_get(("aws", "b")))


if __name__ == "__main__":
    unittest.main()
