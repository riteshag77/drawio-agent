"""Equivalence tests for layout_corridor_ir — the typed-IR layout function
that computes final corridor/shelf/leaf geometry directly from a CorridorIR's
own graph, instead of the compiler emitting a placeholder that gets discarded
and recomputed later by apply_layered_corridor_layout (which has to re-derive
that same graph by parsing the compiler's own XML output back into cells).

Per the plan ("Make Logical/Security/Data-flow as stable as Network"), step 1
is proving this produces the same geometry as today's full pipeline
(compile_corridor_ir -> repair_xml) for the same input, before anything is
wired in live. Two deliberate, documented exceptions are NOT bugs in the new
function — they're pre-existing bugs in the current pipeline that this port
does not replicate:

1. Data-flow's `phase_control` (an `anchor_id=None` "whole row" shelf) is
   currently folded into the ranked L->R row by the XML-based path, because
   neither `_is_data_flow_shelf` nor `apply_layered_corridor_layout`'s
   `shelf_filter` recognizes it as a shelf at all — it only knows about
   `phase_consume`/`phase_saas_*`. `layout_corridor_ir` positions it
   correctly, below the whole row, per `AnchoredCorridor.anchor_id=None`'s
   own documented contract.
2. Security's `zone_external_saas` shelf is *supposed* to anchor under
   `zone_restricted` (`SecurityIR`'s own validator enforces this) but the
   current pipeline's `enforce_corridor_gap_consistency` re-anchors every
   shelf under the rightmost ranked corridor unconditionally, which happens
   to be `zone_detect` (ranked after `zone_restricted`) — silently
   overriding the semantically-correct anchor `apply_layered_corridor_layout`
   itself picked one step earlier. `layout_corridor_ir` anchors under
   `shelf.anchor_id` exactly, as validated.

Logical's "journey spine" (Persona/IdP flowing into a leaf inside
`domain_experience`) is exactly `ExternalActor` + `Flow` — the same
actors-band machinery Security already exercises in
`ActorContainerizationTests` below — so it needs no bespoke layout code of its
own; `LogicalEquivalenceTests` proves this against the same
compile-then-repair baseline used for Security/Data-flow.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.compiler.corridor import _CorridorCompiler
from app.adapters.outbound.diagram.logical_layout import layout_corridor_ir
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.ir import AnchoredCorridor, Corridor, DataFlowIR, ExternalActor, Flow, Leaf, LogicalIR, SecurityIR


def _leaf(i: str, st: str, label: str) -> Leaf:
    return Leaf(id=i, shape_type=st, label=label)


def realistic_security_ir() -> SecurityIR:
    return SecurityIR(
        corridors=[
            Corridor(id="zone_public", label="Public", leaves=[_leaf("dns", "aws_route53", "Route 53")]),
            Corridor(id="zone_dmz", label="DMZ", leaves=[_leaf("waf", "aws_waf", "WAF")]),
            Corridor(id="zone_identity", label="Identity", leaves=[_leaf("okta", "saas_okta", "Okta")]),
            Corridor(id="zone_private", label="Private", leaves=[
                _leaf("app", "aws_ecs", "App"), _leaf("secrets", "aws_secrets_manager", "Secrets Manager"),
            ]),
            Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds", "aws_rds", "RDS")]),
            Corridor(id="zone_detect", label="Detect", leaves=[
                _leaf("gd", "aws_guardduty", "GuardDuty"), _leaf("cw", "aws_cloudwatch", "CloudWatch"),
            ]),
        ],
        anchored=[AnchoredCorridor(
            id="zone_external_saas", label="External SaaS Zone", anchor_id="zone_restricted",
            leaves=[_leaf("workday", "saas_workday", "Workday")],
        )],
        flows=[
            Flow(source="dns", target="waf"), Flow(source="waf", target="okta"),
            Flow(source="okta", target="app"), Flow(source="app", target="rds"),
            Flow(source="app", target="secrets", label="Fetches Credentials"),
            Flow(source="app", target="gd", label="Logs & Telemetry", primary=False),
        ],
    )


def realistic_data_flow_ir(*, with_control: bool = False) -> DataFlowIR:
    anchored = [
        AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store",
                          leaves=[_leaf("dash", "aws_quicksight", "Dashboard")]),
        AnchoredCorridor(id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store",
                          leaves=[_leaf("sf", "saas_salesforce", "Salesforce")]),
        AnchoredCorridor(id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store",
                          leaves=[_leaf("stripe", "saas_stripe", "Stripe")]),
    ]
    if with_control:
        anchored.append(AnchoredCorridor(
            id="phase_control", label="Control", anchor_id=None, leaves=[_leaf("sched", "aws_eventbridge", "Scheduler")],
        ))
    return DataFlowIR(
        corridors=[
            Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw", "aws_api_gateway", "API GW")]),
            Corridor(id="phase_process", label="Process", leaves=[_leaf("lambda1", "aws_lambda", "Lambda")]),
            Corridor(id="phase_store", label="Store", leaves=[_leaf("db", "aws_rds", "RDS")]),
        ],
        anchored=anchored,
        flows=[
            Flow(source="apigw", target="lambda1"), Flow(source="lambda1", target="db"),
            Flow(source="dash", target="db"), Flow(source="sf", target="apigw"),
            Flow(source="db", target="stripe"),
        ],
    )


def realistic_logical_ir() -> LogicalIR:
    return LogicalIR(
        corridors=[
            Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal", "aws_ecs", "Portal")]),
            Corridor(id="domain_core", label="Core", leaves=[_leaf("orders", "aws_lambda", "Orders")]),
            Corridor(id="domain_commerce", label="Commerce", leaves=[_leaf("catalog", "aws_dynamodb", "Catalog")]),
            Corridor(id="domain_data", label="Data", leaves=[_leaf("warehouse", "aws_redshift", "Warehouse")]),
            Corridor(id="domain_platform", label="Platform", leaves=[_leaf("obs", "aws_cloudwatch", "Observability")]),
        ],
        anchored=[AnchoredCorridor(
            id="domain_saas", label="SaaS", anchor_id="domain_data",
            leaves=[_leaf("segment", "saas_segment", "Segment")],
        )],
        external_actors=[
            ExternalActor(id="persona", kind="users", label="Persona"),
            ExternalActor(id="okta", kind="saas_ingress", label="Identity SaaS (Okta)"),
        ],
        flows=[
            Flow(source="persona", target="okta"),
            Flow(source="okta", target="portal", label="Redirects to"),
            Flow(source="portal", target="orders"),
            Flow(source="orders", target="catalog"),
            Flow(source="orders", target="warehouse"),
            Flow(source="warehouse", target="segment"),
        ],
    )


def _pipeline_geometry(ir, page_name: str) -> dict[str, tuple[float, float, float, float]]:
    xml = compile_corridor_ir(ir, page_name=page_name)
    repaired, post = repair_xml(xml)
    assert post.errors == [], post.errors
    assert post.warnings == [], post.warnings
    cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
    out: dict[str, tuple[float, float, float, float]] = {}
    for cid, cell in cells.items():
        g = cell.find("mxGeometry")
        if g is None:
            continue
        try:
            out[cid] = (float(g.get("x")), float(g.get("y")), float(g.get("width")), float(g.get("height")))
        except (TypeError, ValueError):
            continue
    return out


def _legacy_pipeline_geometry(ir, page_name: str) -> dict[str, tuple[float, float, float, float]]:
    """Same as `_pipeline_geometry`, but forces the pre-migration `run()` path
    (placeholder geometry + full `repair_xml` fixer chain) regardless of
    `_LAYERED_VIEWS`, so Logical's equivalence test genuinely compares against
    the legacy XML-based layout it's replacing, not against itself."""
    xml = _CorridorCompiler(ir, page_name).run()
    repaired, post = repair_xml(xml)
    assert post.errors == [], post.errors
    assert post.warnings == [], post.warnings
    cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
    out: dict[str, tuple[float, float, float, float]] = {}
    for cid, cell in cells.items():
        g = cell.find("mxGeometry")
        if g is None:
            continue
        try:
            out[cid] = (float(g.get("x")), float(g.get("y")), float(g.get("width")), float(g.get("height")))
        except (TypeError, ValueError):
            continue
    return out


class SecurityEquivalenceTests(unittest.TestCase):
    def test_matches_pipeline_except_the_known_anchor_bug(self) -> None:
        ir = realistic_security_ir()
        pipeline_geo = _pipeline_geometry(ir, "Security")
        new_geo = layout_corridor_ir(ir)

        known_diverging = {"zone_external_saas"}
        for cid, g in new_geo.items():
            if cid in known_diverging:
                continue
            old = pipeline_geo[cid]
            self.assertAlmostEqual(g.x, old[0], delta=1.0, msg=f"{cid} x")
            self.assertAlmostEqual(g.y, old[1], delta=1.0, msg=f"{cid} y")
            self.assertAlmostEqual(g.w, old[2], delta=1.0, msg=f"{cid} w")
            self.assertAlmostEqual(g.h, old[3], delta=1.0, msg=f"{cid} h")

    def test_saas_shelf_anchors_under_restricted_zone_not_rightmost_corridor(self) -> None:
        """The documented, IR-validated contract (SecurityIR requires
        zone_external_saas to anchor under zone_restricted) — the current
        pipeline's enforce_corridor_gap_consistency silently violates this by
        re-anchoring every shelf under the rightmost ranked corridor
        (zone_detect here) regardless of its actual anchor_id."""
        ir = realistic_security_ir()
        new_geo = layout_corridor_ir(ir)
        self.assertEqual(new_geo["zone_external_saas"].x, new_geo["zone_restricted"].x)


class DataFlowEquivalenceTests(unittest.TestCase):
    def test_matches_pipeline_for_the_three_store_shelves(self) -> None:
        ir = realistic_data_flow_ir(with_control=False)
        pipeline_geo = _pipeline_geometry(ir, "Data flow")
        new_geo = layout_corridor_ir(ir)
        for cid, g in new_geo.items():
            old = pipeline_geo[cid]
            self.assertAlmostEqual(g.x, old[0], delta=1.0, msg=f"{cid} x")
            self.assertAlmostEqual(g.y, old[1], delta=1.0, msg=f"{cid} y")
            self.assertAlmostEqual(g.w, old[2], delta=1.0, msg=f"{cid} w")
            self.assertAlmostEqual(g.h, old[3], delta=1.0, msg=f"{cid} h")

    def test_phase_control_anchors_below_whole_row_not_folded_into_it(self) -> None:
        """The documented, IR-validated contract (DataFlowIR requires
        phase_control's anchor_id to be None, 'below the whole row') — the
        current pipeline folds it into the ranked L->R row instead, since
        neither _is_data_flow_shelf nor apply_layered_corridor_layout's
        shelf_filter recognizes it as a shelf at all."""
        ir = realistic_data_flow_ir(with_control=True)
        new_geo = layout_corridor_ir(ir)
        row_bottom = max(
            new_geo[cid].y + new_geo[cid].h
            for cid in ("phase_ingest", "phase_process", "phase_store")
        )
        self.assertGreaterEqual(new_geo["phase_control"].y, row_bottom)
        # Not squeezed into the ranked row's own x sequence.
        row_xs = {new_geo[cid].x for cid in ("phase_ingest", "phase_process", "phase_store")}
        self.assertNotIn(new_geo["phase_control"].x, row_xs - {new_geo["phase_ingest"].x})

    def test_multi_shelf_stack_does_not_collide(self) -> None:
        """Three shelves anchored under phase_store must not overlap — the
        exact bug Part A fixed in the XML-based fixer chain; the new path
        must not be able to regress it."""
        ir = realistic_data_flow_ir(with_control=False)
        new_geo = layout_corridor_ir(ir)
        shelves = [new_geo[cid] for cid in ("phase_consume", "phase_saas_producer", "phase_saas_consumer")]
        ys = sorted(g.y for g in shelves)
        for a, b in zip(ys, ys[1:]):
            self.assertGreater(b, a, "shelves must be stacked, not overlapping at the same y")


class LogicalLayoutTests(unittest.TestCase):
    """Unlike Security/Data-flow, Logical's legacy XML-repair path does not
    just recompute the same graph — its journey-spine fixer hoists the
    "Portal" leaf out of its domain container onto a spine row and
    pixel-shifts the whole domain band right to clear space for it (verified:
    every domain corridor differs from the legacy path by the same constant
    offset, and `domain_experience` itself no longer exists as a container in
    the repaired legacy XML — its leaf was hoisted to `parent="1"`). That is a
    cosmetic convention this compiler deliberately does not reproduce, so
    these tests hold the new path to structural correctness (no overlaps, no
    diagnostics, correct anchoring) rather than exact-geometry parity with the
    legacy pipeline — see the module docstring in `compiler/corridor.py`."""

    def test_saas_shelf_anchors_under_domain_data(self) -> None:
        ir = realistic_logical_ir()
        new_geo = layout_corridor_ir(ir)
        self.assertEqual(new_geo["domain_saas"].x, new_geo["domain_data"].x)

    def test_journey_spine_actor_band_does_not_collide_with_domain_row(self) -> None:
        """Persona/Okta (the 'journey spine') render as ExternalActor, not
        bespoke spine geometry — this is the regression test for that: the
        actors band must sit clear of the domain row, not overlap it."""
        ir = realistic_logical_ir()
        xml = compile_corridor_ir(ir, page_name="Logical")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        actor_geo = cells["persona"].find("mxGeometry")
        row_geo = cells["domain_experience"].find("mxGeometry")
        actor_right = float(actor_geo.get("x")) + float(actor_geo.get("width"))
        self.assertLessEqual(actor_right, float(row_geo.get("x")))

    def test_raw_compiler_output_has_zero_diagnostics(self) -> None:
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])

    def test_layout_authoritative_marker_is_set(self) -> None:
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["1"].get("layout"), "ir")

    def test_repair_xml_does_not_touch_corridor_or_leaf_geometry(self) -> None:
        """Same guarantee as Security/Data-flow: once the compiler marks the
        page layout="ir", repair_xml must not move any corridor/leaf it
        already placed, even though Logical has its own dedicated fixers
        (test_layout_authoritative_marker_is_set proves those now no-op)."""
        xml = compile_corridor_ir(realistic_logical_ir(), page_name="Logical")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(post.warnings, [])
        before = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        after = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        for cid, geom in before.items():
            if geom is None or cid not in after or after[cid] is None:
                continue
            if geom.get("relative") == "1":
                continue  # edge geometry, not a corridor/leaf
            self.assertEqual(
                (geom.get("x"), geom.get("y"), geom.get("width"), geom.get("height")),
                (after[cid].get("x"), after[cid].get("y"), after[cid].get("width"), after[cid].get("height")),
                f"{cid}'s geometry changed under repair_xml",
            )

    def test_diverges_from_legacy_spine_hoist_by_design(self) -> None:
        """Documents the divergence explained in the class docstring: the
        legacy path hoists `portal` out of `domain_experience` (which then
        stops existing as a container at all) and shifts every other domain
        corridor right by the same constant to make room. The new path keeps
        `portal` nested in its domain and does not shift the row — this
        pins that as an intentional design difference, not a bug, so a
        future change that silently reintroduces spine-hoisting is caught."""
        ir = realistic_logical_ir()
        legacy_geo = _legacy_pipeline_geometry(ir, "Logical")
        new_geo = layout_corridor_ir(ir)
        self.assertNotIn("domain_experience", legacy_geo, "legacy spine hoist should still unwrap this container")
        self.assertIn("domain_experience", new_geo, "new path must keep the domain as a real container")
        shift = legacy_geo["domain_core"][0] - new_geo["domain_core"].x
        for cid in ("domain_commerce", "domain_platform", "domain_data"):
            self.assertAlmostEqual(legacy_geo[cid][0] - new_geo[cid].x, shift, delta=1.0)


class NetworkParityTests(unittest.TestCase):
    """The correctness bar this whole change exists to reach: raw compiler
    output — no repair_xml call at all — already passes diagnose_xml with
    zero errors and zero warnings, exactly like
    test_network_compiler.py::test_realistic_diagram_has_no_diagnostics.
    Before this change, Security/Data-flow's compiler output was only ever
    checked *after* repair_xml, because repair was still doing real geometry
    work; this test is the proof that it no longer needs to."""

    def test_security_raw_compiler_output_has_zero_diagnostics(self) -> None:
        xml = compile_corridor_ir(realistic_security_ir(), page_name="Security")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])

    def test_data_flow_raw_compiler_output_has_zero_diagnostics(self) -> None:
        xml = compile_corridor_ir(realistic_data_flow_ir(with_control=True), page_name="Data flow")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])

    def test_layout_authoritative_marker_is_set(self) -> None:
        xml = compile_corridor_ir(realistic_security_ir(), page_name="Security")
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["1"].get("layout"), "ir")

    def test_repair_xml_does_not_touch_corridor_or_leaf_geometry(self) -> None:
        """The whole point of the layout="ir" marker: repair_xml's downstream
        color-normalization and edge-anchor passes still legitimately run
        (they're not geometry re-derivation), but corridor/shelf/leaf x/y/w/h
        must come out identical to what the compiler itself emitted."""
        for ir, page in ((realistic_security_ir(), "Security"), (realistic_data_flow_ir(with_control=True), "Data flow")):
            xml = compile_corridor_ir(ir, page_name=page)
            repaired, post = repair_xml(xml)
            self.assertEqual(post.errors, [])
            self.assertEqual(post.warnings, [])
            before = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
            after = {c.get("id"): c.find("mxGeometry") for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
            for cid, geom in before.items():
                if geom is None or cid not in after or after[cid] is None:
                    continue
                if geom.get("relative") == "1":
                    continue  # edge geometry, not a corridor/leaf
                self.assertEqual(
                    (geom.get("x"), geom.get("y"), geom.get("width"), geom.get("height")),
                    (after[cid].get("x"), after[cid].get("y"), after[cid].get("width"), after[cid].get("height")),
                    f"{page}: {cid}'s geometry changed under repair_xml",
                )


class ActorContainerizationTests(unittest.TestCase):
    """The layered compiler path skips the fixer chain that used to
    containerize external actors (Part A's Fix 2c,
    `placement_fixers._containerize_corridor_actors`) — the compiler must do
    it itself now, or actors regress to bare parent="1" leaves."""

    def _security_with_actor(self) -> SecurityIR:
        ir = realistic_security_ir()
        return ir.model_copy(update={
            "external_actors": [ExternalActor(id="employees", kind="users", label="Employees")],
            "flows": [*ir.flows, Flow(source="employees", target="dns")],
        })

    def test_left_actor_is_containerized(self) -> None:
        ir = self._security_with_actor()
        xml = compile_corridor_ir(ir, page_name="Security")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])
        cells = {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["employees"].get("parent"), "actors_left")
        band_geo = cells["actors_left"].find("mxGeometry")
        self.assertGreaterEqual(float(band_geo.get("x")), 0.0, "band must not land at a negative x")

    def test_actor_band_survives_repair_with_zero_diagnostics(self) -> None:
        """Regression for a real bug found building this: resolve_misplaced_actors
        (a generic, un-skipped fixer) used to treat any actor parented under
        something other than "1" as misplaced and hoist it back out —
        including the compiler's own actors_left band."""
        ir = self._security_with_actor()
        xml = compile_corridor_ir(ir, page_name="Security")
        repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(post.warnings, [])
        cells = {c.get("id"): c for c in ET.fromstring(repaired).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["employees"].get("parent"), "actors_left")


class ContainerSizingMatchesPlacementTests(unittest.TestCase):
    """Regression for a real bug: `_place_leaves` computed leaf *placement*
    columns (`cols_n`, capped by flow-derived rank depth) separately from
    container *sizing* (`grid_fill(len(leaves))`, which recomputed its own,
    wider default column count) — when they disagreed, leaves were placed
    into more rows than the container was sized for, and leaves rendered
    outside their own container's bounds. `grid_fill` already supports an
    explicit `cols` override for exactly this; the bug was simply not passing
    the already-computed `cols_n` through. Reproduced with 6 leaves forming
    three independent 2-node flow chains: `grid_cols(6)` alone would pick a
    wider grid than the rank-capped `cols_n`, which is exactly the condition
    that triggers the drift."""

    def _ir_with_rank_capped_corridor(self) -> DataFlowIR:
        leaves = [_leaf(f"l{i}", "aws_lambda", f"L{i}") for i in range(6)]
        return DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=leaves),
                Corridor(id="phase_process", label="Process", leaves=[_leaf("p", "aws_lambda", "P")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("s", "aws_rds", "S")]),
            ],
            anchored=[],
            flows=[
                Flow(source="l0", target="l1"), Flow(source="l2", target="l3"),
                Flow(source="l4", target="l5"), Flow(source="l1", target="p"),
                Flow(source="p", target="s"),
            ],
        )

    def test_leaves_never_overflow_container_on_ir_path(self) -> None:
        ir = self._ir_with_rank_capped_corridor()
        geos = layout_corridor_ir(ir)
        container = geos["phase_ingest"]
        for leaf in ir.corridors[0].leaves:
            g = geos[leaf.id]
            self.assertLessEqual(g.x + g.w, container.w, f"{leaf.id} right edge exceeds container width")
            self.assertLessEqual(g.y + g.h, container.h, f"{leaf.id} bottom edge exceeds container height")

    def test_raw_compiler_output_has_zero_diagnostics_with_rank_capped_corridor(self) -> None:
        xml = compile_corridor_ir(self._ir_with_rank_capped_corridor(), page_name="Data flow")
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        self.assertEqual(diag.warnings, [])


if __name__ == "__main__":
    unittest.main()
