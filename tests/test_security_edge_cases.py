"""Security view battle-test coverage — the fixes from the Security
correctness audit (corridor leaf wrap, shelf/legend/actor overhang, the
perimeter actor-id synthesis, the partner-bypass and protection-consistency
checks, the intra-zone boundary false positive, and the design_tradeoffs
nudge).

The wrap and overhang fixes live in shared corridor code, so the geometry
assertions here double as the regression net for Logical and Data-flow.
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import story_step_cap
from app.adapters.outbound.diagram.compiler import compile_corridor_ir
from app.adapters.outbound.diagram.compiler.legend import build_legend
from app.adapters.outbound.diagram.logical_layout import layout_corridor_ir
from app.adapters.outbound.diagram.view_conformance import score_view
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml, repair_xml
from app.domain.ir import (
    AnchoredCorridor,
    Corridor,
    ExternalActor,
    Flow,
    Leaf,
    NetworkIR,
    SecurityIR,
)

_ALL_ZONES = (
    "zone_public", "zone_dmz", "zone_identity", "zone_partner",
    "zone_private", "zone_restricted", "zone_detect",
)


def _leaf(i: str, st: str = "aws_ecs", label: str | None = None, **kw) -> Leaf:
    return Leaf(id=i, shape_type=st, label=label or i.replace("_", " ").title(), **kw)


def _cells(xml: str) -> dict[str, ET.Element]:
    return {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}


def _geom(cell: ET.Element) -> tuple[float, float, float, float]:
    g = cell.find("mxGeometry")
    return tuple(float(g.get(k) or 0) for k in ("x", "y", "width", "height"))  # type: ignore[return-value]


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


class CorridorLeafWrapTests(unittest.TestCase):
    """1.1 — the headline fix. A corridor's leaves wrap into a real grid
    instead of one unbounded column.

    Before the fix, 8 leaves produced a 126x1784 sliver (single column,
    scrambled order) and `_equalize_row_band` propagated that height to every
    row corridor — worst on Security, which has the most rows.
    """

    def _ir(self, n: int) -> SecurityIR:
        return SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf(f"p{i}") for i in range(n)]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
            ],
            flows=[Flow(source="p0", target="app", protection="tls")],
            purpose="wrap fixture",
        )

    def test_eight_leaves_wrap_into_a_grid_not_one_column(self) -> None:
        geos, _routes = layout_corridor_ir(self._ir(8))
        box = geos["zone_public"]
        distinct_x = {geos[f"p{i}"].x for i in range(8)}
        distinct_y = {geos[f"p{i}"].y for i in range(8)}
        self.assertGreater(len(distinct_x), 1, "leaves never wrapped into columns")
        self.assertGreater(len(distinct_y), 1, "leaves never wrapped into rows")
        self.assertGreater(box.w, 400, f"corridor stayed one column wide: {box}")
        self.assertLess(box.h, 700, f"corridor grew unbounded in height: {box}")

    def test_leaf_order_follows_declaration_order_within_the_grid(self) -> None:
        """ELK returned leaves in an arbitrary order (p0,p7,p2,p1,...); the
        deterministic grid reads left-to-right, top-to-bottom."""
        geos, _routes = layout_corridor_ir(self._ir(8))
        ordered = sorted(range(8), key=lambda i: (geos[f"p{i}"].y, geos[f"p{i}"].x))
        self.assertEqual(ordered, list(range(8)))

    def test_one_dense_zone_does_not_inflate_every_other_zone(self) -> None:
        geos, _routes = layout_corridor_ir(self._ir(8))
        # Rows still share one band (that is intended), but the band is now
        # the wrapped height, not a 1784px column.
        self.assertLess(geos["zone_private"].h, 700)

    def test_wrapped_corridor_compiles_with_no_diagnostics(self) -> None:
        xml = compile_corridor_ir(self._ir(8), page_name="Security")
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])
        self.assertEqual(post.warnings, [])

    def test_no_leaf_escapes_its_corridor_box(self) -> None:
        ir = self._ir(9)
        geos, _routes = layout_corridor_ir(ir)
        box = geos["zone_public"]
        for i in range(9):
            lg = geos[f"p{i}"]
            self.assertGreaterEqual(lg.x, 0)
            self.assertGreaterEqual(lg.y, 0)
            self.assertLessEqual(lg.x + lg.w, box.w + 1.0, f"p{i} overflows horizontally")
            self.assertLessEqual(lg.y + lg.h, box.h + 1.0, f"p{i} overflows vertically")


class ShelfOverhangTests(unittest.TestCase):
    """1.2 — content_right must span shelves, or the legend and the
    right-hand actor band get placed on top of a wide shelf."""

    def _ir(self) -> SecurityIR:
        return SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds")]),
                Corridor(id="zone_detect", label="Detect", leaves=[_leaf("gd")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="External SaaS", anchor_id="zone_restricted",
                leaves=[_leaf(f"sv{i}", "saas_workday") for i in range(9)],
            )],
            external_actors=[
                ExternalActor(id=f"sa{i}", kind="saas_egress", label=f"Vendor{i}") for i in range(4)
            ],
            flows=[
                Flow(source="dns", target="app", protection="tls"),
                Flow(source="app", target="rds"),
                Flow(source="rds", target="sv0"),
                *[Flow(source="rds", target=f"sa{i}") for i in range(4)],
            ],
            purpose="shelf overhang fixture",
        )

    def test_right_actors_and_legend_clear_a_wide_shelf(self) -> None:
        cells = _cells(compile_corridor_ir(self._ir(), page_name="Security"))
        shelf = _geom(cells["zone_external_saas"])
        for cid in ("legend_box", "sa0", "sa1", "sa2", "sa3"):
            if cid in cells:
                self.assertFalse(
                    _overlaps(shelf, _geom(cells[cid])),
                    f"{cid} overlaps the zone_external_saas shelf",
                )


class SevenZoneTests(unittest.TestCase):
    """No fixture and no real capture has ever built all 7 zones at once."""

    def _ir(self) -> SecurityIR:
        return SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("waf", "aws_waf", "WAF")]),
                Corridor(id="zone_dmz", label="DMZ", leaves=[_leaf("alb", "aws_alb", "Public ALB")]),
                Corridor(id="zone_identity", label="Identity", leaves=[_leaf("idp", "saas_okta", "Okta")]),
                Corridor(id="zone_partner", label="Partner", leaves=[
                    _leaf("pgw", "aws_api_gateway", "Partner API Gateway"),
                    _leaf("broker", "security_partner_org", "Broker Org"),
                ]),
                Corridor(id="zone_private", label="Private", leaves=[
                    _leaf("app"), _leaf("secrets", "aws_secrets_manager", "Secrets Manager"),
                ]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("rds", "aws_rds", "Claims DB")]),
                Corridor(id="zone_detect", label="Detect", leaves=[_leaf("gd", "aws_guardduty", "GuardDuty")]),
            ],
            flows=[
                Flow(source="waf", target="alb", label="inspect", protection="tls"),
                Flow(source="alb", target="idp", label="AuthN (TLS)", protection="tls"),
                Flow(source="broker", target="pgw", label="Partner API (mTLS)", protection="mtls"),
                Flow(source="pgw", target="app", label="Scoped Partner API", protection="mtls"),
                Flow(source="idp", target="app", label="issues token", protection="tls"),
                Flow(source="app", target="rds", label="Encrypted (TLS)", protection="tls"),
                Flow(source="app", target="secrets", label="Fetches Credentials (TLS)", protection="tls"),
                Flow(source="app", target="gd", label="Logs & Telemetry", primary=False),
            ],
            purpose="Full trust-axis journey.",
            design_tradeoffs=["mTLS on the partner link over one-way TLS: the partner must authenticate too."],
        )

    def test_all_seven_zones_compile_clean(self) -> None:
        ir = self._ir()
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual(ir.semantic_warnings(), [])
        xml = compile_corridor_ir(ir, page_name="Security")
        self.assertEqual(diagnose_xml(xml).errors, [])
        _repaired, post = repair_xml(xml)
        self.assertEqual(post.errors, [])

    def test_zones_render_in_canonical_trust_order(self) -> None:
        geos, _routes = layout_corridor_ir(self._ir())
        xs = [geos[z].x for z in _ALL_ZONES]
        self.assertEqual(xs, sorted(xs), "zones are not left-to-right in canonical rank order")

    def test_seven_zone_page_scores_clean_conformance(self) -> None:
        xml = compile_corridor_ir(self._ir(), page_name="Security")
        scored = score_view(xml, view="security")
        self.assertNotIn("group_taxonomy", {v.code for v in scored.violations})
        self.assertNotIn("unprotected_boundary_flow", {v.code for v in scored.violations})


class PerimeterActorIdTests(unittest.TestCase):
    """1.3 — a relocated perimeter service must get a legal, collision-free
    id. 22 of 30 vendor labels are multi-word, so 'label as id' produced an
    illegal id whose failure was swallowed into a direct-XML fallback."""

    def _raw(self, actors: list[dict], public_leaves: list[dict] | None = None) -> dict:
        return {
            "view": "security", "title": "T",
            "corridors": [
                {"id": "zone_public", "label": "Public",
                 "leaves": public_leaves or [{"id": "seed", "shape_type": "aws_ecs", "label": "Seed"}]},
                {"id": "zone_private", "label": "Private",
                 "leaves": [{"id": "app", "shape_type": "aws_ecs", "label": "App"}]},
            ],
            "external_actors": actors,
            "flows": [{"source": "seed", "target": "app"}],
        }

    def _public_leaves(self, ir: SecurityIR) -> list[tuple[str, str]]:
        return [(l.id, l.label) for c in ir.corridors if c.id == "zone_public" for l in c.leaves]

    def test_id_less_multi_word_label_becomes_a_legal_id(self) -> None:
        ir = SecurityIR.model_validate(self._raw([{"kind": "saas_ingress", "label": "Route 53"}]))
        self.assertIn(("aws_route53", "Route 53"), self._public_leaves(ir))

    def test_id_less_single_word_label_becomes_a_legal_id(self) -> None:
        ir = SecurityIR.model_validate(self._raw([{"kind": "saas_ingress", "label": "CloudFront"}]))
        self.assertIn(("aws_cloudfront", "CloudFront"), self._public_leaves(ir))

    def test_colliding_id_is_suffixed_not_fatal(self) -> None:
        ir = SecurityIR.model_validate(self._raw(
            [{"id": "waf", "kind": "saas_ingress", "label": "AWS WAF"}],
            public_leaves=[{"id": "waf", "shape_type": "aws_ecs", "label": "Existing WAF"}],
        ))
        ids = [lid for lid, _ in self._public_leaves(ir)]
        self.assertEqual(sorted(ids), ["waf", "waf_2"])

    def test_legal_declared_id_is_preserved(self) -> None:
        """A relocated actor's own id moves buckets — it must not be treated
        as colliding with itself."""
        ir = SecurityIR.model_validate(self._raw([{"id": "my_dns", "kind": "saas_ingress", "label": "Route 53"}]))
        self.assertIn("my_dns", [lid for lid, _ in self._public_leaves(ir)])

    def test_two_id_less_actors_sharing_a_shape_do_not_collide(self) -> None:
        ir = SecurityIR.model_validate(self._raw([
            {"kind": "saas_ingress", "label": "Route 53"},
            {"kind": "saas_ingress", "label": "Amazon Route 53"},
        ]))
        ids = [lid for lid, _ in self._public_leaves(ir)]
        self.assertEqual(len(ids), len(set(ids)), f"duplicate ids: {ids}")

    def test_non_perimeter_actor_is_left_alone(self) -> None:
        ir = SecurityIR.model_validate(self._raw([{"id": "u", "kind": "users", "label": "Users"}]))
        self.assertEqual([a.label for a in ir.external_actors], ["Users"])


class NetworkPerimeterActorIdTests(unittest.TestCase):
    """1.3 — Network copied the function byte-identically, so it carried the
    identical bug into edge_layer."""

    def _raw(self, actors: list[dict], edge: dict | None = None) -> dict:
        data = {
            "view": "network", "platform": "aws", "title": "T",
            "cloud": {"id": "cloud", "label": "AWS", "regions": [{
                "id": "r", "label": "us-east-1", "vpcs": [{
                    "id": "vpc", "label": "V", "zones": [{
                        "id": "az", "label": "AZ", "subnets": [{
                            "id": "sub", "tier": "app",
                            "leaves": [{"id": "svc", "shape_type": "aws_ecs", "label": "App"}],
                        }],
                    }],
                }],
            }]},
            "external_actors": actors,
            "flows": [{"source": "u", "target": "svc", "label": "HTTPS", "primary": True}],
        }
        if edge:
            data["cloud"]["edge_layer"] = edge
        return data

    def _edge_leaves(self, ir: NetworkIR) -> list[tuple[str, str]]:
        return [(l.id, l.label) for l in (ir.cloud.edge_layer.leaves if ir.cloud.edge_layer else [])]

    def test_id_less_multi_word_label_becomes_a_legal_id(self) -> None:
        ir = NetworkIR.model_validate(self._raw([
            {"id": "u", "kind": "users", "label": "U"},
            {"kind": "saas_ingress", "label": "Route 53"},
        ]))
        self.assertIn(("aws_route53", "Route 53"), self._edge_leaves(ir))

    def test_colliding_id_is_suffixed_not_fatal(self) -> None:
        ir = NetworkIR.model_validate(self._raw(
            [{"id": "u", "kind": "users", "label": "U"},
             {"id": "cf", "kind": "saas_ingress", "label": "CloudFront"}],
            edge={"id": "edge_layer", "label": "Edge",
                  "leaves": [{"id": "cf", "shape_type": "aws_s3", "label": "Existing CF"}]},
        ))
        ids = [lid for lid, _ in self._edge_leaves(ir)]
        self.assertEqual(sorted(ids), ["cf", "cf_2"])


class BoundaryCrossingTests(unittest.TestCase):
    """1.7 — 'crosses a boundary' means exactly one end is inside one."""

    def test_intra_boundary_zone_flow_does_not_warn(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[
                    _leaf("dns", "aws_route53", "Route 53"), _leaf("cf", "aws_cloudfront", "CloudFront"),
                ]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
            ],
            flows=[Flow(source="dns", target="cf"), Flow(source="cf", target="app", protection="tls")],
            purpose="p",
        )
        self.assertFalse([w for w in ir.semantic_warnings() if "boundary" in w])

    def test_genuine_crossing_without_protection_warns(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("cf", "aws_cloudfront", "CloudFront")]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
            ],
            flows=[Flow(source="cf", target="app")],
            purpose="p",
        )
        self.assertTrue([w for w in ir.semantic_warnings() if "boundary" in w])

    def test_conformance_scoring_agrees_with_the_ir_warning(self) -> None:
        """The same rule is implemented twice (IR + view_conformance); they
        must not disagree about what a crossing is."""
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[
                    _leaf("dns", "aws_route53", "Route 53"), _leaf("cf", "aws_cloudfront", "CloudFront"),
                ]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
            ],
            flows=[Flow(source="dns", target="cf"), Flow(source="cf", target="app", protection="tls")],
            purpose="p",
        )
        xml = compile_corridor_ir(ir, page_name="Security")
        codes = {v.code for v in score_view(xml, view="security").violations}
        self.assertNotIn("unprotected_boundary_flow", codes)


class PartnerBypassTests(unittest.TestCase):
    """1.6 — security.txt's Forbidden list said partner traffic must enter
    through the Partner API Gateway; nothing enforced it."""

    def _ir(self, source: str) -> SecurityIR:
        return SecurityIR(
            corridors=[
                Corridor(id="zone_partner", label="Partner", leaves=[
                    _leaf("broker", "security_partner_org", "Broker Feed"),
                    _leaf("pgw", "aws_api_gateway", "Partner API Gateway"),
                ]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
            ],
            flows=[Flow(source=source, target="app", label="Scoped Partner API", protection="mtls")],
            purpose="p",
        )

    def test_partner_leaf_straight_into_private_warns(self) -> None:
        warnings = self._ir("broker").semantic_warnings()
        self.assertTrue([w for w in warnings if "straight into" in w], warnings)

    def test_routing_through_the_gateway_does_not_warn(self) -> None:
        warnings = self._ir("pgw").semantic_warnings()
        self.assertFalse([w for w in warnings if "straight into" in w], warnings)


class ProtectionLabelConsistencyTests(unittest.TestCase):
    """1.6 — a flow labeled (mTLS)/(TLS) must carry the matching field."""

    def _ir(self, label: str, protection: str) -> SecurityIR:
        return SecurityIR(
            corridors=[Corridor(id="zone_private", label="Private", leaves=[_leaf("a"), _leaf("b")])],
            flows=[Flow(source="a", target="b", label=label, protection=protection)],
            purpose="p",
        )

    def test_mtls_label_with_no_protection_warns(self) -> None:
        self.assertTrue([w for w in self._ir("Partner API (mTLS)", "none").semantic_warnings() if "labeled" in w])

    def test_tls_label_with_no_protection_warns(self) -> None:
        self.assertTrue([w for w in self._ir("Fetches Credentials (TLS)", "none").semantic_warnings() if "labeled" in w])

    def test_matching_label_and_field_does_not_warn(self) -> None:
        self.assertFalse([w for w in self._ir("Partner API (mTLS)", "mtls").semantic_warnings() if "labeled" in w])

    def test_unlabeled_internal_flow_does_not_warn(self) -> None:
        self.assertFalse([w for w in self._ir("Logs & Telemetry", "none").semantic_warnings() if "labeled" in w])


class DesignTradeoffsNudgeTests(unittest.TestCase):
    """1.5 — the gap already closed for Data-flow, and corridor.py names
    Security as the view where the field matters most."""

    def _ir(self, tradeoffs: list[str]) -> SecurityIR:
        return SecurityIR(
            corridors=[
                Corridor(id="zone_private", label="Private", leaves=[_leaf("a"), _leaf("b"), _leaf("c")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db", "aws_rds", "DB")]),
            ],
            flows=[Flow(source="a", target="b"), Flow(source="b", target="c"), Flow(source="c", target="db")],
            purpose="p",
            design_tradeoffs=tradeoffs,
        )

    def test_empty_design_tradeoffs_warns(self) -> None:
        self.assertTrue([w for w in self._ir([]).semantic_warnings() if "design_tradeoffs" in w])

    def test_populated_design_tradeoffs_does_not_warn(self) -> None:
        warnings = self._ir(["mTLS over one-way TLS: the partner authenticates too."]).semantic_warnings()
        self.assertFalse([w for w in warnings if "design_tradeoffs" in w])


class OverviewAndLegendTests(unittest.TestCase):
    def test_blank_threat_model_renders_no_bullet(self) -> None:
        """1.9 — the schema description used to invite a literal "N/A",
        which _overview_notes would then render as a real bullet."""
        import json

        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db", "aws_rds", "DB")]),
            ],
            flows=[Flow(source="app", target="db", protection="tls")],
            purpose="Trust boundary overview.",
            threat_model_reference="",
        )
        notes = json.loads(_cells(compile_corridor_ir(ir, page_name="Security"))["1"].get("business_notes"))
        self.assertTrue(any(n.startswith("Purpose:") for n in notes))
        self.assertFalse([n for n in notes if "Threat model" in n])

    def test_security_legend_does_not_claim_a_single_colour_ramp(self) -> None:
        """1.8 — identity/partner/detect are deliberately off the trust axis
        (palette.SECURITY_TRUST_ORDER); the old row implied one ramp for all."""
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("waf", "aws_waf", "WAF")]),
                Corridor(id="zone_partner", label="Partner", leaves=[_leaf("pgw", "aws_api_gateway", "Partner API Gateway")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db", "aws_rds", "DB")]),
            ],
            anchored=[AnchoredCorridor(
                id="zone_external_saas", label="External SaaS", anchor_id="zone_restricted",
                leaves=[_leaf("wd", "saas_workday", "Workday")],
            )],
            flows=[
                Flow(source="waf", target="pgw", protection="tls"),
                Flow(source="pgw", target="db", protection="mtls"),
                Flow(source="db", target="wd", primary=False, protection="tls"),
            ],
            purpose="p",
        )
        body = build_legend(ir) or ""
        self.assertIn("off that axis", body)

    def test_classified_flow_gets_a_sensitivity_legend_row_on_security(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db", "aws_rds", "DB")]),
            ],
            flows=[Flow(source="app", target="db", protection="tls", classification="pii")],
            purpose="p",
        )
        self.assertIn("data sensitivity", build_legend(ir) or "")


class StoryStepCapTests(unittest.TestCase):
    def test_security_gets_room_for_a_full_trust_journey(self) -> None:
        """1.10 — security.txt's own worked journey is 6 hops."""
        self.assertGreaterEqual(story_step_cap("Security"), 6)


class CriticalityPreservationTests(unittest.TestCase):
    def test_critical_restricted_leaf_survives_assign_saas_shape_types(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import assign_saas_shape_types

        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_private", label="Private", leaves=[_leaf("app")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[
                    _leaf("db", "logical_saas", "Critical Vendor DB", criticality="critical"),
                ]),
            ],
            flows=[Flow(source="app", target="db", protection="tls")],
            purpose="p",
        )
        cells = _cells(assign_saas_shape_types(compile_corridor_ir(ir, page_name="Security")))
        self.assertEqual(cells["db"].get("criticality"), "critical")
        self.assertIn("B85450", cells["db"].get("style"))


if __name__ == "__main__":
    unittest.main()
