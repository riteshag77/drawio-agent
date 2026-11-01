"""NetworkIR validation — the guardrails that replace prompt-prose rules."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.domain.ir import (
    AvailabilityZone,
    CloudBoundary,
    CrossCuttingGroup,
    EdgeLayer,
    ExternalActor,
    Flow,
    Leaf,
    NetworkIR,
    Region,
    Subnet,
    Vpc,
)


def _leaf(i: str, st: str = "aws_ecs", label: str = "Svc") -> Leaf:
    return Leaf(id=i, shape_type=st, label=label)


def minimal_ir(**overrides) -> NetworkIR:
    """Smallest IR that is structurally complete."""
    zone = AvailabilityZone(
        id="az1",
        label="AZ-1a",
        subnets=[Subnet(id="app1", tier="app", leaves=[_leaf("ecs1")])],
    )
    vpc = Vpc(id="vpc", zones=[zone], **overrides.pop("vpc_kwargs", {}))
    cloud = CloudBoundary(
        id="cloud",
        label="AWS Cloud",
        regions=[Region(id="region", label="us-east-1", vpcs=[vpc])],
        **overrides.pop("cloud_kwargs", {}),
    )
    return NetworkIR(cloud=cloud, platform="aws", **overrides)


class LeafValidationTests(unittest.TestCase):
    def test_shape_type_rejects_drawio_style_strings(self) -> None:
        """The 'LLM must not invent stencils' rule is structural, not prose."""
        for bad in (
            "resIcon=mxgraph.aws4.resourceIcon",
            "shape=mxgraph.gcp2.database",
            "mxgraph.azure2.compute",
            "rounded=0;fillColor=#fff;",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Leaf(id="x", shape_type=bad, label="X")

    def test_shape_type_accepts_semantic_keys(self) -> None:
        for good in ("aws_rds", "gcp_cloud_run", "azure_functions"):
            self.assertEqual(Leaf(id="x", shape_type=good, label="X").shape_type, good)

    def test_reserved_and_malformed_ids_rejected(self) -> None:
        for bad in ("0", "1", "9leading", "has-dash", "has space", ""):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                Leaf(id=bad, shape_type="aws_ecs", label="X")

    def test_extra_fields_are_rejected_loudly(self) -> None:
        """A model inventing `x`/`style` must fail, not have it silently dropped."""
        with self.assertRaises(ValidationError):
            Leaf(id="x", shape_type="aws_ecs", label="X", style="rounded=0;")


class StructuralTests(unittest.TestCase):
    def test_empty_subnet_is_impossible(self) -> None:
        with self.assertRaises(ValidationError):
            Subnet(id="s", tier="app", leaves=[])

    def test_empty_zone_is_impossible(self) -> None:
        with self.assertRaises(ValidationError):
            AvailabilityZone(id="z", label="Z", subnets=[])

    def test_subnets_are_sorted_into_canonical_tier_order(self) -> None:
        zone = AvailabilityZone(
            id="z", label="Z",
            subnets=[
                Subnet(id="d", tier="data", leaves=[_leaf("l1")]),
                Subnet(id="p", tier="public", leaves=[_leaf("l2")]),
                Subnet(id="a", tier="app", leaves=[_leaf("l3")]),
            ],
        )
        self.assertEqual([s.tier for s in zone.subnets], ["public", "app", "data"])

    def test_duplicate_tier_in_one_zone_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AvailabilityZone(
                id="z", label="Z",
                subnets=[
                    Subnet(id="a1", tier="app", leaves=[_leaf("l1")]),
                    Subnet(id="a2", tier="app", leaves=[_leaf("l2")]),
                ],
            )

    def test_duplicate_group_kind_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Vpc(
                id="v",
                zones=[AvailabilityZone(id="z", label="Z", subnets=[Subnet(id="s", tier="app", leaves=[_leaf("l")])])],
                groups=[
                    CrossCuttingGroup(id="g1", kind="security", leaves=[_leaf("a")]),
                    CrossCuttingGroup(id="g2", kind="security", leaves=[_leaf("b")]),
                ],
            )

    def test_duplicate_ids_across_the_document_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            NetworkIR(
                cloud=CloudBoundary(
                    id="cloud", label="AWS",
                    regions=[Region(id="region", label="r", vpcs=[Vpc(
                        id="vpc",
                        zones=[AvailabilityZone(id="dup", label="Z", subnets=[
                            Subnet(id="dup", tier="app", leaves=[_leaf("l")]),
                        ])],
                    )])],
                ),
            )


class SemanticRuleTests(unittest.TestCase):
    def _ir_with_security_group(self) -> NetworkIR:
        zone = AvailabilityZone(
            id="az1", label="AZ-1a",
            subnets=[Subnet(id="app1", tier="app", leaves=[_leaf("ecs1")])],
        )
        vpc = Vpc(
            id="vpc", zones=[zone],
            groups=[CrossCuttingGroup(id="sec", kind="security", leaves=[_leaf("iam", "aws_iam", "IAM")])],
        )
        return NetworkIR(
            cloud=CloudBoundary(id="cloud", label="AWS", regions=[Region(id="r", label="us-east-1", vpcs=[vpc])]),
        )

    def test_flow_touching_cross_cutting_group_is_an_error(self) -> None:
        """network.txt's 'ABSOLUTE RULE — NO LINES', now checkable."""
        ir = self._ir_with_security_group()
        ir.flows = [Flow(source="ecs1", target="iam")]
        errors = ir.semantic_errors()
        self.assertTrue(errors)
        self.assertIn("cross-cutting", errors[0])
        self.assertFalse(ir.is_valid())

    def test_flow_to_group_container_itself_is_an_error(self) -> None:
        ir = self._ir_with_security_group()
        ir.flows = [Flow(source="ecs1", target="sec")]
        self.assertTrue(any("cross-cutting" in e for e in ir.semantic_errors()))

    def test_flow_to_unknown_id_is_an_error(self) -> None:
        ir = minimal_ir()
        ir.flows = [Flow(source="ecs1", target="ghost")]
        self.assertTrue(any("not a known cell id" in e for e in ir.semantic_errors()))

    def test_self_loop_is_an_error(self) -> None:
        ir = minimal_ir()
        ir.flows = [Flow(source="ecs1", target="ecs1")]
        self.assertTrue(any("itself" in e for e in ir.semantic_errors()))

    def test_valid_ir_reports_no_errors(self) -> None:
        ir = self._ir_with_security_group()
        ir.external_actors = [ExternalActor(id="users", kind="users", label="Users")]
        ir.flows = [Flow(source="users", target="ecs1", label="HTTPS")]
        self.assertEqual(ir.semantic_errors(), [])
        self.assertTrue(ir.is_valid())
        # Missing public/edge/gateway is a warning (corrective pass), not a hard error.
        self.assertTrue(any("ingress" in w for w in ir.semantic_warnings()))

    def _ir_with_four_leaves_two_tiers(self, **overrides) -> NetworkIR:
        leaves = [_leaf(f"svc{i}") for i in range(3)]
        zone = AvailabilityZone(
            id="az1", label="AZ",
            subnets=[
                Subnet(id="pub", tier="public", leaves=[_leaf("alb", "aws_alb", "ALB")]),
                Subnet(id="app1", tier="app", leaves=leaves),
            ],
        )
        return NetworkIR(
            cloud=CloudBoundary(
                id="cloud", label="AWS",
                regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=[zone])])],
            ),
            platform="aws",
            **overrides,
        )

    def test_empty_design_tradeoffs_warns_on_a_real_sized_network(self) -> None:
        """design_tradeoffs is optional (has a default) so it's never in the
        schema's required set — same lesson already learned for business_notes:
        a nudge via the one-shot corrective pass is the only way to actually
        get it filled in on non-trivial diagrams."""
        ir = self._ir_with_four_leaves_two_tiers()
        self.assertTrue(any("design_tradeoffs" in w for w in ir.semantic_warnings()))

    def test_populated_design_tradeoffs_does_not_warn(self) -> None:
        ir = self._ir_with_four_leaves_two_tiers(
            design_tradeoffs=["ALB over a plain load balancer: needed content-based routing."],
        )
        self.assertFalse(any("design_tradeoffs" in w for w in ir.semantic_warnings()))

    def test_tiny_network_does_not_warn_about_empty_design_tradeoffs(self) -> None:
        """Below the leaf_n>=4 thoroughness threshold — a 1-service fixture
        genuinely may not have a real tradeoff to name."""
        ir = minimal_ir()
        self.assertFalse(any("design_tradeoffs" in w for w in ir.semantic_warnings()))

    def test_partial_orphan_actor_is_an_error(self) -> None:
        ir = self._ir_with_security_group()
        ir.external_actors = [
            ExternalActor(id="users", kind="users", label="Users"),
            ExternalActor(id="partner", kind="saas_egress", label="Partner API"),
        ]
        ir.flows = [Flow(source="users", target="ecs1")]  # "partner" never touches a flow
        errors = ir.semantic_errors()
        self.assertTrue(any("partner" in e and "no flow" in e for e in errors))

    def test_all_actors_connected_reports_no_orphan_error(self) -> None:
        ir = self._ir_with_security_group()
        ir.external_actors = [ExternalActor(id="users", kind="users", label="Users")]
        ir.flows = [Flow(source="users", target="ecs1")]
        errors = ir.semantic_errors()
        self.assertFalse(any("no flow" in e for e in errors))

    def test_four_plus_leaves_want_two_subnet_tiers_but_are_not_rejected(self) -> None:
        """A flat network is a design criticism, not an unrenderable diagram.

        It was a hard error, so the page was thrown away rather than argued
        with — three captures lost their Network tab to this and the two rules
        beside it. As a warning it still earns one corrective ask, which is the
        right weight for a judgment about someone else's architecture.
        """
        leaves = [_leaf(f"svc{i}") for i in range(4)]
        zone = AvailabilityZone(
            id="az1", label="AZ",
            subnets=[Subnet(id="app1", tier="app", leaves=leaves)],
        )
        ir = NetworkIR(
            cloud=CloudBoundary(
                id="cloud", label="AWS",
                regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=[zone])])],
            ),
            platform="aws",
        )
        self.assertFalse(any("only one subnet tier" in e for e in ir.semantic_errors()),
                         "a flat network must still compile")
        self.assertTrue(any("only one subnet tier" in w for w in ir.semantic_warnings()))

        zone2 = AvailabilityZone(
            id="az1", label="AZ",
            subnets=[
                Subnet(id="pub", tier="public", leaves=[_leaf("alb", "aws_alb", "ALB")]),
                Subnet(id="app1", tier="app", leaves=leaves[:3]),
            ],
        )
        ir2 = NetworkIR(
            cloud=CloudBoundary(
                id="cloud", label="AWS",
                regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=[zone2])])],
            ),
            platform="aws",
        )
        self.assertFalse(any("only one subnet tier" in w for w in ir2.semantic_warnings()),
                         "two tiers must not be criticised for spanning one")

    def test_four_plus_leaves_with_zero_flows_and_no_actors_is_an_error(self) -> None:
        """Regression: a live capture had a schema-valid NetworkIR — 14 leaves,
        0 external_actors, 0 flows — compile straight to a Network page with
        43 cells and not one arrow. The external_actors-gated empty-flows rule
        never fires when there are no actors at all, so a network of real
        service leaves with zero connectivity sailed through undetected."""
        leaves = [_leaf(f"svc{i}") for i in range(4)]
        zone = AvailabilityZone(
            id="az1", label="AZ",
            subnets=[
                Subnet(id="pub", tier="public", leaves=[_leaf("alb", "aws_alb", "ALB")]),
                Subnet(id="app1", tier="app", leaves=leaves),
            ],
        )
        ir = NetworkIR(
            cloud=CloudBoundary(
                id="cloud", label="AWS",
                regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=[zone])])],
            ),
            platform="aws",
        )
        self.assertEqual(ir.external_actors, [])
        self.assertEqual(ir.flows, [])
        errors = ir.semantic_errors()
        self.assertTrue(any("zero flows" in e for e in errors))

    def test_long_edge_labels_are_truncated_not_rejected(self) -> None:
        """One chatty label must never fail a whole build (cap is 6 words)."""
        flow = Flow(source="a", target="b", label="sends an encrypted payload over the wire")
        self.assertEqual(flow.label, "sends an encrypted payload over the")


class IdCollectionTests(unittest.TestCase):
    def test_iter_leaves_covers_every_nesting_site(self) -> None:
        vpc = Vpc(
            id="vpc",
            zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                Subnet(id="s", tier="app", leaves=[_leaf("in_subnet")]),
            ])],
            gateway=_leaf("gw", "aws_alb", "ALB"),
            groups=[CrossCuttingGroup(id="sec", kind="security", leaves=[_leaf("in_group")])],
        )
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS",
            regions=[Region(id="r", label="r", vpcs=[vpc])],
            edge_layer=EdgeLayer(id="edge", leaves=[_leaf("in_edge")]),
            connectivity_hubs=[_leaf("tgw", "aws_transit_gateway", "TGW")],
        ))
        self.assertEqual(
            ir.leaf_ids(),
            {"in_subnet", "gw", "in_group", "in_edge", "tgw"},
        )

    def test_no_edge_ids_includes_container_and_members(self) -> None:
        vpc = Vpc(
            id="vpc",
            zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                Subnet(id="s", tier="app", leaves=[_leaf("app")]),
            ])],
            groups=[CrossCuttingGroup(id="mon", kind="monitoring", leaves=[_leaf("cw")])],
        )
        ir = NetworkIR(cloud=CloudBoundary(id="c", label="AWS", regions=[Region(id="r", label="r", vpcs=[vpc])]))
        self.assertEqual(ir.no_edge_ids(), {"mon", "cw"})


if __name__ == "__main__":
    unittest.main()
