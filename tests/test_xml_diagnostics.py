"""Tests for the deterministic draw.io XML diagnostics/repair layer."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.xml_diagnostics import (
    apply_step_markers,
    diagnose_xml,
    ensure_leaf_labels,
    ensure_legend_position,
    ensure_overview_panel_parent_root,
    ensure_overview_panel_position,
    ensure_valid_vertex_geometry,
    remove_cross_cutting_edges,
    remove_dangling_edges,
    repair_xml,
    resolve_duplicate_ids,
    resolve_orphaned_parents,
    resolve_parent_cycles,
    resolve_reserved_id_reuse,
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


class ExternalActorLabelRecognitionTests(unittest.TestCase):
    """Regression for a real bug found via data/captures replay: 'Employees'
    (a very common actor label for internal-user diagrams) wasn't recognized,
    so it never got containerized (Fix 2c) and sat bare next to the corridor,
    where edges into the corridor's leading icon routinely clipped it."""

    def test_employees_label_is_recognized_as_an_actor(self) -> None:
        from app.adapters.outbound.diagram.xml_diagnostics import _is_external_actor_cell

        cell = ET.fromstring('<mxCell id="e" value="Employees" vertex="1"/>')
        self.assertTrue(_is_external_actor_cell(cell))

    def test_admin_label_is_recognized_as_an_actor(self) -> None:
        from app.adapters.outbound.diagram.xml_diagnostics import _is_external_actor_cell

        cell = ET.fromstring('<mxCell id="a" value="Admin" vertex="1"/>')
        self.assertTrue(_is_external_actor_cell(cell))

    def test_unrelated_service_label_is_not_an_actor(self) -> None:
        from app.adapters.outbound.diagram.xml_diagnostics import _is_external_actor_cell

        cell = ET.fromstring('<mxCell id="s" value="Employee Directory Service" vertex="1"/>')
        self.assertFalse(_is_external_actor_cell(cell))


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
        # Overlaps are diagnosed only — geometry mutators were removed from repair_xml.
        self.assertEqual(diag.errors, [])

    def test_empty_input_untouched(self) -> None:
        fixed, diag = repair_xml("")
        self.assertEqual(fixed, "")
        self.assertTrue(diag.ok)


    def test_repairs_parent_cycle_bad_geometry_and_incomplete_edges(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="b">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="a">'
            '<mxGeometry x="100" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="bad_geo" value="Bad" vertex="1" parent="1">'
            '<mxGeometry x="abc" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="a">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" parent="1" source="ghost" target="a">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        fixed, diag = repair_xml(xml)
        self.assertEqual(diag.errors, [])
        tree = ET.fromstring(fixed)
        by_id = {c.get("id"): c for c in tree.iter("mxCell")}
        self.assertEqual(by_id["a"].get("parent"), "1")
        self.assertEqual(by_id["b"].get("parent"), "1")
        geo = by_id["bad_geo"].find("mxGeometry")
        self.assertIsNotNone(geo)
        float(geo.get("x") or 0)  # must be numeric after repair
        float(geo.get("y") or 0)
        self.assertIsNone(by_id.get("e1"))
        self.assertIsNone(by_id.get("e2"))

    def test_unparseable_xml_is_an_error_not_silent_ok(self) -> None:
        diag = diagnose_xml("<not><valid")
        self.assertFalse(diag.ok)
        self.assertTrue(any("unparseable" in e for e in diag.errors))

    def test_resolve_parent_cycles_breaks_self_parent(self) -> None:
        xml = _wrap(
            '<mxCell id="loop" value="Loop" vertex="1" parent="loop">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_parent_cycles(xml)
        cell = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "loop")
        self.assertEqual(cell.get("parent"), "1")


class CrossCuttingGroupEdgeTests(unittest.TestCase):
    """network.txt 'ABSOLUTE RULE — NO LINES': zero edges into Security/
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
    """network.txt: 'Overlap != containment' — only Cloud may sit at parent="1"."""

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


class RowOverflowTests(unittest.TestCase):
    """Service grid max 4 per row: never squeeze a 5th into row one."""

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
    def test_key_facts_section_survives_step_count_cap(self) -> None:
        """Part B (ADS uplift): a data-section="facts" block (the per-view Key
        facts block) is excluded from the 6-step cap and kept verbatim, the
        same way the existing Recommended-gaps section already is."""
        steps = "".join(
            f'&lt;div style=&quot;font-size:15px;&quot;&gt;{i}. Step {i} text here.&lt;/div&gt;'
            for i in range(1, 7)
        )
        facts = (
            '&lt;div data-section=&quot;facts&quot; style=&quot;font-size:13px;&quot;&gt;'
            'Key facts: Encryption at rest via KMS.&lt;/div&gt;'
        )
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" '
            f'value="&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;Title&lt;/div&gt;{steps}{facts}" '
            'style="rounded=1;html=1;fillColor=#FFF8E1;strokeColor=#F5A623;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="400" height="50" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        value = panel.get("value") or ""
        self.assertIn("Key facts", value)
        self.assertIn("Step 6 text here", value, "all 6 real steps must survive, not be crowded out by the facts block")

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

    def test_overview_trim_does_not_double_escape_html(self) -> None:
        """Element.set must receive decoded HTML — pre-escaped values show raw tags in UI."""
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" '
            'value="&lt;div style=&quot;font-size:22px;font-weight:bold;&quot;&gt;Title&lt;/div&gt;'
            '&lt;div style=&quot;font-size:15px;&quot;&gt;1. Short step.&lt;/div&gt;" '
            'style="rounded=1;html=1;fillColor=#FFF8E1;strokeColor=#F5A623;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="400" height="200" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        self.assertNotIn("&amp;lt;", out)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        decoded = panel.get("value") or ""
        self.assertIn("<div", decoded)
        self.assertIn("Title", decoded)
        # Serialized attribute still entity-escapes once for well-formed XML.
        self.assertIn("&lt;div", out)

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

    def test_clamps_oversized_panel_width(self) -> None:
        """Gap B (Fix 3): width was never corrected before — only height was."""
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="1400" height="160" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        w = float(panel.find("mxGeometry").get("width"))
        self.assertLessEqual(w, 640.0)

    def test_clamps_undersized_panel_width(self) -> None:
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="120" height="160" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        w = float(panel.find("mxGeometry").get("width"))
        self.assertGreaterEqual(w, 400.0)

    def test_in_range_panel_width_is_a_noop(self) -> None:
        """Idempotency matters — repair_xml may re-run this fixer."""
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="840" y="100" width="500" height="160" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_position(xml)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        w = float(panel.find("mxGeometry").get("width"))
        self.assertEqual(w, 500.0)

    def test_reposition_after_container_growth_clears_new_bounds(self) -> None:
        """Regression: a surgical edit that adds a node into an existing container
        grows that container via fit_containers_to_children AFTER repair_xml's one
        ensure_overview_panel_position pass already ran — leaving the panel pinned
        against the container's smaller, pre-growth width. xml_pipeline.py now
        re-runs this fixer after that later growth; this asserts the fixer itself
        does the right thing when called against the grown geometry, which is what
        makes that second call effective."""
        xml = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="100" width="600" height="400" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;&lt;div&gt;1. Step.&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="820" y="100" width="400" height="160" as="geometry"/></mxCell>'
        )
        # First pass (mirrors repair_xml's call, before the container has grown)
        # — panel already clear of the container at its original width, so it's
        # left alone.
        once = ensure_overview_panel_position(xml)
        panel = next(c for c in ET.fromstring(once).iter("mxCell") if c.get("id") == "overview_panel")
        self.assertEqual(float(panel.find("mxGeometry").get("x")), 820.0)

        # Simulate fit_containers_to_children widening the container to enclose a
        # node a surgical edit just added — now it overlaps the still-820 panel.
        tree = ET.fromstring(once)
        cloud = next(c for c in tree.iter("mxCell") if c.get("id") == "cloud")
        cloud.find("mxGeometry").set("width", "900")
        grown = ET.tostring(tree, encoding="unicode")
        twice = ensure_overview_panel_position(grown)
        panel = next(c for c in ET.fromstring(twice).iter("mxCell") if c.get("id") == "overview_panel")
        px, pw = float(panel.find("mxGeometry").get("x")), float(panel.find("mxGeometry").get("width"))
        self.assertGreaterEqual(px, 200.0 + 900.0, "panel must clear the grown container's right edge")
        self.assertGreater(pw, 0.0)


class EnsureOverviewPanelParentRootTests(unittest.TestCase):
    """Regression for a defect found via live-generation review (Part 6): a Security

    page had ``overview_panel`` emitted with ``parent="zone_dmz"`` — the LLM applied
    the view's "every leaf's parent must be its owning zone" rule too literally to
    this page-level UI cell. Since every downstream consumer (position/size fixers,
    container shrink-fit) treats the panel's geometry as already absolute, the wrong
    parent silently balloons that zone's width to engulf the panel.
    """

    def test_overview_panel_reparented_to_root_with_absolute_geometry(self) -> None:
        xml = _wrap(
            '<mxCell id="zone_dmz" value="Perimeter Zone" style="container=1;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="160" y="140" width="200" height="200" as="geometry"/></mxCell>'
            '<mxCell id="waf" value="WAF" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="28" y="56" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;" '
            'style="rounded=1;html=1;fillColor=#FFF8E1;" vertex="1" parent="zone_dmz">'
            '<mxGeometry x="900" y="0" width="400" height="200" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_parent_root(xml)
        panel = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "overview_panel")
        self.assertEqual(panel.get("parent"), "1")
        geo = panel.find("mxGeometry")
        # zone_dmz sits at (160, 140) — panel's absolute position is its
        # zone-relative (900, 0) plus that offset, not the raw relative value.
        self.assertAlmostEqual(float(geo.get("x")), 1060.0)
        self.assertAlmostEqual(float(geo.get("y")), 140.0)

    def test_noop_when_already_parent_root(self) -> None:
        xml = _wrap(
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="100" width="400" height="200" as="geometry"/></mxCell>'
        )
        out = ensure_overview_panel_parent_root(xml)
        self.assertEqual(out, xml)


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


class ResolveReservedIdReuseTests(unittest.TestCase):
    def test_vertex_reusing_id_1_gets_renamed(self) -> None:
        xml = _wrap(
            '<mxCell id="1" value="Rogue" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_reserved_id_reuse(xml)
        tree = ET.fromstring(out)
        rogue = next(c for c in tree.iter("mxCell") if c.get("value") == "Rogue")
        self.assertNotEqual(rogue.get("id"), "1")
        # the canonical root/layer cells from _wrap() must be untouched
        self.assertIsNotNone(tree.find(".//mxCell[@id='0']"))
        canonical_layer = next(c for c in tree.iter("mxCell") if c.get("id") == "1")
        self.assertEqual(canonical_layer.get("parent"), "0")
        self.assertIsNone(canonical_layer.get("vertex"))

    def test_canonical_root_and_layer_are_untouched_on_clean_doc(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        self.assertEqual(resolve_reserved_id_reuse(xml), xml)

    def test_repair_xml_clears_the_reserved_id_error(self) -> None:
        xml = _wrap(
            '<mxCell id="1" value="Rogue" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        _, diag = repair_xml(xml)
        self.assertEqual([e for e in diag.errors if "reserved id" in e], [])


class ResolveDuplicateIdsTests(unittest.TestCase):
    def test_duplicate_ids_get_unique_ids(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_duplicate_ids(xml)
        tree = ET.fromstring(out)
        ids = [c.get("id") for c in tree.iter("mxCell")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_last_occurrence_in_document_order_keeps_the_original_id(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_duplicate_ids(xml)
        tree = ET.fromstring(out)
        first = next(c for c in tree.iter("mxCell") if c.get("value") == "First")
        second = next(c for c in tree.iter("mxCell") if c.get("value") == "Second")
        self.assertEqual(second.get("id"), "dup")
        self.assertNotEqual(first.get("id"), "dup")

    def test_existing_references_to_the_kept_id_still_resolve(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="other" value="Other" vertex="1" parent="1">'
            '<mxGeometry x="600" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="other" target="dup">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = resolve_duplicate_ids(xml)
        diag = diagnose_xml(out)
        self.assertEqual([e for e in diag.errors if "does not exist" in e], [])

    def test_three_way_duplicate_all_but_last_renamed(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Third" vertex="1" parent="1">'
            '<mxGeometry x="600" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_duplicate_ids(xml)
        tree = ET.fromstring(out)
        ids = [c.get("id") for c in tree.iter("mxCell")]
        self.assertEqual(len(ids), len(set(ids)))
        third = next(c for c in tree.iter("mxCell") if c.get("value") == "Third")
        self.assertEqual(third.get("id"), "dup")

    def test_repair_xml_clears_the_duplicate_id_error(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        _, diag = repair_xml(xml)
        self.assertEqual([e for e in diag.errors if "duplicate id" in e], [])

    def test_default_call_without_warnings_list_is_unaffected(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_duplicate_ids(xml)  # positional-only call, no crash
        tree = ET.fromstring(out)
        ids = [c.get("id") for c in tree.iter("mxCell")]
        self.assertEqual(len(ids), len(set(ids)))

    def test_rebind_warning_fires_when_an_edge_references_a_deduped_id(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="other" value="Other" vertex="1" parent="1">'
            '<mxGeometry x="600" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="other" target="dup">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        warnings: list[str] = []
        resolve_duplicate_ids(xml, warnings)
        self.assertTrue(any("dup" in w for w in warnings))

    def test_no_warning_when_no_edge_references_the_duplicated_id(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        warnings: list[str] = []
        resolve_duplicate_ids(xml, warnings)
        self.assertEqual(warnings, [])

    def test_repair_xml_surfaces_rebind_warning(self) -> None:
        xml = _wrap(
            '<mxCell id="dup" value="First" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="dup" value="Second" vertex="1" parent="1">'
            '<mxGeometry x="300" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="other" value="Other" vertex="1" parent="1">'
            '<mxGeometry x="600" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="other" target="dup">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        _, diag = repair_xml(xml)
        self.assertTrue(any("dup" in w for w in diag.warnings))


class ResolveOrphanedParentsTests(unittest.TestCase):
    def test_cell_with_missing_parent_is_reparented_to_root(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="ghost_parent">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_orphaned_parents(xml)
        a = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        self.assertEqual(a.get("parent"), "1")

    def test_cell_with_valid_parent_untouched(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        self.assertEqual(resolve_orphaned_parents(xml), xml)

    def test_repair_xml_clears_the_missing_parent_error(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="ghost_parent">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        _, diag = repair_xml(xml)
        self.assertEqual([e for e in diag.errors if "does not exist" in e and "parent" in e], [])


class EnsureValidVertexGeometryTests(unittest.TestCase):
    def test_missing_geometry_gets_default_leaf_size(self) -> None:
        xml = _wrap('<mxCell id="a" value="A" vertex="1" parent="1"/>')
        out = ensure_valid_vertex_geometry(xml)
        a = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        geom = a.find("mxGeometry")
        self.assertIsNotNone(geom)
        self.assertEqual(float(geom.get("width")), 78.0)
        self.assertEqual(float(geom.get("height")), 78.0)

    def test_missing_geometry_on_container_gets_container_default_size(self) -> None:
        xml = _wrap('<mxCell id="c" value="Container" style="container=1;" vertex="1" parent="1"/>')
        out = ensure_valid_vertex_geometry(xml)
        c = next(cell for cell in ET.fromstring(out).iter("mxCell") if cell.get("id") == "c")
        geom = c.find("mxGeometry")
        self.assertIsNotNone(geom)
        self.assertEqual(float(geom.get("width")), 160.0)
        self.assertEqual(float(geom.get("height")), 120.0)

    def test_non_positive_size_clamped_to_minimum(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="0" height="-5" as="geometry"/></mxCell>'
        )
        out = ensure_valid_vertex_geometry(xml)
        a = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        geom = a.find("mxGeometry")
        self.assertEqual(float(geom.get("width")), 78.0)
        self.assertEqual(float(geom.get("height")), 78.0)

    def test_negative_position_clamped_to_zero(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="-40" y="-10" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = ensure_valid_vertex_geometry(xml)
        a = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "a")
        geom = a.find("mxGeometry")
        self.assertEqual(float(geom.get("x")), 0.0)
        self.assertEqual(float(geom.get("y")), 0.0)

    def test_valid_geometry_untouched(self) -> None:
        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="10" y="10" width="78" height="78" as="geometry"/></mxCell>'
        )
        self.assertEqual(ensure_valid_vertex_geometry(xml), xml)


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


class MoveOverviewAndLegendToEndTests(unittest.TestCase):
    """7c: overview panel / legend must always render on top (last in document order)."""

    def test_panel_emitted_first_moves_to_last(self) -> None:
        from app.adapters.outbound.diagram.xml_diagnostics import (
            move_overview_and_legend_to_end,
        )

        xml = _wrap(
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="100" width="400" height="200" as="geometry"/></mxCell>'
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="b" value="B" vertex="1" parent="1">'
            '<mxGeometry x="100" y="0" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = move_overview_and_legend_to_end(xml)
        ids = [c.get("id") for c in ET.fromstring(out).iter("mxCell")]
        self.assertEqual(ids[-1], "overview_panel")

    def test_noop_when_already_last(self) -> None:
        from app.adapters.outbound.diagram.xml_diagnostics import (
            move_overview_and_legend_to_end,
        )

        xml = _wrap(
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="100" width="400" height="200" as="geometry"/></mxCell>'
        )
        self.assertEqual(move_overview_and_legend_to_end(xml), xml)

    def test_legend_and_panel_both_moved_last_preserving_order(self) -> None:
        from app.adapters.outbound.diagram.xml_diagnostics import (
            move_overview_and_legend_to_end,
        )

        xml = _wrap(
            '<mxCell id="legend_box" value="Legend" style="rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="500" width="200" height="80" as="geometry"/></mxCell>'
            '<mxCell id="a" value="A" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="overview_panel" value="&lt;div&gt;Title&lt;/div&gt;" '
            'style="rounded=1;html=1;" vertex="1" parent="1">'
            '<mxGeometry x="900" y="100" width="400" height="200" as="geometry"/></mxCell>'
        )
        out = move_overview_and_legend_to_end(xml)
        ids = [c.get("id") for c in ET.fromstring(out).iter("mxCell")]
        self.assertEqual(set(ids[-2:]), {"overview_panel", "legend_box"})
        self.assertEqual(ids[-1], "overview_panel")


if __name__ == "__main__":
    unittest.main()
