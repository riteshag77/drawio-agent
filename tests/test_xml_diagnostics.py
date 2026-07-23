"""Tests for the deterministic draw.io XML diagnostics/repair layer."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.xml_diagnostics import (
    apply_step_markers,
    diagnose_xml,
    ensure_leaf_labels,
    ensure_legend_position,
    ensure_overview_panel_position,
    remove_cross_cutting_edges,
    remove_dangling_edges,
    repair_xml,
    resolve_coordinate_only_nesting,
    resolve_leaf_overlaps,
    resolve_misplaced_actors,
    resolve_pitch_outliers,
    resolve_row_overflow,
)
from app.domain.entities import StepMarker


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


class CrossCuttingGroupEdgeTests(unittest.TestCase):
    """xml_gen_core.txt 'ABSOLUTE RULE — NO LINES': zero edges into Security/
    Monitoring/Integration/Management groups."""

    def _xml_with_edge_into_security_group(self) -> str:
        return _wrap(
            '<mxCell id="app" value="App" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="sec" value="Security" style="container=1;strokeColor=#DD344C;dashed=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="kms" value="KMS" vertex="1" parent="sec">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="app" target="kms">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )

    def test_edge_into_security_group_is_an_error(self) -> None:
        diag = diagnose_xml(self._xml_with_edge_into_security_group())
        self.assertTrue(any("cross-cutting" in e for e in diag.errors), diag.errors)

    def test_edge_to_the_group_container_itself_is_also_an_error(self) -> None:
        xml = _wrap(
            '<mxCell id="app" value="App" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="mon" value="Monitoring" style="container=1;strokeColor=#147EBA;dashed=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="200" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="app" target="mon">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertTrue(any("cross-cutting" in e for e in diag.errors), diag.errors)

    def test_remove_cross_cutting_edges_drops_the_edge(self) -> None:
        out = remove_cross_cutting_edges(self._xml_with_edge_into_security_group())
        tree = ET.fromstring(out)
        self.assertIsNone(next((c for c in tree.iter("mxCell") if c.get("id") == "e1"), None))
        # the group and its child are untouched
        self.assertIsNotNone(next((c for c in tree.iter("mxCell") if c.get("id") == "kms"), None))

    def test_repair_xml_clears_the_cross_cutting_error(self) -> None:
        fixed, diag = repair_xml(self._xml_with_edge_into_security_group())
        self.assertEqual([e for e in diag.errors if "cross-cutting" in e], [])
        tree = ET.fromstring(fixed)
        self.assertIsNone(next((c for c in tree.iter("mxCell") if c.get("id") == "e1"), None))

    def test_edge_between_ordinary_services_is_untouched(self) -> None:
        diag = diagnose_xml(_wrap(
            '<mxCell id="a" value="ALB" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="ECS" vertex="1" parent="1">'
            '<mxGeometry x="200" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a" target="b">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        ))
        self.assertEqual([e for e in diag.errors if "cross-cutting" in e], [])


class CoordinateOnlyNestingTests(unittest.TestCase):
    """xml_gen_core.txt: 'Overlap != containment' — only Cloud may sit at parent="1"."""

    def _xml_with_flat_vpc_inside_cloud(self) -> str:
        return _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="50" y="50" width="300" height="200" as="geometry"/></mxCell>'
        )

    def test_container_visually_inside_another_at_root_is_an_error(self) -> None:
        diag = diagnose_xml(self._xml_with_flat_vpc_inside_cloud())
        self.assertTrue(any("coordinate-only containment" in e for e in diag.errors), diag.errors)

    def test_resolve_reparents_and_converts_to_relative_coords(self) -> None:
        out = resolve_coordinate_only_nesting(self._xml_with_flat_vpc_inside_cloud())
        tree = ET.fromstring(out)
        vpc = next(c for c in tree.iter("mxCell") if c.get("id") == "vpc")
        self.assertEqual(vpc.get("parent"), "cloud")
        geom = vpc.find("mxGeometry")
        assert geom is not None
        self.assertEqual(float(geom.get("x")), 50.0)
        self.assertEqual(float(geom.get("y")), 50.0)

    def test_repair_xml_clears_the_nesting_error(self) -> None:
        fixed, diag = repair_xml(self._xml_with_flat_vpc_inside_cloud())
        self.assertEqual([e for e in diag.errors if "coordinate-only containment" in e], [])
        tree = ET.fromstring(fixed)
        vpc = next(c for c in tree.iter("mxCell") if c.get("id") == "vpc")
        self.assertEqual(vpc.get("parent"), "cloud")

    def test_single_top_level_container_is_not_flagged(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="600" height="400" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertEqual([e for e in diag.errors if "coordinate-only containment" in e], [])

    def test_non_overlapping_sibling_containers_are_not_flagged(self) -> None:
        xml = _wrap(
            '<mxCell id="p1" value="P1" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="p2" value="P2" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="200" height="200" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertEqual([e for e in diag.errors if "coordinate-only containment" in e], [])
        out = resolve_coordinate_only_nesting(xml)
        self.assertEqual(out, xml)


class RowOverflowTests(unittest.TestCase):
    """xml_gen_core.txt 'Service Grid — MAX 4 PER ROW': never squeeze a 5th into row one."""

    def _xml_with_five_in_a_row(self) -> str:
        cells = "".join(
            f'<mxCell id="s{i}" value="Svc{i}" vertex="1" parent="1">'
            f'<mxGeometry x="{i * 200}" y="0" width="78" height="78" as="geometry"/></mxCell>'
            for i in range(5)
        )
        return _wrap(cells)

    def test_five_icons_in_one_row_is_a_warning(self) -> None:
        diag = diagnose_xml(self._xml_with_five_in_a_row())
        self.assertTrue(any("more than 4 icons in one row" in w for w in diag.warnings), diag.warnings)

    def test_four_icons_in_one_row_is_not_flagged(self) -> None:
        cells = "".join(
            f'<mxCell id="s{i}" value="Svc{i}" vertex="1" parent="1">'
            f'<mxGeometry x="{i * 200}" y="0" width="78" height="78" as="geometry"/></mxCell>'
            for i in range(4)
        )
        diag = diagnose_xml(_wrap(cells))
        self.assertEqual([w for w in diag.warnings if "more than 4 icons in one row" in w], [])

    def test_resolve_row_overflow_wraps_fifth_icon_below(self) -> None:
        out = resolve_row_overflow(self._xml_with_five_in_a_row())
        tree = ET.fromstring(out)
        s4 = next(c for c in tree.iter("mxCell") if c.get("id") == "s4")
        geom = s4.find("mxGeometry")
        assert geom is not None
        self.assertGreater(float(geom.get("y")), 0.0)
        self.assertEqual(float(geom.get("x")), 0.0)

    def test_repair_xml_clears_the_row_overflow_warning(self) -> None:
        fixed, diag = repair_xml(self._xml_with_five_in_a_row())
        self.assertEqual([w for w in diag.warnings if "more than 4 icons in one row" in w], [])
        tree = ET.fromstring(fixed)
        s4 = next(c for c in tree.iter("mxCell") if c.get("id") == "s4")
        geom = s4.find("mxGeometry")
        assert geom is not None
        self.assertGreater(float(geom.get("y")), 0.0)


class LabelBandOverlapTests(unittest.TestCase):
    """Sibling overlap detection must include the under-icon label band, not just the icon box."""

    def test_labels_overlap_even_when_icon_boxes_do_not(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" style="verticalLabelPosition=bottom;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" style="verticalLabelPosition=bottom;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="90" width="78" height="78" as="geometry"/></mxCell>'
        )
        # Icon boxes alone (0-78 vs 90-168) don't overlap, but a's 28px label band
        # (extending to y=106) overlaps b's box starting at y=90.
        diag = diagnose_xml(xml)
        self.assertTrue(any("overlap" in w for w in diag.warnings), diag.warnings)

    def test_plain_icons_without_bottom_label_style_unaffected(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="0" y="90" width="78" height="78" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertEqual([w for w in diag.warnings if "overlap" in w], [])


class ApplyStepMarkersTests(unittest.TestCase):
    def test_badge_placed_at_nested_leaf_absolute_position(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="160" y="40" width="600" height="800" as="geometry"/></mxCell>'
            '<mxCell id="lambda_1" value="Lambda" shape_type="aws_lambda" vertex="1" parent="cloud">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = apply_step_markers(xml, [StepMarker(step_number=1, cell_id="lambda_1")])
        tree = ET.fromstring(out)
        badge = next(c for c in tree.iter("mxCell") if c.get("id") == "step_badge_1")
        self.assertEqual(badge.get("parent"), "1")
        self.assertEqual(badge.get("value"), "1")
        geom = badge.find("mxGeometry")
        assert geom is not None
        # absolute = cloud(160,40) + lambda(24,48) = (184,88), minus the badge offset
        self.assertAlmostEqual(float(geom.get("x")), 184.0 - 11.0)
        self.assertAlmostEqual(float(geom.get("y")), 88.0 - 11.0)

    def test_badge_is_last_cell_in_document_order(self) -> None:
        xml = _wrap(
            '<mxCell id="lambda_1" value="Lambda" shape_type="aws_lambda" vertex="1" parent="1">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = apply_step_markers(xml, [StepMarker(step_number=1, cell_id="lambda_1")])
        ids_in_order = [c.get("id") for c in ET.fromstring(out).iter("mxCell")]
        self.assertEqual(ids_in_order[-1], "step_badge_1")

    def test_missing_cell_id_is_skipped_without_error(self) -> None:
        xml = _wrap(
            '<mxCell id="lambda_1" value="Lambda" vertex="1" parent="1">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = apply_step_markers(xml, [StepMarker(step_number=1, cell_id="does_not_exist")])
        self.assertEqual(out, xml)

    def test_marker_without_cell_id_is_skipped(self) -> None:
        xml = _wrap(
            '<mxCell id="lambda_1" value="Lambda" vertex="1" parent="1">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = apply_step_markers(xml, [StepMarker(step_number=1, cell_id=None)])
        self.assertEqual(out, xml)

    def test_empty_markers_list_returns_input_unchanged(self) -> None:
        xml = _wrap(
            '<mxCell id="lambda_1" value="Lambda" vertex="1" parent="1">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        self.assertEqual(apply_step_markers(xml, []), xml)

    def test_duplicate_target_cells_get_diagonally_offset_badges(self) -> None:
        xml = _wrap(
            '<mxCell id="rds_1" value="RDS" vertex="1" parent="1">'
            '<mxGeometry x="100" y="100" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = apply_step_markers(
            xml, [StepMarker(step_number=1, cell_id="rds_1"), StepMarker(step_number=3, cell_id="rds_1")],
        )
        tree = ET.fromstring(out)
        b1 = next(c for c in tree.iter("mxCell") if c.get("id") == "step_badge_1").find("mxGeometry")
        b3 = next(c for c in tree.iter("mxCell") if c.get("id") == "step_badge_3").find("mxGeometry")
        assert b1 is not None and b3 is not None
        self.assertNotEqual((b1.get("x"), b1.get("y")), (b3.get("x"), b3.get("y")))

    def test_badge_id_collision_gets_suffixed(self) -> None:
        xml = _wrap(
            '<mxCell id="step_badge_1" value="preexisting" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="10" height="10" as="geometry"/></mxCell>'
            '<mxCell id="lambda_1" value="Lambda" vertex="1" parent="1">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = apply_step_markers(xml, [StepMarker(step_number=1, cell_id="lambda_1")])
        ids = [c.get("id") for c in ET.fromstring(out).iter("mxCell")]
        self.assertIn("step_badge_1_1", ids)
        self.assertEqual(ids.count("step_badge_1"), 1)  # the pre-existing one, untouched


class EnsureOverviewPanelPositionTests(unittest.TestCase):
    def test_shrinks_oversized_panel_to_actual_text_content(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" '
            'value="&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;Title&lt;/div&gt;'
            '&lt;div style=&quot;font-size:15px;&quot;&gt;1. Short step.&lt;/div&gt;" '
            'style="rounded=1;html=1;fillColor=#FFF8E1;strokeColor=#F5A623;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="400" height="900" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        h = float(panel.find("mxGeometry").get("height"))
        self.assertLess(h, 900.0)  # shrunk, not left at the LLM's inflated value

    def test_grows_undersized_panel_to_fit_many_steps(self) -> None:
        steps = "".join(
            f'&lt;div style=&quot;font-size:15px;&quot;&gt;{i}. A reasonably long step description here.&lt;/div&gt;'
            for i in range(1, 8)
        )
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            f'<mxCell id="overview_panel" value="&lt;div style=&quot;font-size:22px;&quot;&gt;Title&lt;/div&gt;{steps}" '
            'style="rounded=1;html=1;fillColor=#FFF8E1;strokeColor=#F5A623;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="400" height="50" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        h = float(panel.find("mxGeometry").get("height"))
        self.assertGreater(h, 50.0)  # grown to fit 7 real steps, not left tiny

    def test_grows_page_height_when_panel_bottom_exceeds_it(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="1300" width="400" height="160" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        model = ET.fromstring(out).find(".//mxGraphModel")
        self.assertGreater(float(model.get("pageHeight")), 1400.0)


class EnsureLegendPositionTests(unittest.TestCase):
    def test_legend_moved_directly_below_panel(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="900" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="400" height="160" as="geometry"/></mxCell>'
            '<mxCell id="legend_box" value="Legend" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="800" width="380" height="100" as="geometry"/></mxCell>'
        )
        out = ensure_legend_position(xml)
        tree = ET.fromstring(out)
        panel = next(c for c in tree.iter("mxCell") if c.get("id") == "overview_panel")
        legend = next(c for c in tree.iter("mxCell") if c.get("id") == "legend_box")
        pg, lg = panel.find("mxGeometry"), legend.find("mxGeometry")
        self.assertEqual(float(lg.get("x")), float(pg.get("x")))
        self.assertAlmostEqual(
            float(lg.get("y")), float(pg.get("y")) + float(pg.get("height")) + 20.0,
        )

    def test_no_legend_is_a_no_op(self) -> None:
        xml = _wrap(
            '<mxCell id="overview_panel" value="Title" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="400" height="160" as="geometry"/></mxCell>'
        )
        self.assertEqual(ensure_legend_position(xml), xml)

    def test_no_panel_is_a_no_op(self) -> None:
        xml = _wrap(
            '<mxCell id="legend_box" value="Legend" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="800" width="380" height="100" as="geometry"/></mxCell>'
        )
        self.assertEqual(ensure_legend_position(xml), xml)


class ResolvePitchOutliersTests(unittest.TestCase):
    """Generic, shape_type-agnostic fix for a leaf sitting far from its row-mates."""

    def test_extreme_gap_snapped_back_to_normal_pitch(self) -> None:
        xml = _wrap(
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="20" y="50" width="2000" height="300" as="geometry"/></mxCell>'
            '<mxCell id="alb" value="ALB" shape_type="aws_alb" vertex="1" parent="vpc">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="tgw" value="Transit Gateway" shape_type="aws_transit_gateway" '
            'vertex="1" parent="vpc">'
            '<mxGeometry x="1600" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_pitch_outliers(xml)
        tgw = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "tgw")
        self.assertEqual(float(tgw.find("mxGeometry").get("x")), 220.0)  # 20 + canonical 200px pitch

    def test_generic_not_tied_to_any_specific_shape_type(self) -> None:
        """The fixer must trigger for any far-flung leaf, not a hardcoded service list."""
        xml = _wrap(
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="20" y="50" width="2000" height="300" as="geometry"/></mxCell>'
            '<mxCell id="svc_a" value="Some Service" shape_type="aws_some_random_service" '
            'vertex="1" parent="vpc">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="svc_b" value="Another Service" vertex="1" parent="vpc">'
            '<mxGeometry x="1700" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_pitch_outliers(xml)
        svc_b = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "svc_b")
        self.assertEqual(float(svc_b.find("mxGeometry").get("x")), 220.0)

    def test_normal_pitch_is_untouched(self) -> None:
        xml = _wrap(
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="20" y="50" width="600" height="300" as="geometry"/></mxCell>'
            '<mxCell id="a" value="A" vertex="1" parent="vpc">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="vpc">'
            '<mxGeometry x="220" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        self.assertEqual(resolve_pitch_outliers(xml), xml)

    def test_moderate_out_of_range_pitch_is_left_for_the_llm_repair_pass(self) -> None:
        """Only a clear outlier (>3x max) is auto-fixed here; smaller deviations stay diagnose_xml's job."""
        xml = _wrap(
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="20" y="50" width="600" height="300" as="geometry"/></mxCell>'
            '<mxCell id="a" value="A" vertex="1" parent="vpc">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="vpc">'
            '<mxGeometry x="320" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        self.assertEqual(resolve_pitch_outliers(xml), xml)


class StepBadgeExclusionTests(unittest.TestCase):
    """Badges must be invisible to the leaf-grid machinery regardless of pipeline ordering."""

    def test_badge_near_users_actor_triggers_no_overlap_warning(self) -> None:
        xml = _wrap(
            '<mxCell id="users" value="Users" shape_type="aws_users" vertex="1" parent="1">'
            '<mxGeometry x="20" y="20" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="step_badge_1" value="1" style="ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="25" y="25" width="24" height="24" as="geometry"/></mxCell>'
        )
        diag = diagnose_xml(xml)
        self.assertEqual(diag.warnings, [])
        self.assertEqual(diag.errors, [])

    def test_repair_xml_does_not_move_badges(self) -> None:
        xml = _wrap(
            '<mxCell id="users" value="Users" shape_type="aws_users" vertex="1" parent="1">'
            '<mxGeometry x="20" y="20" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="step_badge_1" value="1" style="ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="25" y="25" width="24" height="24" as="geometry"/></mxCell>'
        )
        fixed, _ = repair_xml(xml)
        badge = next(c for c in ET.fromstring(fixed).iter("mxCell") if c.get("id") == "step_badge_1")
        geom = badge.find("mxGeometry")
        # Numeric position unchanged (some fixers reformat "25" -> "25.0" on
        # unrelated cells they touch; that's a string-format detail, not movement).
        self.assertEqual((float(geom.get("x")), float(geom.get("y"))), (25.0, 25.0))


if __name__ == "__main__":
    unittest.main()
