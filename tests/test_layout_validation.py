"""Unit tests for validate_layout (Fix 2e) — informational, best-effort
container/edge sanity checks that run after repair_xml."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.xml_diagnostics import validate_layout


def _page(name: str, cells: str) -> str:
    return (
        f'<mxfile><diagram name="{name}" id="p1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class ContainerBoundsTests(unittest.TestCase):
    def test_child_within_parent_is_clean(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="phase_ingest" value="Ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="apigw" value="API GW" vertex="1" parent="phase_ingest">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertEqual([i for i in issues if "exceeds parent" in i], [])

    def test_child_exceeding_parent_is_flagged(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="phase_ingest" value="Ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="100" height="100" as="geometry"/></mxCell>'
            '<mxCell id="apigw" value="API GW" vertex="1" parent="phase_ingest">'
            '<mxGeometry x="80" y="80" width="78" height="78" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertTrue(any("exceeds parent" in i for i in issues))


class SiblingOverlapTests(unittest.TestCase):
    def test_non_overlapping_siblings_are_clean(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="phase_ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="phase_store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="200" height="150" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertEqual([i for i in issues if "overlap" in i and "containers" in i], [])

    def test_overlapping_siblings_are_flagged(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="phase_ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="phase_store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="100" y="0" width="200" height="150" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertTrue(any("sibling containers" in i for i in issues))


class TierHeightTests(unittest.TestCase):
    def test_uniform_row_heights_are_clean(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="phase_ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="phase_store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="200" height="150" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertEqual([i for i in issues if "tier height" in i], [])

    def test_wildly_uneven_row_heights_are_flagged(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="phase_ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="phase_store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="200" height="800" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertTrue(any("row tier height" in i for i in issues))

    def test_shelf_and_row_are_separate_tiers(self) -> None:
        """A shelf anchored below the row is expected to differ in height from
        the row — it must not be lumped into the same tier check as the row."""
        page = _page(
            "Data flow",
            '<mxCell id="phase_store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="phase_consume" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="200" width="200" height="500" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertEqual([i for i in issues if "tier height" in i], [])


class EdgeValidationTests(unittest.TestCase):
    def test_clean_direct_edge_has_no_issues(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="a" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b" '
            'style="exitX=1;exitY=0.5;entryX=0;entryY=0.5;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertEqual(issues, [])

    def test_mismatched_anchor_is_flagged(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="a" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b" '
            'style="exitX=0;exitY=0.5;entryX=1;entryY=0.5;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertTrue(any("anchor disagrees" in i for i in issues))

    def test_edge_through_non_endpoint_node_is_flagged(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="a" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="mid" vertex="1" parent="1">'
            '<mxGeometry x="150" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b" '
            'style="exitX=1;exitY=0.5;entryX=0;entryY=0.5;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>',
        )
        issues = validate_layout(page)
        self.assertTrue(any("passes through non-endpoint node" in i for i in issues))

    def test_too_many_waypoint_bends_is_flagged(self) -> None:
        page = _page(
            "Data flow",
            '<mxCell id="a" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" vertex="1" parent="1">'
            '<mxGeometry x="600" y="600" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b">'
            '<mxGeometry relative="1" as="geometry">'
            '<Array as="points">'
            '<mxPoint x="100" y="50"/><mxPoint x="150" y="200"/>'
            '<mxPoint x="300" y="250"/><mxPoint x="400" y="500"/>'
            "</Array></mxGeometry></mxCell>",
        )
        issues = validate_layout(page)
        self.assertTrue(any("waypoint bends" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
