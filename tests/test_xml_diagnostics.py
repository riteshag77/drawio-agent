"""Tests for the deterministic draw.io XML diagnostics/repair layer."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.xml_diagnostics import (
    diagnose_xml,
    ensure_leaf_labels,
    remove_dangling_edges,
    repair_xml,
    resolve_leaf_overlaps,
    resolve_misplaced_actors,
)


def _wrap(cells: str) -> str:
    return (
        '<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
        "<mxGraphModel><root>"
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class DiagnoseXmlTests(unittest.TestCase):
    def test_clean_diagram_has_no_findings(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="Lambda" style="rounded=0;" vertex="1" parent="1">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="S3" style="rounded=0;" vertex="1" parent="1">'
            '<mxGeometry x="224" y="48" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b" style="edgeStyle=orthogonalEdgeStyle;">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])
        self.assertTrue(diag.ok)

    def test_dangling_edge_target_is_an_error(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="Lambda" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="missing">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertTrue(any("e1" in e and "missing" in e for e in diag.errors), diag.errors)

    def test_duplicate_id_is_an_error(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="B" vertex="1" parent="1">'
            '<mxGeometry x="200" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertTrue(any("duplicate id" in e for e in diag.errors), diag.errors)

    def test_overlapping_leaf_siblings_warn(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="20" y="20" width="78" height="78" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertTrue(any("overlap" in w for w in diag.warnings), diag.warnings)

    def test_empty_label_warns(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertTrue(any("empty label" in w for w in diag.warnings), diag.warnings)

    def test_fullpage_background_is_not_flagged_empty_or_overlapping(self) -> None:
        xml = _wrap(
            '<mxCell id="bg" value="" style="fillColor=#F5F5F5;strokeColor=none;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="2400" height="1400" as="geometry"/></mxCell>'
            '<mxCell id="users" value="Users" vertex="1" parent="1">'
            '<mxGeometry x="0" y="200" width="78" height="78" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])

    def test_container_children_do_not_count_as_overlap(self) -> None:
        xml = _wrap(
            '<mxCell id="vpc" value="VPC" style="container=1;fillColor=none;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="leaf" value="EC2" vertex="1" parent="vpc">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertEqual(diag.warnings, [])


class RemoveDanglingEdgesTests(unittest.TestCase):
    def test_drops_edge_with_missing_target(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="ghost">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = remove_dangling_edges(xml)
        tree = ET.fromstring(out)
        self.assertIsNone(next((c for c in tree.iter("mxCell") if c.get("id") == "e1"), None))

    def test_valid_edge_untouched(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="200" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = remove_dangling_edges(xml)
        tree = ET.fromstring(out)
        self.assertIsNotNone(next((c for c in tree.iter("mxCell") if c.get("id") == "e1"), None))


class EnsureLeafLabelsTests(unittest.TestCase):
    def test_empty_value_gets_fallback_from_shape_type(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="" shape_type="aws_lambda" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = ensure_leaf_labels(xml)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        self.assertEqual(cell.get("value"), "Lambda")

    def test_non_empty_value_untouched(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="My Lambda" shape_type="aws_lambda" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = ensure_leaf_labels(xml)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        self.assertEqual(cell.get("value"), "My Lambda")

    def test_fullpage_background_never_gets_a_fallback_label(self) -> None:
        xml = _wrap(
            '<mxCell id="bg" value="" style="fillColor=#F5F5F5;strokeColor=none;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="2400" height="1400" as="geometry"/></mxCell>'
        )
        out = ensure_leaf_labels(xml)
        self.assertEqual(out, xml)

    def test_no_shape_type_falls_back_to_service(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = ensure_leaf_labels(xml)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        self.assertEqual(cell.get("value"), "Service")


class ResolveLeafOverlapsTests(unittest.TestCase):
    def test_overlapping_pair_no_longer_overlaps(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_leaf_overlaps(xml)
        diag = diagnose_xml(out)
        self.assertEqual([w for w in diag.warnings if "overlap" in w], [])

    def test_first_cell_in_reading_order_stays_put(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_leaf_overlaps(xml)
        cell_a = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        geom = cell_a.find("mxGeometry")
        assert geom is not None
        self.assertEqual((geom.get("x"), geom.get("y")), ("0", "0"))

    def test_non_overlapping_siblings_untouched(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_leaf_overlaps(xml)
        self.assertEqual(out, xml)

    def test_fullpage_background_does_not_push_other_leaves_around(self) -> None:
        xml = _wrap(
            '<mxCell id="bg" value="" style="fillColor=#F5F5F5;strokeColor=none;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="2400" height="1400" as="geometry"/></mxCell>'
            '<mxCell id="users" value="Users" vertex="1" parent="1">'
            '<mxGeometry x="0" y="200" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_leaf_overlaps(xml)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "users")
        geom = cell.find("mxGeometry")
        assert geom is not None
        self.assertEqual((geom.get("x"), geom.get("y")), ("0", "200"))

    def test_different_parents_not_treated_as_overlap(self) -> None:
        xml = _wrap(
            '<mxCell id="p1" value="P1" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="p2" value="P2" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="a" value="A" vertex="1" parent="p1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="p2">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_leaf_overlaps(xml)
        self.assertEqual(out, xml)


class ResolveMisplacedActorsTests(unittest.TestCase):
    def test_actor_nested_in_cloud_is_reparented_to_root(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="40" width="600" height="800" as="geometry"/></mxCell>'
            '<mxCell id="users" value="Users" shape_type="aws_users" vertex="1" parent="cloud">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_actors(xml)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "users")
        self.assertEqual(cell.get("parent"), "1")
        geom = cell.find("mxGeometry")
        assert geom is not None
        # left of the cloud box (x=160), clamped to a minimum 20px margin
        self.assertLess(float(geom.get("x")), 160.0)
        self.assertGreaterEqual(float(geom.get("x")), 20.0)

    def test_actor_already_on_root_is_untouched(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="40" width="600" height="800" as="geometry"/></mxCell>'
            '<mxCell id="users" value="Users" shape_type="aws_users" vertex="1" parent="1">'
            '<mxGeometry x="20" y="200" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_actors(xml)
        self.assertEqual(out, xml)

    def test_nested_service_with_user_in_name_is_not_reparented(self) -> None:
        """A Cognito 'User Pool' or similar must not be mistaken for an external actor."""
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="40" width="600" height="800" as="geometry"/></mxCell>'
            '<mxCell id="pool" value="User Pool" shape_type="aws_cognito" vertex="1" parent="cloud">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_actors(xml)
        self.assertEqual(out, xml)

    def test_ordinary_nested_leaf_is_not_reparented(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="40" width="600" height="800" as="geometry"/></mxCell>'
            '<mxCell id="lambda" value="Lambda" shape_type="aws_lambda" vertex="1" parent="cloud">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_actors(xml)
        self.assertEqual(out, xml)


class RepairXmlTests(unittest.TestCase):
    def test_fixes_all_three_defect_types_in_one_pass(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="" shape_type="aws_s3" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="ghost">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        fixed, diag = repair_xml(xml)
        tree = ET.fromstring(fixed)
        self.assertIsNone(next((c for c in tree.iter("mxCell") if c.get("id") == "e1"), None))
        cell_a = next(c for c in tree.iter("mxCell") if c.get("id") == "a")
        self.assertEqual(cell_a.get("value"), "S3")
        self.assertEqual([w for w in diag.warnings if "overlap" in w], [])
        self.assertEqual(diag.errors, [])

    def test_empty_input_untouched(self) -> None:
        fixed, diag = repair_xml("")
        self.assertEqual(fixed, "")
        self.assertTrue(diag.ok)


if __name__ == "__main__":
    unittest.main()
