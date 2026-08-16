"""Deterministic flow-spine synthesis when the LLM leaves flows empty."""

from __future__ import annotations

import unittest

from app.domain.ir import (
    AvailabilityZone,
    CloudBoundary,
    Corridor,
    CrossCuttingGroup,
    EdgeLayer,
    ExternalActor,
    Flow,
    Leaf,
    LogicalIR,
    NetworkIR,
    Region,
    SecurityIR,
    Subnet,
    Vpc,
)
from app.domain.ir.flow_spine import synthesize_spine_flows
from app.core.services.agent import _generate_ir_with_corrections, _is_empty_flows_error


def _leaf(i: str, st: str = "aws_ecs", label: str = "Svc") -> Leaf:
    return Leaf(id=i, shape_type=st, label=label)


class FlowSpineTests(unittest.TestCase):
    def test_corridor_empty_flows_get_spine(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="E", leaves=[_leaf("portal", "logical_portal", "Portal")]),
                Corridor(id="domain_core", label="C", leaves=[
                    _leaf("item", "logical_capability", "Item"),
                    _leaf("reviews", "logical_capability", "Reviews"),
                ]),
                Corridor(id="domain_data", label="D", leaves=[_leaf("store", "logical_datastore", "Store")]),
            ],
            external_actors=[ExternalActor(id="users", kind="users", label="Users")],
            flows=[],
        )
        self.assertTrue(any("flows is empty" in e for e in ir.semantic_errors()))
        self.assertTrue(synthesize_spine_flows(ir))
        self.assertGreaterEqual(len(ir.flows), 3)
        self.assertEqual(ir.flows[0].source, "users")
        self.assertEqual(ir.semantic_errors(), [])

    def test_wide_corridors_leave_no_orphans(self) -> None:
        """The spine used to thread only the first three leaves of a corridor,
        so on a wider one it *created* orphans — and an unconnected leaf is a
        hard Logical error, so the spine built to rescue a flow-less IR failed
        it instead."""
        corridors = [
            Corridor(
                id=f"domain_{name}", label=name[0].upper(),
                leaves=[_leaf(f"{name[:2]}{i}", "logical_capability", f"Cap {i}") for i in range(8)],
            )
            for name in ("experience", "core", "data")
        ]
        ir = LogicalIR(corridors=corridors, flows=[])
        self.assertTrue(synthesize_spine_flows(ir))
        self.assertEqual(ir.semantic_errors(), [])
        touched = {f.source for f in ir.flows} | {f.target for f in ir.flows}
        self.assertEqual(touched, {lf.id for c in corridors for lf in c.leaves})
        # One edge per leaf — a chain, never the mesh the old cap warned about.
        self.assertEqual(len(ir.flows), 23)

    def test_synthesized_flows_never_reorder_the_drawn_diagram(self) -> None:
        """End-to-end: what reaches the page is the order the model declared.

        Three separate flow-driven heuristics read the graph before emission —
        leaf ranking in `_tile_order`, `_reorder_row_leaves` (which rewrites
        `corridor.leaves` in place, and is the one Logical's flat storyboard
        actually reads), and the `_nudge_primary_leaf_bands` row alignment.
        Each is a crossing reduction when the model described those calls and a
        scramble when this module invented them, so all three are skipped.
        """
        import xml.etree.ElementTree as ET

        from app.adapters.outbound.diagram.compiler.corridor import compile_corridor_ir

        corridors = [
            Corridor(
                id=f"domain_{name}", label=name[0].upper(),
                leaves=[_leaf(f"{name[:2]}{i}", "logical_capability", f"Cap {i}") for i in range(8)],
            )
            for name in ("experience", "core", "data")
        ]
        ir = LogicalIR(corridors=corridors, flows=[])
        self.assertTrue(synthesize_spine_flows(ir))
        self.assertTrue(ir.flows_are_synthesized())

        root = ET.fromstring(compile_corridor_ir(ir, page_name="Logical"))
        placed = {
            c.get("id"): (
                float(c.find("mxGeometry").get("y")), float(c.find("mxGeometry").get("x")),
            )
            for c in root.iter("mxCell")
            if c.find("mxGeometry") is not None and c.get("id", "").startswith(("ex", "co", "da"))
        }
        for prefix in ("ex", "co", "da"):
            ids = [f"{prefix}{i}" for i in range(8)]
            self.assertEqual(sorted(ids, key=lambda i: placed[i]), ids, prefix)

    def test_synthesized_chain_preserves_declaration_order(self) -> None:
        """The layout ranks leaves from their edges, so the shape of the
        invented spine decides the corridor's reading order. A chain makes rank
        equal declaration index, which is the only ordering signal a model that
        returned no flows actually gave — a fan from one anchor instead put
        every straggler a rank below it and scrambled the row."""
        from app.adapters.outbound.diagram.logical_layout import _tile_order

        leaves = [_leaf(f"c{i}", "logical_capability", f"Cap {i}") for i in range(8)]
        ir = LogicalIR(
            corridors=[Corridor(id="domain_core", label="C", leaves=leaves)], flows=[],
        )
        synthesize_spine_flows(ir)
        ids = [lf.id for lf in leaves]
        local = [(f.source, f.target) for f in ir.flows
                 if f.source in set(ids) and f.target in set(ids)]
        self.assertEqual(_tile_order(ids, local)[0], ids)
        # Identical to what a corridor carrying no edges at all would produce.
        self.assertEqual(_tile_order(ids, local), _tile_order(ids, []))

    def test_does_not_overwrite_existing_flows(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="E", leaves=[_leaf("a")]),
                Corridor(id="domain_core", label="C", leaves=[_leaf("b")]),
            ],
            flows=[Flow(source="a", target="b")],
        )
        self.assertFalse(synthesize_spine_flows(ir))
        self.assertEqual(len(ir.flows), 1)

    def test_network_spine_skips_cross_cutting_groups(self) -> None:
        ir = NetworkIR(
            cloud=CloudBoundary(
                id="cloud",
                label="AWS",
                regions=[
                    Region(
                        id="reg",
                        label="us-east-1",
                        vpcs=[
                            Vpc(
                                id="vpc",
                                label="VPC",
                                gateway=_leaf("alb", "aws_alb", "ALB"),
                                zones=[
                                    AvailabilityZone(
                                        id="az1",
                                        label="AZ1",
                                        subnets=[
                                            Subnet(
                                                id="app",
                                                tier="app",
                                                leaves=[_leaf("api", "aws_ecs", "API")],
                                            ),
                                            Subnet(
                                                id="data",
                                                tier="data",
                                                leaves=[_leaf("db", "aws_rds", "DB")],
                                            ),
                                        ],
                                    )
                                ],
                                groups=[
                                    CrossCuttingGroup(
                                        id="sec",
                                        kind="security",
                                        leaves=[_leaf("kms", "aws_kms", "KMS")],
                                    )
                                ],
                            )
                        ],
                    )
                ],
                edge_layer=EdgeLayer(id="edge", label="Edge", leaves=[_leaf("cdn", "aws_cloudfront", "CDN")]),
            ),
            external_actors=[ExternalActor(id="users", kind="users", label="Users")],
            flows=[],
        )
        self.assertTrue(synthesize_spine_flows(ir))
        touched = {f.source for f in ir.flows} | {f.target for f in ir.flows}
        self.assertNotIn("kms", touched)
        self.assertNotIn("sec", touched)
        self.assertIn("users", touched)
        self.assertEqual(ir.semantic_errors(), [])


class EmptyFlowsCorrectionTests(unittest.TestCase):
    def test_is_empty_flows_error_helper(self) -> None:
        self.assertTrue(_is_empty_flows_error(["flows is empty but there are 2+ leaves — add edges"]))
        self.assertFalse(_is_empty_flows_error(["flow 'x' -> itself is not meaningful"]))
        self.assertFalse(_is_empty_flows_error([]))

    def test_generate_ir_synthesizes_when_model_returns_empty_flows(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("gw", "aws_api_gateway", "GW")]),
                Corridor(id="zone_private", label="Private", leaves=[_leaf("api", "aws_ecs", "API")]),
            ],
            external_actors=[ExternalActor(id="users", kind="users", label="Users")],
            flows=[],
        )

        def fake_llm(*_args, **_kwargs):
            return ir.model_copy(deep=True)

        out = _generate_ir_with_corrections(
            ir_type=SecurityIR,
            system_prompt="sys",
            human_content="req",
            provider_key="claude",
            logger=None,
            attachment_documents=None,
            llm=fake_llm,
            view_label="Security",
        )
        self.assertGreaterEqual(len(out.flows), 2)
        self.assertEqual(out.semantic_errors(), [])


class SanitizeRawTests(unittest.TestCase):
    def test_zone_detect_relocated_from_anchored(self) -> None:
        raw = {
            "view": "security",
            "platform": "aws",
            "title": "t",
            "external_actors": [{"id": "users", "kind": "users", "label": "Users"}],
            "corridors": [
                {
                    "id": "zone_public",
                    "label": "Public",
                    "leaves": [{"id": "gw", "shape_type": "aws_api_gateway", "label": "GW"}],
                },
            ],
            "anchored": [
                {
                    "id": "zone_detect",
                    "label": "Detect",
                    "anchor_id": None,
                    "leaves": [{"id": "gd", "shape_type": "aws_guardduty", "label": "GuardDuty"}],
                }
            ],
            "flows": [{"source": "users", "target": "gw"}],
        }
        sanitized = SecurityIR.sanitize_raw(raw)
        self.assertIsInstance(sanitized, dict)
        assert isinstance(sanitized, dict)
        anchored_ids = [c.get("id") for c in sanitized.get("anchored") or []]
        row_ids = [c.get("id") for c in sanitized.get("corridors") or []]
        self.assertIn("zone_detect", row_ids)
        self.assertNotIn("zone_detect", anchored_ids)
        ir = SecurityIR.model_validate(sanitized)
        self.assertIn("zone_detect", {c.id for c in ir.corridors})

    def test_perimeter_service_relocated_from_external_actors(self) -> None:
        """Regression test for a live capture: the model emitted Route 53 as
        {"kind": "saas_ingress", "label": "Route 53"} in external_actors — it
        rendered with a generic person/user icon (external actors always get
        a plain actor shape, never an MCP-resolved service icon) instead of
        its real AWS icon. The identical service was correctly placed as a
        zone_public leaf in other captures of the same prompt, proving this
        was non-deterministic model behavior the prompt's own "never put a
        leaf in external_actors that isn't actually outside the trust
        boundary" rule didn't reliably prevent — hence the code-level fix."""
        raw = {
            "view": "security",
            "platform": "aws",
            "title": "t",
            "corridors": [
                {"id": "zone_private", "label": "Private", "leaves": [{"id": "app", "shape_type": "aws_ecs", "label": "App"}]},
            ],
            "anchored": [],
            "external_actors": [
                {"id": "users", "kind": "users", "label": "Users"},
                {"id": "route53", "kind": "saas_ingress", "label": "Route 53"},
            ],
            "flows": [
                {"source": "users", "target": "route53", "label": "DNS Lookup", "primary": True},
                {"source": "route53", "target": "app", "label": "HTTPS", "primary": True},
            ],
        }
        sanitized = SecurityIR.sanitize_raw(raw)
        actor_ids = [a.get("id") for a in sanitized.get("external_actors") or []]
        self.assertNotIn("route53", actor_ids)
        self.assertIn("users", actor_ids)  # a genuine actor must be left untouched

        ir = SecurityIR.model_validate(sanitized)
        self.assertEqual(ir.semantic_errors(), [])  # flows still resolve after relocation
        zone_public = ir.corridor_by_id("zone_public")
        self.assertIsNotNone(zone_public)
        route53_leaf = next(l for l in zone_public.leaves if l.id == "route53")
        self.assertEqual(route53_leaf.shape_type, "aws_route53")

    def test_perimeter_service_relocation_preserves_existing_zone_public_leaves(self) -> None:
        raw = {
            "view": "security", "platform": "aws", "title": "t",
            "corridors": [
                {"id": "zone_public", "label": "Public", "leaves": [{"id": "waf", "shape_type": "aws_waf", "label": "WAF"}]},
            ],
            "anchored": [],
            "external_actors": [{"id": "route53", "kind": "saas_ingress", "label": "Route 53"}],
            "flows": [{"source": "route53", "target": "waf", "label": "HTTPS", "primary": True}],
        }
        ir = SecurityIR.model_validate(SecurityIR.sanitize_raw(raw))
        zone_public = ir.corridor_by_id("zone_public")
        leaf_ids = {l.id for l in zone_public.leaves}
        self.assertEqual(leaf_ids, {"waf", "route53"})

    def test_actor_with_unrelated_label_is_not_touched(self) -> None:
        """A genuine external actor must never be relocated just because
        _relocate_perimeter_services_from_actors ran — only exact matches
        against the known perimeter-service label table qualify."""
        raw = {
            "view": "security", "platform": "aws", "title": "t",
            "corridors": [{"id": "zone_private", "label": "Private", "leaves": [{"id": "app", "shape_type": "aws_ecs", "label": "App"}]}],
            "anchored": [],
            "external_actors": [{"id": "partner", "kind": "saas_ingress", "label": "Acme Load Balancer Corp"}],
            "flows": [{"source": "partner", "target": "app"}],
        }
        sanitized = SecurityIR.sanitize_raw(raw)
        actor_ids = [a.get("id") for a in sanitized.get("external_actors") or []]
        self.assertIn("partner", actor_ids)


class FlowLabelLimitTests(unittest.TestCase):
    def test_six_word_labels_kept(self) -> None:
        f = Flow(source="a", target="b", label="Cache Miss: Fetches via API")
        self.assertEqual(f.label, "Cache Miss: Fetches via API")
        f2 = Flow(source="a", target="b", label="JSON: Failed API Retries extra word")
        self.assertEqual(f2.label, "JSON: Failed API Retries extra word")
        f3 = Flow(source="a", target="b", label="one two three four five six seven")
        self.assertEqual(f3.label, "one two three four five six")


if __name__ == "__main__":
    unittest.main()
