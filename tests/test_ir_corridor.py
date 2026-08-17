"""CorridorIR validation — shared base plus Logical/Security/Data-flow vocabulary."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.domain.ir import (
    AnchoredCorridor,
    Corridor,
    DataFlowIR,
    ExternalActor,
    Flow,
    Leaf,
    LogicalIR,
    SecurityIR,
)


def _leaf(i: str, st: str = "aws_ecs", label: str = "Svc") -> Leaf:
    return Leaf(id=i, shape_type=st, label=label)


class SharedCorridorTests(unittest.TestCase):
    """Rules that must hold across all three views identically (base.py)."""

    def test_empty_corridor_is_impossible(self) -> None:
        with self.assertRaises(ValidationError):
            Corridor(id="domain_core", label="Core", leaves=[])

    def test_duplicate_ids_across_corridors_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[
                    Corridor(id="domain_experience", label="Experience", leaves=[_leaf("dup")]),
                    Corridor(id="domain_core", label="Core", leaves=[_leaf("dup")]),
                ],
            )

    def test_shelf_anchor_must_reference_a_row_corridor(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[Corridor(id="domain_experience", label="E", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="domain_saas", label="SaaS", anchor_id="domain_data", leaves=[_leaf("b")])],
            )

    def test_whole_row_shelf_anchor_id_none_is_allowed(self) -> None:
        ir = DataFlowIR(
            corridors=[Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("a")])],
            anchored=[AnchoredCorridor(id="phase_control", label="Control", anchor_id=None, leaves=[_leaf("b")])],
            flows=[Flow(source="a", target="b")],
        )
        self.assertEqual(ir.semantic_errors(), [])

    def test_flow_to_unknown_id_is_an_error(self) -> None:
        ir = LogicalIR(corridors=[Corridor(id="domain_experience", label="E", leaves=[_leaf("a")])])
        ir.flows = [Flow(source="a", target="ghost")]
        self.assertTrue(any("not a known cell id" in e for e in ir.semantic_errors()))

    def test_self_loop_is_an_error(self) -> None:
        ir = LogicalIR(corridors=[Corridor(id="domain_experience", label="E", leaves=[_leaf("a")])])
        ir.flows = [Flow(source="a", target="a")]
        self.assertTrue(any("itself" in e for e in ir.semantic_errors()))

    def test_extra_fields_on_leaf_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Leaf(id="x", shape_type="aws_ecs", label="X", x=10)

    def test_external_actor_shared_type_importable_from_every_module(self) -> None:
        """ExternalActor moved to base.py — every view's package import must still work."""
        from app.domain.ir.base import ExternalActor as BaseActor
        from app.domain.ir.network import ExternalActor as NetworkActor
        self.assertIs(BaseActor, NetworkActor)
        self.assertIs(BaseActor, ExternalActor)


class LogicalVocabularyTests(unittest.TestCase):
    def test_valid_domain_ids_accepted(self) -> None:
        ir = LogicalIR(
            corridors=[
                Corridor(id="domain_experience", label="Experience", leaves=[_leaf("portal")]),
                Corridor(id="domain_data", label="Data", leaves=[_leaf("store")]),
            ],
            anchored=[AnchoredCorridor(id="domain_saas", label="SaaS", anchor_id="domain_data", leaves=[_leaf("wd")])],
            flows=[Flow(source="portal", target="store"), Flow(source="store", target="wd")],
        )
        self.assertEqual(ir.semantic_errors(), [])

    def test_invented_domain_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(corridors=[Corridor(id="domain_bogus", label="Bogus", leaves=[_leaf("a")])])

    def test_non_domain_prefix_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(corridors=[Corridor(id="zone_public", label="Public", leaves=[_leaf("a")])])

    def test_shelf_id_must_be_domain_saas(self) -> None:
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[Corridor(id="domain_data", label="Data", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="domain_wrong", label="Wrong", anchor_id="domain_data", leaves=[_leaf("b")])],
            )


class ShelfAnchorFallbackTests(unittest.TestCase):
    """A shelf must stay constructible when its preferred anchor isn't there.

    The preferred corridor (domain_data / zone_restricted / phase_store) is
    optional, and the two halves of the old check disagreed about that:
    `_relocate_vocabulary` only forces the anchor when the corridor is present,
    while the after-validator demanded it unconditionally. Any view built
    without that corridor but with a shelf therefore had no legal anchor at
    all — and because it fails in a Pydantic validator, the object never builds,
    so semantic_errors() and its corrective pass never run and the request
    404s... 400s. Measured live on "show me the message flow when an order is
    placed", which returned HTTP 400 with no diagram.
    """

    def test_raw_output_with_no_data_domain_is_relocated_below_the_row(self) -> None:
        """The exact live failure, as the model actually emits it (raw dicts,
        so the pre-parse relocation runs). Previously HTTP 400."""
        ir = LogicalIR.model_validate({
            "view": "logical", "platform": "aws", "title": "Order Processing",
            "corridors": [
                {"id": "domain_experience", "label": "Experience",
                 "leaves": [{"id": "web", "shape_type": "logical_portal", "label": "Web App"}]},
                {"id": "domain_commerce", "label": "Commerce",
                 "leaves": [{"id": "orders", "shape_type": "aws_lambda", "label": "Order Service"}]},
            ],
            "anchored": [{"id": "domain_saas", "label": "External SaaS", "anchor_id": "domain_commerce",
                          "leaves": [{"id": "pay", "shape_type": "saas_stripe", "label": "Payment Gateway"}]}],
            "external_actors": [{"id": "customer", "kind": "users", "label": "Customer"}],
            "flows": [
                {"source": "customer", "target": "web"},
                {"source": "web", "target": "orders"},
                {"source": "orders", "target": "pay"},
            ],
        })
        self.assertIsNone(ir.anchored[0].anchor_id, "shelf hangs below the whole row")
        self.assertEqual(ir.semantic_errors(), [])

    def test_shelf_under_an_unrelated_corridor_is_still_invalid(self) -> None:
        """The semantic rule is untouched — only the dead end is gone. Model
        instances bypass the relocation, so this reaches the validator."""
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[Corridor(id="domain_commerce", label="Commerce", leaves=[_leaf("orders")])],
                anchored=[AnchoredCorridor(
                    id="domain_saas", label="SaaS", anchor_id="domain_commerce", leaves=[_leaf("pay")],
                )],
            )

    def test_logical_shelf_still_pinned_when_data_domain_present(self) -> None:
        # Model instances bypass _relocate_vocabulary (it only rewrites raw
        # dicts), so this reaches the validator with the preference intact.
        with self.assertRaises(ValidationError):
            LogicalIR(
                corridors=[
                    Corridor(id="domain_commerce", label="Commerce", leaves=[_leaf("orders")]),
                    Corridor(id="domain_data", label="Data", leaves=[_leaf("store")]),
                ],
                anchored=[AnchoredCorridor(
                    id="domain_saas", label="SaaS", anchor_id="domain_commerce", leaves=[_leaf("pay")],
                )],
            )

    def test_unanchored_shelf_is_accepted_when_preferred_corridor_absent(self) -> None:
        ir = LogicalIR(
            corridors=[Corridor(id="domain_commerce", label="Commerce", leaves=[_leaf("orders")])],
            anchored=[AnchoredCorridor(
                id="domain_saas", label="SaaS", anchor_id=None, leaves=[_leaf("pay")],
            )],
            flows=[Flow(source="orders", target="pay")],
        )
        self.assertIsNone(ir.anchored[0].anchor_id)

    def test_security_and_data_flow_relocate_the_same_way(self) -> None:
        sec = SecurityIR.model_validate({
            "corridors": [{"id": "zone_public", "label": "Public",
                           "leaves": [{"id": "edge", "shape_type": "aws_alb", "label": "Edge"}]}],
            "anchored": [{"id": "zone_external_saas", "label": "SaaS", "anchor_id": "zone_public",
                          "leaves": [{"id": "idp", "shape_type": "saas_okta", "label": "IdP"}]}],
            "flows": [{"source": "edge", "target": "idp"}],
        })
        self.assertIsNone(sec.anchored[0].anchor_id)

        flow = DataFlowIR.model_validate({
            "corridors": [{"id": "phase_ingest", "label": "Ingest",
                           "leaves": [{"id": "api", "shape_type": "aws_api_gateway", "label": "API"}]}],
            "anchored": [{"id": "phase_consume", "label": "Consume", "anchor_id": "phase_ingest",
                          "leaves": [{"id": "dw", "shape_type": "aws_redshift", "label": "Warehouse"}]}],
            "flows": [{"source": "api", "target": "dw"}],
        })
        self.assertIsNone(flow.anchored[0].anchor_id)


class SecurityVocabularyTests(unittest.TestCase):
    def test_valid_zone_ids_accepted(self) -> None:
        ir = SecurityIR(
            corridors=[
                Corridor(id="zone_public", label="Public", leaves=[_leaf("dns")]),
                Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("db")]),
            ],
            anchored=[AnchoredCorridor(id="zone_external_saas", label="SaaS", anchor_id="zone_restricted", leaves=[_leaf("wd")])],
            flows=[Flow(source="dns", target="db")],
        )
        self.assertEqual(ir.semantic_errors(), [])

    def test_invented_zone_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            SecurityIR(corridors=[Corridor(id="zone_bogus", label="Bogus", leaves=[_leaf("a")])])

    def test_partner_zone_is_a_valid_row_corridor(self) -> None:
        ir = SecurityIR(corridors=[Corridor(id="zone_partner", label="Partner", leaves=[_leaf("gw")])])
        self.assertEqual(ir.semantic_errors(), [])

    def test_external_saas_shelf_must_anchor_under_restricted(self) -> None:
        with self.assertRaises(ValidationError):
            SecurityIR(
                corridors=[
                    Corridor(id="zone_public", label="Public", leaves=[_leaf("a")]),
                    Corridor(id="zone_restricted", label="Restricted", leaves=[_leaf("b")]),
                ],
                anchored=[AnchoredCorridor(id="zone_external_saas", label="SaaS", anchor_id="zone_public", leaves=[_leaf("c")])],
            )


class DataFlowVocabularyTests(unittest.TestCase):
    def test_valid_phase_ids_accepted(self) -> None:
        ir = DataFlowIR(
            corridors=[
                Corridor(id="phase_ingest", label="Ingest", leaves=[_leaf("apigw")]),
                Corridor(id="phase_store", label="Store", leaves=[_leaf("db")]),
            ],
            anchored=[
                AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_store", leaves=[_leaf("dash")]),
                AnchoredCorridor(id="phase_saas_producer", label="SaaS Producers", anchor_id="phase_store", leaves=[_leaf("sf")]),
                AnchoredCorridor(id="phase_saas_consumer", label="SaaS Consumers", anchor_id="phase_store", leaves=[_leaf("stripe")]),
                AnchoredCorridor(id="phase_control", label="Control", anchor_id=None, leaves=[_leaf("glue")]),
            ],
            flows=[Flow(source="apigw", target="db")],
        )
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual(len(ir.anchored), 4)

    def test_old_undifferentiated_saas_id_rejected(self) -> None:
        """The pre-split bare 'phase_saas' id is no longer valid vocabulary."""
        with self.assertRaises(ValidationError):
            DataFlowIR(
                corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("db")])],
                anchored=[AnchoredCorridor(id="phase_saas", label="SaaS", anchor_id="phase_store", leaves=[_leaf("sf")])],
            )

    def test_invented_phase_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(corridors=[Corridor(id="phase_bogus", label="Bogus", leaves=[_leaf("a")])])

    def test_double_shelf_both_anchor_under_store(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(
                corridors=[Corridor(id="phase_process", label="Process", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="phase_consume", label="Consume", anchor_id="phase_process", leaves=[_leaf("b")])],
            )

    def test_control_shelf_must_have_no_anchor(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(
                corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="phase_control", label="Control", anchor_id="phase_store", leaves=[_leaf("b")])],
            )

    def test_unknown_anchored_id_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DataFlowIR(
                corridors=[Corridor(id="phase_store", label="Store", leaves=[_leaf("a")])],
                anchored=[AnchoredCorridor(id="phase_bogus_shelf", label="Bogus", anchor_id=None, leaves=[_leaf("b")])],
            )


class ExternalActorReuseTests(unittest.TestCase):
    def test_actor_kinds_usable_across_every_corridor_view(self) -> None:
        for ir_cls, prefix in ((LogicalIR, "domain_experience"), (SecurityIR, "zone_public"), (DataFlowIR, "phase_ingest")):
            ir = ir_cls(
                corridors=[Corridor(id=prefix, label="X", leaves=[_leaf("a")])],
                external_actors=[
                    ExternalActor(id="u", kind="users", label="Users"),
                    ExternalActor(id="idp", kind="saas_ingress", label="Okta"),
                ],
            )
            self.assertEqual(len(ir.external_actors), 2)


class InfraStripBridgingTests(unittest.TestCase):
    """Stripping cross-cutting infra must never leave a dangling flow behind.

    Both strippers run pre-parse, so these go through ``model_validate`` on raw
    dicts — the same entry point the LLM's output takes.

    The corpus numbers in each test are from replaying ``data/captures``: this
    was not a theoretical edge case but the single largest source of IR
    rejection in the app's own history, and every rejection cost the user a
    Logical or Data-flow tab.
    """

    def _raw_logical(self, flows: list[dict], **extra) -> dict:
        return {
            "view": "logical",
            "corridors": [
                {"id": "domain_experience", "label": "Experience", "leaves": [
                    {"id": "cloudfront", "shape_type": "aws_cloudfront", "label": "CloudFront"},
                    {"id": "waf", "shape_type": "aws_waf", "label": "WAF"},
                ]},
                {"id": "domain_core", "label": "Core", "leaves": [
                    {"id": "apigw", "shape_type": "aws_api_gateway", "label": "API Gateway"},
                ]},
            ],
            "external_actors": [{"id": "users", "kind": "users", "label": "Users"}],
            "flows": flows,
            **extra,
        }

    def test_chain_of_two_removed_leaves_bridges_end_to_end(self) -> None:
        """``users -> cloudfront -> waf -> apigw``: both hops are denylisted.

        Pairwise bridging emitted ``cloudfront -> apigw`` here — a flow whose
        source had just been deleted — which failed validation outright. 121 of
        142 captured sessions hit this exact chain.
        """
        ir = LogicalIR.model_validate(self._raw_logical([
            {"source": "users", "target": "cloudfront", "label": "HTTPS"},
            {"source": "cloudfront", "target": "waf", "label": "Filters"},
            {"source": "waf", "target": "apigw", "label": "Forwards Request"},
        ]))
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual([(f.source, f.target) for f in ir.flows], [("users", "apigw")])
        self.assertEqual(ir.flows[0].label, "Forwards Request")

    def test_single_removed_hop_still_bridges(self) -> None:
        ir = LogicalIR.model_validate(self._raw_logical([
            {"source": "users", "target": "cloudfront", "label": "HTTPS"},
            {"source": "cloudfront", "target": "apigw", "label": "Forwards"},
        ]))
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual([(f.source, f.target) for f in ir.flows], [("users", "apigw")])

    def test_cycle_among_removed_cells_terminates(self) -> None:
        """A -> B -> A between two stripped cells must not loop forever."""
        ir = LogicalIR.model_validate(self._raw_logical([
            {"source": "users", "target": "cloudfront", "label": "HTTPS"},
            {"source": "cloudfront", "target": "waf", "label": "Filters"},
            {"source": "waf", "target": "cloudfront", "label": "Returns"},
            {"source": "waf", "target": "apigw", "label": "Forwards"},
        ]))
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual([(f.source, f.target) for f in ir.flows], [("users", "apigw")])

    def test_stripped_actor_does_not_leave_a_dangling_flow(self) -> None:
        """Route 53 filed as a ``saas_ingress`` actor rather than a leaf.

        The actor stripper removed the cell but left its flows untouched — 13
        captured sessions.
        """
        raw = self._raw_logical(
            [
                {"source": "users", "target": "route53", "label": "DNS"},
                {"source": "route53", "target": "apigw", "label": "Resolves"},
            ],
        )
        raw["external_actors"].append({"id": "route53", "kind": "saas_ingress", "label": "Route 53"})
        ir = LogicalIR.model_validate(raw)
        self.assertEqual(ir.semantic_errors(), [])
        self.assertNotIn("route53", {a.id for a in ir.external_actors})
        self.assertEqual([(f.source, f.target) for f in ir.flows], [("users", "apigw")])

    def test_flows_between_surviving_cells_are_untouched(self) -> None:
        ir = LogicalIR.model_validate(self._raw_logical([
            {"source": "users", "target": "apigw", "label": "HTTPS"},
        ]))
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual([(f.source, f.target) for f in ir.flows], [("users", "apigw")])

    def test_bridge_keeps_entry_edge_attributes(self) -> None:
        ir = LogicalIR.model_validate(self._raw_logical([
            {"source": "users", "target": "cloudfront", "label": "HTTPS",
             "classification": "pii", "protection": "tls", "primary": True},
            {"source": "cloudfront", "target": "apigw", "label": "Forwards",
             "classification": "none", "protection": "none", "primary": False},
        ]))
        self.assertEqual(ir.flows[0].classification, "pii")
        self.assertEqual(ir.flows[0].protection, "tls")

    def test_data_flow_view_bridges_the_same_way(self) -> None:
        ir = DataFlowIR.model_validate({
            "view": "data_flow",
            "corridors": [
                {"id": "phase_ingest", "label": "Ingest", "leaves": [
                    {"id": "cloudfront", "shape_type": "aws_cloudfront", "label": "CDN"},
                    {"id": "alb", "shape_type": "aws_application_load_balancer", "label": "ALB"},
                ]},
                {"id": "phase_process", "label": "Process", "leaves": [
                    {"id": "app_server", "shape_type": "aws_ec2", "label": "App Server"},
                ]},
            ],
            "external_actors": [{"id": "users", "kind": "users", "label": "Users"}],
            "flows": [
                {"source": "users", "target": "cloudfront", "label": "HTTPS"},
                {"source": "cloudfront", "target": "alb", "label": "Routes"},
                {"source": "alb", "target": "app_server", "label": "HTTPS: App Request"},
            ],
        })
        self.assertEqual(ir.semantic_errors(), [])
        self.assertEqual([(f.source, f.target) for f in ir.flows], [("users", "app_server")])


if __name__ == "__main__":
    unittest.main()
