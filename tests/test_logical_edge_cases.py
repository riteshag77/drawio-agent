"""Logical view battle-test coverage — edge cases found by audit that had
zero test coverage before this file. Each test class documents which bug (or
untested-but-real interaction) it targets; several are direct regression
tests for fixes made in the same audit (criticality loss, corridor-order
dependency).
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram import palette
from app.adapters.outbound.diagram.architecture_docs import (
    _LOGICAL_ROLE_TO_SHAPE_TYPE,
    assign_logical_shape_types,
    assign_saas_shape_types,
)
from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.diagram_edits import apply_diagram_edits
from app.adapters.outbound.diagram.drawio_mcp_client import _LOGICAL_BUILTIN_STYLES
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.entities.diagram_edit import DiagramEditOp, DiagramEditPlan
from app.domain.ir import AnchoredCorridor, Corridor, ExternalActor, Flow, Leaf, LogicalIR


def _leaf(i: str, st: str, label: str, **kwargs) -> Leaf:
    return Leaf(id=i, shape_type=st, label=label, **kwargs)


def _cells(xml: str) -> dict[str, ET.Element]:
    return {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}


class CriticalityPreservationTests(unittest.TestCase):
    """Direct regression test for the headline bug: assign_logical_shape_types/
    assign_saas_shape_types unconditionally overwrote a leaf's style, silently
    dropping criticality="critical"'s red border on any leaf not already
    aws_*/gcp_*/azure_*-prefixed (i.e. most of Logical's own generic
    vocabulary)."""

    def test_criticality_survives_assign_logical_shape_types(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[
                _leaf("payment", "logical_capability", "Payment Service", criticality="critical"),
                _leaf("helper", "logical_capability", "Helper Service"),
            ])],
            flows=[Flow(source="payment", target="helper")],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        xml = assign_logical_shape_types(xml)
        xml = assign_saas_shape_types(xml)
        cells = _cells(xml)
        self.assertIn(palette.CRITICAL_LEAF_STROKE, cells["payment"].get("style") or "")
        self.assertNotIn(palette.CRITICAL_LEAF_STROKE, cells["helper"].get("style") or "")

    def test_criticality_survives_assign_saas_shape_types_vendor_match(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[
                _leaf("stripe", "logical_saas", "Stripe", criticality="critical"),
                _leaf("api", "logical_capability", "API"),
            ])],
            flows=[Flow(source="api", target="stripe")],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        xml = assign_logical_shape_types(xml)
        xml = assign_saas_shape_types(xml)
        cells = _cells(xml)
        # Confirms the vendor match actually fired (shape_type changed away
        # from the generic logical_saas placeholder) before checking the
        # criticality border survived that rewrite too.
        self.assertTrue((cells["stripe"].get("shape_type") or "").startswith("saas_"))
        self.assertIn(palette.CRITICAL_LEAF_STROKE, cells["stripe"].get("style") or "")


class CorridorDeclarationOrderTests(unittest.TestCase):
    """Direct regression test: _emit_logical_flat built its left-to-right
    order directly from JSON declaration order (nothing re-sorts by canonical
    rank), so a model emitting corridors out of order silently inverted the
    journey read direction."""

    def _build(self, order: list[str]) -> LogicalIR:
        by_id = {
            "domain_experience": Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "logical_portal", "Portal")]),
            "domain_core": Corridor(id="domain_core", label="Core", leaves=[_leaf("api", "logical_capability", "API")]),
            "domain_data": Corridor(id="domain_data", label="Data", leaves=[_leaf("db", "logical_datastore", "DB")]),
        }
        return LogicalIR(
            corridors=[by_id[i] for i in order],
            flows=[Flow(source="portal", target="api"), Flow(source="api", target="db")],
        )

    def test_leaf_x_position_independent_of_declaration_order(self) -> None:
        canonical = compile_corridor_ir(
            self._build(["domain_experience", "domain_core", "domain_data"]), page_name="Logical",
        )
        reversed_decl = compile_corridor_ir(
            self._build(["domain_data", "domain_core", "domain_experience"]), page_name="Logical",
        )
        c1, c2 = _cells(canonical), _cells(reversed_decl)
        for cid in ("portal", "api", "db"):
            x1 = float(c1[cid].find("mxGeometry").get("x"))
            x2 = float(c2[cid].find("mxGeometry").get("x"))
            self.assertEqual(x1, x2, f"{cid} x-position depends on corridor declaration order")
        # And the canonical L->R order itself is correct (portal < api < db).
        xs = [float(c1[cid].find("mxGeometry").get("x")) for cid in ("portal", "api", "db")]
        self.assertEqual(xs, sorted(xs))


class InterleavedCloudLeafOrderTests(unittest.TestCase):
    """_emit_logical_flat's before/cloud_leaves/after split is a single
    linear pass with a one-way seen_cloud flag: a non-cloud leaf ranked after
    any cloud leaf lands in `after` (right of the cloud group), not `before`
    (left of it). Documents this as intended, tested behavior rather than an
    unexercised implicit contract."""

    def test_noncloud_leaf_ranked_after_cloud_leaf_lands_right_of_cloud_group(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "logical_portal", "Portal")]),
                Corridor(id="domain_core", label="Core", leaves=[_leaf("api", "aws_lambda", "API")]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("archive", "logical_datastore", "Archive")]),
            ],
            flows=[Flow(source="portal", target="api"), Flow(source="api", target="archive")],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        cells = _cells(xml)
        self.assertIn("cloud_boundary", cells)
        cb_geo = cells["cloud_boundary"].find("mxGeometry")
        cb_right = float(cb_geo.get("x")) + float(cb_geo.get("width"))
        # archive (non-cloud, ranked after the cloud leaf "api") must be
        # parented to root and positioned to the right of the cloud group,
        # not folded into `before` on the left.
        self.assertEqual(cells["archive"].get("parent"), "1")
        archive_x = float(cells["archive"].find("mxGeometry").get("x"))
        self.assertGreaterEqual(archive_x, cb_right)


class MultiActorTests(unittest.TestCase):
    """No existing fixture has more than 2 external_actors or actors on both
    sides together. Extends to 3 left actors with distinct flow-endpoint Y
    preferences plus 1 right actor."""

    def test_three_left_actors_do_not_collide_and_right_actor_splits_correctly(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[
                    _leaf("portal", "logical_portal", "Portal"),
                ]),
                Corridor(id="domain_core", label="Core", leaves=[
                    _leaf("svc_a", "logical_capability", "Service A"),
                    _leaf("svc_b", "logical_capability", "Service B"),
                    _leaf("svc_c", "logical_capability", "Service C"),
                ]),
            ],
            external_actors=[
                ExternalActor(id="users", kind="users", label="Users"),
                ExternalActor(id="okta", kind="saas_ingress", label="Okta"),
                ExternalActor(id="partner", kind="saas_ingress", label="Partner API"),
                ExternalActor(id="stripe", kind="saas_egress", label="Stripe"),
            ],
            flows=[
                Flow(source="users", target="portal"),
                Flow(source="okta", target="svc_a"),
                Flow(source="partner", target="svc_b"),
                Flow(source="portal", target="svc_c"),
                Flow(source="svc_c", target="stripe"),
            ],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        cells = _cells(xml)

        left_ids = ["users", "okta", "partner"]
        boxes = []
        for cid in left_ids:
            g = cells[cid].find("mxGeometry")
            x, y = float(g.get("x")), float(g.get("y"))
            h = float(g.get("height"))
            boxes.append((cid, x, y, h))
        # No two left actors may vertically overlap.
        boxes_by_y = sorted(boxes, key=lambda b: b[2])
        for (_id1, _x1, y1, h1), (_id2, x2, y2, h2) in zip(boxes_by_y, boxes_by_y[1:]):
            self.assertGreaterEqual(y2, y1 + h1, "left actors overlap vertically")

        # Right-kind actor (Stripe) must not sit in the left actor column.
        left_x = min(b[1] for b in boxes)
        stripe_x = float(cells["stripe"].find("mxGeometry").get("x"))
        self.assertGreater(stripe_x, left_x)


class NearCapDensityTests(unittest.TestCase):
    """The only real 13-leaf capture in the corpus is the known-broken
    zero-flows regression fixture — no real example exercises the near-cap
    layout path with a valid diagram. Constructs one at the 14-leaf hard cap
    (<=5/domain) and compiles it through the full pipeline."""

    def test_fourteen_leaf_diagram_compiles_cleanly_with_wrap(self) -> None:
        def leaves(prefix: str, n: int, shape: str) -> list[Leaf]:
            return [_leaf(f"{prefix}{i}", shape, f"{prefix.title()} {i}") for i in range(n)]

        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=leaves("exp", 1, "logical_portal")),
                Corridor(id="domain_core", label="Core", leaves=leaves("core", 5, "logical_capability")),
                Corridor(id="domain_data", label="Data", leaves=leaves("data", 5, "logical_datastore")),
                Corridor(id="domain_platform", label="Platform", leaves=leaves("plat", 3, "logical_capability")),
            ],
            flows=[
                Flow(source="exp0", target="core0"),
                *[Flow(source=f"core{i}", target=f"core{i+1}") for i in range(4)],
                Flow(source="core4", target="data0"),
                *[Flow(source=f"data{i}", target=f"data{i+1}") for i in range(4)],
                Flow(source="core0", target="plat0"),
                *[Flow(source=f"plat{i}", target=f"plat{i+1}") for i in range(2)],
            ],
        )
        total = sum(len(c.leaves) for c in ir.corridors)
        self.assertEqual(total, 14)
        self.assertEqual(ir.semantic_errors(), [])

        xml = compile_corridor_ir(ir, page_name="Logical")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        cells = _cells(xml)
        # core has 5 leaves -> must wrap to 2 rows (<=4 cols) inside the
        # cloud-shaped grouping only if cloud-shaped; these are all
        # logical_* (non-cloud), so they render loose at parent="1" — the
        # wrap still applies via _place_row's cols=min(4, len(leaves)).
        core_ys = {cells[f"core{i}"].find("mxGeometry").get("y") for i in range(5)}
        self.assertGreater(len(core_ys), 1, "5-leaf block never wrapped to a second row")


class DuplicateLeafLabelTests(unittest.TestCase):
    """Two leaves, different ids, identical label — confirms leaf_label()
    and _logical_leaf_role() classification don't conflate the two cells."""

    def test_duplicate_labels_remain_distinct_cells(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_core", label="Core", leaves=[
                _leaf("svc1", "logical_capability", "Order Service"),
                _leaf("svc2", "logical_capability", "Order Service"),
            ])],
            flows=[Flow(source="svc1", target="svc2")],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        xml = assign_logical_shape_types(xml)
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        cells = _cells(xml)
        self.assertIn("svc1", cells)
        self.assertIn("svc2", cells)
        g1, g2 = cells["svc1"].find("mxGeometry"), cells["svc2"].find("mxGeometry")
        self.assertNotEqual(
            (g1.get("x"), g1.get("y")), (g2.get("x"), g2.get("y")),
            "duplicate-label leaves collapsed onto the same position",
        )


class AnnotationRenderingTests(unittest.TestCase):
    """No existing Logical test sets Leaf.annotation. Confirms the
    "Label [Tag]" suffix renders correctly on a loose (parent="1") leaf."""

    def test_annotation_suffix_renders_on_loose_leaf(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_data", label="Data", leaves=[
                _leaf("store", "logical_datastore", "Customer Records", annotation="PII"),
            ])],
            external_actors=[ExternalActor(id="user", kind="users", label="User")],
            flows=[Flow(source="user", target="store")],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        cells = _cells(xml)
        self.assertEqual(cells["store"].get("value"), "Customer Records [PII]")


class ShelfCloudLeafInteractionTests(unittest.TestCase):
    """realistic_logical_ir()'s shelf leaf is deliberately non-cloud-prefixed
    (saas_segment), so the shelf-leaf-inside-cloud_boundary interaction is
    untested. When the shelf's own leaf IS cloud-shaped, it participates in
    the single shared cloud_boundary group like any other cloud leaf — there
    is no separate shelf-specific visual container in the flat storyboard to
    strand it from."""

    def test_cloud_shaped_shelf_leaf_joins_the_shared_cloud_boundary(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "aws_ecs", "Portal")]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("db", "aws_dynamodb", "DB")]),
            ],
            anchored=[AnchoredCorridor(
                id="domain_saas", label="SaaS", anchor_id="domain_data",
                leaves=[_leaf("managed_saas", "aws_cognito", "Cloud Vendor")],
            )],
            flows=[Flow(source="portal", target="db"), Flow(source="db", target="managed_saas")],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        cells = _cells(xml)
        self.assertEqual(cells["managed_saas"].get("parent"), "cloud_boundary")


class ShapeTypeLockstepTests(unittest.TestCase):
    """_LOGICAL_ROLE_TO_SHAPE_TYPE and _LOGICAL_BUILTIN_STYLES are two
    independent dicts that must stay in lockstep by hand — nothing enforced
    this before. A role mapping to a shape_type absent from the builtin
    styles table silently degrades to the generic logical_capability tile
    instead of failing loudly."""

    def test_every_mapped_shape_type_has_a_builtin_style(self) -> None:
        mapped_shape_types = set(_LOGICAL_ROLE_TO_SHAPE_TYPE.values())
        missing = mapped_shape_types - set(_LOGICAL_BUILTIN_STYLES.keys())
        self.assertEqual(missing, set(), f"shape_types with no builtin style: {missing}")


class SurgicalEditInteractionTests(unittest.TestCase):
    """No existing Logical test imports diagram_edits. Confirms adding a node
    into cloud_boundary mid-storyboard survives the generic post-edit repair
    pass cleanly (layout="ir" is correctly stripped by apply_diagram_edits
    for a geometry op, then repair_xml's fit_containers_to_children reflows
    the container)."""

    def test_add_node_into_cloud_boundary_then_repair_is_clean(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "aws_ecs", "Portal")]),
                Corridor(id="domain_core", label="Core", leaves=[_leaf("api", "aws_lambda", "API")]),
            ],
            flows=[Flow(source="portal", target="api")],
        )
        xml = compile_corridor_ir(ir, page_name="Logical")
        cells = _cells(xml)
        self.assertEqual(cells["1"].get("layout"), "ir")

        plan = DiagramEditPlan(applicable=True, ops=[
            DiagramEditOp(op="add_node", id="new_svc", label="New Service", shape_type="aws_sqs", parent="cloud_boundary"),
            DiagramEditOp(op="connect", source="api", target="new_svc"),
        ])
        edited = apply_diagram_edits(xml, plan)
        edited_cells = _cells(edited)
        self.assertNotEqual(edited_cells["1"].get("layout"), "ir", "layout=ir should be stripped by a geometry op")
        self.assertIn("new_svc", edited_cells)

        repaired, diag = repair_xml(edited)
        self.assertEqual(diag.errors, [])
        repaired_cells = _cells(repaired)
        self.assertIn("new_svc", repaired_cells)
        # The new node must actually be (or remain) inside cloud_boundary,
        # not orphaned to root by the reflow.
        self.assertEqual(repaired_cells["new_svc"].get("parent"), "cloud_boundary")


if __name__ == "__main__":
    unittest.main()
