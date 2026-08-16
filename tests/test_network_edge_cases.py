"""Network view battle-test coverage — the fixes from the Network correctness
audit (peer-zone equalization, edge_layer ordering, ElkLayoutError wrapping,
the new actor/leaf safety net, SaaS icon threading, the two new semantic
warnings, and density/scale shapes no prior fixture reached).
"""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.adapters.outbound.diagram import geometry_spec as geo
from app.adapters.outbound.diagram.compiler import compile_network_ir
from app.adapters.outbound.diagram.compiler.network import CompilerError
from app.adapters.outbound.diagram.compiler.elk_layout_network import layout_network_ir
from app.adapters.outbound.diagram.elk_bridge import ElkLayoutError
from app.adapters.outbound.diagram.view_conformance import score_view
from app.adapters.outbound.diagram.xml_diagnostics import diagnose_xml
from app.adapters.outbound.llm.prompts.ir import get_network_ir_system_prompt
from app.domain.ir import (
    AvailabilityZone,
    CloudBoundary,
    EdgeLayer,
    ExternalActor,
    Flow,
    Leaf,
    NetworkIR,
    Region,
    Subnet,
    Vpc,
)


def _leaf(i: str, st: str = "aws_ecs", label: str = "Svc", **kwargs) -> Leaf:
    return Leaf(id=i, shape_type=st, label=label, **kwargs)


def _cells(xml: str) -> dict[str, ET.Element]:
    return {c.get("id"): c for c in ET.fromstring(xml).iter("mxCell") if c.get("id")}


def _geom(cell: ET.Element) -> tuple[float, float, float, float]:
    g = cell.find("mxGeometry")
    return (
        float(g.get("x") or 0), float(g.get("y") or 0),
        float(g.get("width") or 0), float(g.get("height") or 0),
    )


class PeerZoneEqualizationTests(unittest.TestCase):
    """1.1 — the headline fix: peer zones must render as mirrors."""

    def _asymmetric_vpc(self) -> Vpc:
        zone_a = AvailabilityZone(
            id="az_a", label="AZ-A",
            subnets=[
                Subnet(id="pub_a", tier="public", leaves=[_leaf("nat_a", "aws_nat_gateway", "NAT")]),
                Subnet(id="app_a", tier="app", leaves=[_leaf("app_a1"), _leaf("app_a2")]),
                Subnet(id="data_a", tier="data", leaves=[_leaf("db_a", "aws_rds", "DB")]),
            ],
        )
        zone_b = AvailabilityZone(
            id="az_b", label="AZ-B",
            subnets=[Subnet(id="app_b", tier="app", leaves=[_leaf("app_b1")])],
        )
        return Vpc(id="vpc", zones=[zone_a, zone_b])

    def _ir(self) -> NetworkIR:
        return NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[self._asymmetric_vpc()])],
        ), external_actors=[ExternalActor(id="user", kind="users", label="User")], flows=[
            Flow(source="user", target="nat_a", label="HTTPS", primary=True),
            Flow(source="nat_a", target="app_a1", label="Forwards", primary=True),
            Flow(source="app_a1", target="db_a", label="SQL", primary=True),
        ])

    def test_asymmetric_peer_zones_get_equal_total_height(self) -> None:
        geos = layout_network_ir(self._ir())
        self.assertEqual(geos["az_a"].h, geos["az_b"].h)

    def test_shared_tier_lands_on_the_same_row_across_peers(self) -> None:
        """The 'app' tier is the only tier both zones have — it must land at
        the same relative y-offset within each zone's own box, not just make
        the zones the same total height by accident."""
        geos = layout_network_ir(self._ir())
        app_a_rel_y = geos["app_a"].y
        app_b_rel_y = geos["app_b"].y
        self.assertEqual(app_a_rel_y, app_b_rel_y)

    def test_every_subnet_in_the_vpc_shares_one_common_width(self) -> None:
        geos = layout_network_ir(self._ir())
        widths = {geos[sid].w for sid in ("pub_a", "app_a", "data_a", "app_b")}
        self.assertEqual(len(widths), 1, f"subnet widths are not uniform: {widths}")

    def test_spacer_nodes_never_leak_into_compiled_xml(self) -> None:
        xml = compile_network_ir(self._ir())
        cells = _cells(xml)
        for cid in cells:
            self.assertFalse(cid.startswith("az_b__spacer_"), f"spacer leaked into output: {cid}")

    def test_asymmetric_vpc_still_has_no_diagnostics(self) -> None:
        diag = diagnose_xml(compile_network_ir(self._ir()))
        self.assertEqual(diag.errors, [])


class EdgeLayerOrderingTests(unittest.TestCase):
    """1.2 — edge_layer renders above regions, not below."""

    def test_edge_layer_y_is_less_than_region_y(self) -> None:
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            edge_layer=EdgeLayer(id="edge", leaves=[_leaf("cdn", "aws_cloudfront", "CDN")]),
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc",
                zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                    Subnet(id="app", tier="app", leaves=[_leaf("svc")]),
                ])],
            )])],
        ))
        xml = compile_network_ir(ir)
        cells = _cells(xml)
        edge_y = _geom(cells["edge"])[1]
        region_y = _geom(cells["r"])[1]
        self.assertLess(edge_y, region_y)


class ElkFailureSurfacesAsCompilerErrorTests(unittest.TestCase):
    """1.3 — a real ELK failure must not surface as an unhandled internal exception."""

    def test_elk_layout_error_becomes_compiler_error(self) -> None:
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc",
                zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                    Subnet(id="app", tier="app", leaves=[_leaf("svc")]),
                ])],
            )])],
        ))
        with patch(
            "app.adapters.outbound.diagram.compiler.elk_layout_network.run_elk",
            side_effect=ElkLayoutError("boom"),
        ):
            with self.assertRaises(CompilerError):
                compile_network_ir(ir)


class ActorSafetyNetTests(unittest.TestCase):
    """1.4 — a misfiled perimeter service on external_actors relocates into edge_layer."""

    def _raw(self, actor_label: str, actor_kind: str = "saas_ingress") -> dict:
        return {
            "view": "network", "platform": "aws", "title": "Test",
            "cloud": {
                "id": "cloud", "label": "AWS Cloud",
                "regions": [{
                    "id": "r1", "label": "us-east-1",
                    "vpcs": [{
                        "id": "vpc1", "label": "VPC",
                        "zones": [{
                            "id": "az1", "label": "AZ1",
                            "subnets": [{
                                "id": "sub1", "tier": "app",
                                "leaves": [{"id": "svc1", "shape_type": "aws_ec2", "label": "App"}],
                            }],
                        }],
                    }],
                }],
            },
            "external_actors": [{"id": "actor1", "kind": actor_kind, "label": actor_label}],
            "flows": [{"source": "actor1", "target": "svc1", "label": "Requests", "primary": True}],
        }

    def test_cloudfront_actor_relocates_into_edge_layer(self) -> None:
        ir = NetworkIR.model_validate(self._raw("CloudFront"))
        self.assertEqual(ir.external_actors, [])
        self.assertIsNotNone(ir.cloud.edge_layer)
        self.assertEqual([leaf.id for leaf in ir.cloud.edge_layer.leaves], ["actor1"])
        self.assertEqual(ir.cloud.edge_layer.leaves[0].shape_type, "aws_cloudfront")

    def test_non_perimeter_actor_is_left_alone(self) -> None:
        ir = NetworkIR.model_validate(self._raw("Okta SSO"))
        self.assertEqual([a.label for a in ir.external_actors], ["Okta SSO"])
        self.assertIsNone(ir.cloud.edge_layer)

    def test_relocated_actor_merges_into_existing_edge_layer(self) -> None:
        raw = self._raw("CloudFront")
        raw["cloud"]["edge_layer"] = {
            "id": "edge_layer", "label": "Edge / Global Layer",
            "leaves": [{"id": "r53", "shape_type": "aws_route53", "label": "Route 53"}],
        }
        ir = NetworkIR.model_validate(raw)
        leaf_ids = [leaf.id for leaf in ir.cloud.edge_layer.leaves]
        self.assertEqual(leaf_ids, ["r53", "actor1"])


class SaasIconThreadingTests(unittest.TestCase):
    """1.5 — Network's system prompt actually includes the SaaS icon pack."""

    def test_network_prompt_includes_saas_catalog(self) -> None:
        prompt = get_network_ir_system_prompt(platform="aws")
        self.assertIn("Enterprise SaaS", prompt)


class SemanticWarningTests(unittest.TestCase):
    """1.6/1.7 — new non-blocking nudges."""

    def _three_tier_ir(self, *, flow_labels: bool, reach_data: bool) -> NetworkIR:
        zone = AvailabilityZone(
            id="az", label="AZ",
            subnets=[
                Subnet(id="pub", tier="public", leaves=[_leaf("alb", "aws_alb", "ALB")]),
                Subnet(id="app", tier="app", leaves=[_leaf("app1")]),
                Subnet(id="data", tier="data", leaves=[_leaf("db", "aws_rds", "DB")]),
            ],
        )
        flows = [
            Flow(source="user", target="alb", label="HTTPS" if flow_labels else "", primary=True),
        ]
        if reach_data:
            flows.append(Flow(source="alb", target="app1", label="Forwards" if flow_labels else "", primary=True))
            flows.append(Flow(source="app1", target="db", label="SQL" if flow_labels else "", primary=True))
        else:
            flows.append(Flow(source="alb", target="app1", label="Forwards" if flow_labels else "", primary=True))
        return NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=[zone])])],
        ), external_actors=[ExternalActor(id="user", kind="users", label="User")], flows=flows)

    def test_blank_primary_label_warns(self) -> None:
        ir = self._three_tier_ir(flow_labels=False, reach_data=True)
        warnings = ir.semantic_warnings()
        self.assertTrue(any("has no label" in w for w in warnings), warnings)

    def test_labeled_primary_flow_does_not_warn_about_labels(self) -> None:
        ir = self._three_tier_ir(flow_labels=True, reach_data=True)
        warnings = ir.semantic_warnings()
        self.assertFalse(any("has no label" in w for w in warnings), warnings)

    def test_primary_path_never_reaching_data_tier_warns(self) -> None:
        ir = self._three_tier_ir(flow_labels=True, reach_data=False)
        warnings = ir.semantic_warnings()
        self.assertTrue(any("never reaches it" in w for w in warnings), warnings)

    def test_primary_path_reaching_data_tier_does_not_warn(self) -> None:
        ir = self._three_tier_ir(flow_labels=True, reach_data=True)
        warnings = ir.semantic_warnings()
        self.assertFalse(any("never reaches it" in w for w in warnings), warnings)

    def _hub_ir(self, num_vpcs: int) -> NetworkIR:
        vpcs = []
        flows = []
        for i in range(1, num_vpcs + 1):
            vpcs.append(Vpc(
                id=f"vpc{i}", label=f"VPC{i}",
                zones=[AvailabilityZone(id=f"az{i}", label=f"AZ{i}", subnets=[
                    Subnet(id=f"app{i}", tier="app", leaves=[_leaf(f"svc{i}")]),
                ])],
            ))
            flows.append(Flow(source="hub", target=f"svc{i}", label="Connects", primary=False))
        return NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=vpcs)],
            connectivity_hubs=[_leaf("hub", "aws_transit_gateway", "Transit Gateway")],
        ), flows=flows)

    def test_hub_touching_one_vpc_warns(self) -> None:
        ir = self._hub_ir(1)
        warnings = ir.semantic_warnings()
        self.assertTrue(any("only touches 1 VPC" in w for w in warnings), warnings)

    def test_hub_touching_two_vpcs_does_not_warn(self) -> None:
        ir = self._hub_ir(2)
        warnings = ir.semantic_warnings()
        self.assertFalse(any("connectivity hub" in w for w in warnings), warnings)

    def test_hub_with_zero_flows_does_not_warn(self) -> None:
        ir = self._hub_ir(1)
        ir.flows = []
        warnings = ir.semantic_warnings()
        self.assertFalse(any("connectivity hub" in w for w in warnings), warnings)


class RegionAndVpcDensityTests(unittest.TestCase):
    """3.1 #9-#12 — density shapes no prior fixture reached."""

    def _vpc(self, vid: str, n_zones: int = 1) -> Vpc:
        zones = [
            AvailabilityZone(id=f"{vid}_az{i}", label=f"AZ-{i}", subnets=[
                Subnet(id=f"{vid}_app{i}", tier="app", leaves=[_leaf(f"{vid}_svc{i}")]),
            ])
            for i in range(1, n_zones + 1)
        ]
        return Vpc(id=vid, label=vid.upper(), zones=zones)

    def test_region_with_two_peer_vpcs_respects_vpc_gap(self) -> None:
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[self._vpc("vpca"), self._vpc("vpcb")])],
        ))
        xml = compile_network_ir(ir)
        cells = _cells(xml)
        x1, _, w1, _ = _geom(cells["vpca"])
        x2, _, _, _ = _geom(cells["vpcb"])
        self.assertGreaterEqual(x2 - (x1 + w1), geo.VPC_GAP_X)
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])

    def test_vpc_with_three_availability_zones_compiles_clean(self) -> None:
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[self._vpc("vpc", n_zones=3)])],
        ))
        xml = compile_network_ir(ir)
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        cells = _cells(xml)
        for i in (1, 2, 3):
            self.assertIn(f"vpc_az{i}", cells)

    def test_a_large_estate_is_drawn_in_full_and_only_noted(self) -> None:
        """54 leaves — twice the figure that used to be a hard cap. It was a
        semantic error, so an estate this size failed the request outright;
        rendering one showed the layout holds (containers size to contents,
        subnets stay aligned), so the count is a log note now."""
        tiers = ("public", "app", "data")
        zones, per = 3, 6
        n = 0
        az = []
        for z in range(zones):
            subnets = []
            for tier in tiers:
                leaves = []
                for _ in range(per):
                    leaves.append(_leaf(f"n{n}"))
                    n += 1
                subnets.append(Subnet(id=f"{tier}{z}", tier=tier, leaves=leaves))
            az.append(AvailabilityZone(id=f"az{z}", label=f"AZ-{z}", subnets=subnets))
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=az)])],
        ))
        ids = [lf.id for z in az for s in z.subnets for lf in s.leaves]
        ir.flows = [Flow(source=a, target=b) for a, b in zip(ids, ids[1:])]

        self.assertEqual(n, 54)
        self.assertEqual(ir.semantic_errors(), [])
        self.assertTrue(any("54 leaves" in note for note in ir.density_notes()))
        # Never a corrective ask: "fixing" this means deleting services.
        self.assertNotIn("54", " ".join(ir.semantic_warnings()))
        xml = compile_network_ir(ir)
        self.assertEqual(diagnose_xml(xml).errors, [])
        cells = _cells(xml)
        for leaf_id in ids:
            self.assertIn(leaf_id, cells)

    def test_multiple_connectivity_hubs_form_a_grid(self) -> None:
        hubs = [_leaf(f"hub{i}", "aws_transit_gateway", f"TGW {i}") for i in range(1, 4)]
        zone = AvailabilityZone(id="az", label="AZ", subnets=[
            Subnet(id="pub", tier="public", leaves=[_leaf("alb", "aws_alb", "ALB")]),
            Subnet(id="app", tier="app", leaves=[_leaf("svc")]),
        ])
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=[zone])])],
            connectivity_hubs=hubs,
        ), flows=[Flow(source=h.id, target="svc", label="Connects", primary=False) for h in hubs])
        xml = compile_network_ir(ir)
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])
        cells = _cells(xml)
        for hub in hubs:
            self.assertIn(hub.id, cells)

    def test_multi_region_compiles_and_equalizes_zones_independently_per_vpc(self) -> None:
        """Two regions, each with its own asymmetric zone pair — equalization
        must be scoped per-VPC-group, not accidentally global across regions."""
        def asym_vpc(vid: str) -> Vpc:
            zone_big = AvailabilityZone(id=f"{vid}_big", label="Big", subnets=[
                Subnet(id=f"{vid}_big_pub", tier="public", leaves=[_leaf(f"{vid}_alb", "aws_alb", "ALB")]),
                Subnet(id=f"{vid}_big_app", tier="app", leaves=[_leaf(f"{vid}_b1"), _leaf(f"{vid}_b2")]),
            ])
            zone_small = AvailabilityZone(id=f"{vid}_small", label="Small", subnets=[
                Subnet(id=f"{vid}_small_app", tier="app", leaves=[_leaf(f"{vid}_s1")]),
            ])
            return Vpc(id=vid, zones=[zone_big, zone_small])

        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[
                Region(id="r1", label="us-east-1", vpcs=[asym_vpc("v1")]),
                Region(id="r2", label="us-west-2", vpcs=[asym_vpc("v2")]),
            ],
        ), external_actors=[ExternalActor(id="user", kind="users", label="User")], flows=[
            Flow(source="user", target="v1_alb", label="HTTPS", primary=True),
            Flow(source="v1_alb", target="v1_b1", label="Forwards", primary=True),
            Flow(source="user", target="v2_alb", label="HTTPS", primary=True),
            Flow(source="v2_alb", target="v2_b1", label="Forwards", primary=True),
        ])
        geos = layout_network_ir(ir)
        # Each region's own peer zones still equalize with each other...
        self.assertEqual(geos["v1_big"].h, geos["v1_small"].h)
        self.assertEqual(geos["v2_big"].h, geos["v2_small"].h)
        diag = diagnose_xml(compile_network_ir(ir))
        self.assertEqual(diag.errors, [])


class CriticalityPreservationTests(unittest.TestCase):
    """3.1 #13 — a critical leaf's red border survives compile + assign_saas_shape_types."""

    def test_gateway_criticality_survives_compile(self) -> None:
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc", gateway=_leaf("alb", "aws_alb", "ALB", criticality="critical"),
                zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                    Subnet(id="app", tier="app", leaves=[_leaf("svc")]),
                ])],
            )])],
        ))
        xml = compile_network_ir(ir)
        cells = _cells(xml)
        self.assertEqual(cells["alb"].get("criticality"), "critical")
        self.assertIn("B85450", cells["alb"].get("style"))

    def test_subnet_leaf_criticality_survives_assign_saas_shape_types(self) -> None:
        from app.adapters.outbound.diagram.architecture_docs import assign_saas_shape_types

        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc",
                zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                    Subnet(id="app", tier="app", leaves=[
                        _leaf("okta", "logical_saas", "Okta SSO", criticality="critical"),
                    ]),
                ])],
            )])],
        ))
        xml = compile_network_ir(ir)
        rewritten = assign_saas_shape_types(xml)
        cells = _cells(rewritten)
        self.assertEqual(cells["okta"].get("criticality"), "critical")
        self.assertIn("B85450", cells["okta"].get("style"))


class LabelEdgeCaseTests(unittest.TestCase):
    """3.1 #14-#15 — duplicate and very long labels."""

    def test_duplicate_labels_across_subnets_do_not_conflate(self) -> None:
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc",
                zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                    Subnet(id="app", tier="app", leaves=[_leaf("svc_app", "aws_ecs", "Worker")]),
                    Subnet(id="data", tier="data", leaves=[_leaf("svc_data", "aws_rds", "Worker")]),
                ])],
            )])],
        ))
        xml = compile_network_ir(ir)
        cells = _cells(xml)
        self.assertEqual(cells["svc_app"].get("value"), "Worker")
        self.assertEqual(cells["svc_data"].get("value"), "Worker")
        self.assertNotEqual(cells["svc_app"].get("parent"), cells["svc_data"].get("parent"))
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])

    def test_very_long_leaf_label_does_not_crash_or_break_geometry(self) -> None:
        long_label = " ".join(["Enterprise Customer Relationship Management Integration Service"] * 2)
        ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc",
                zones=[AvailabilityZone(id="az", label="AZ", subnets=[
                    Subnet(id="app", tier="app", leaves=[_leaf("svc", "aws_ecs", long_label)]),
                ])],
            )])],
        ))
        xml = compile_network_ir(ir)
        cells = _cells(xml)
        self.assertEqual(cells["svc"].get("value"), long_label)
        _, _, w, h = _geom(cells["svc"])
        self.assertEqual((w, h), (geo.LEAF_W, geo.LEAF_H))
        diag = diagnose_xml(xml)
        self.assertEqual(diag.errors, [])


class BareMinimumDiagramStructureTests(unittest.TestCase):
    """3.1 #16 — no edge_layer, no gateway, no connectivity_hubs, single
    AZ/subnet/leaf. The existing test_minimal_diagram_has_no_diagnostics only
    asserts zero diagnostics; this asserts the actual structure."""

    def setUp(self) -> None:
        self.ir = NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc",
                zones=[AvailabilityZone(id="az", label="AZ-1a", subnets=[
                    Subnet(id="app", tier="app", leaves=[_leaf("svc", "aws_ecs", "Service")]),
                ])],
            )])],
        ))
        self.xml = compile_network_ir(self.ir)
        self.cells = _cells(self.xml)

    def test_exactly_the_expected_cells_exist(self) -> None:
        expected = {"cloud", "r", "vpc", "az", "app", "svc"}
        vertex_ids = {cid for cid, c in self.cells.items() if c.get("vertex") == "1"}
        self.assertEqual(vertex_ids, expected)

    def test_no_edge_layer_no_gateway_no_hub_shelf_cells(self) -> None:
        for cid in self.cells:
            self.assertNotIn("edge_layer", cid)
            self.assertNotIn("gateway", cid)
            self.assertNotIn("connectivity", cid)

    def test_hierarchy_nests_correctly(self) -> None:
        self.assertEqual(self.cells["vpc"].get("parent"), "r")
        self.assertEqual(self.cells["az"].get("parent"), "vpc")
        self.assertEqual(self.cells["app"].get("parent"), "az")
        self.assertEqual(self.cells["svc"].get("parent"), "app")

    def test_no_edges_at_all(self) -> None:
        self.assertFalse(any(c.get("edge") == "1" for c in self.cells.values()))


class ViewConformanceTests(unittest.TestCase):
    """3.2 — Network's new entries in view_conformance.py's three vocab dicts."""

    def _clean_ir(self) -> NetworkIR:
        return NetworkIR(cloud=CloudBoundary(
            id="cloud", label="AWS Cloud",
            regions=[Region(id="r", label="us-east-1", vpcs=[Vpc(
                id="vpc", label="Production VPC",
                gateway=_leaf("alb", "aws_alb", "ALB"),
                zones=[AvailabilityZone(id="az", label="AZ-1a", subnets=[
                    Subnet(id="pub", tier="public", leaves=[_leaf("nat", "aws_nat_gateway", "NAT")]),
                    Subnet(id="app", tier="app", leaves=[_leaf("svc")]),
                ])],
            )])],
        ), external_actors=[ExternalActor(id="user", kind="users", label="User")], flows=[
            Flow(source="user", target="alb", label="HTTPS", primary=True),
            Flow(source="alb", target="svc", label="Forwards", primary=True),
        ])

    def test_realistic_network_page_has_no_group_taxonomy_or_forbidden_violations(self) -> None:
        xml = compile_network_ir(self._clean_ir())
        result = score_view(xml, view="network")
        codes = {v.code for v in result.violations}
        self.assertNotIn("group_taxonomy", codes)
        self.assertNotIn("forbidden_node", codes)

    def test_business_journey_vocabulary_is_forbidden_on_network(self) -> None:
        ir = self._clean_ir()
        ir.cloud.regions[0].vpcs[0].zones[0].subnets[1].leaves[0] = _leaf(
            "svc", "aws_ecs", "Checkout Flow",
        )
        xml = compile_network_ir(ir)
        result = score_view(xml, view="network")
        self.assertTrue(
            any(v.code == "forbidden_node" for v in result.violations),
            result.violations,
        )

    def test_protocol_edge_vocab_does_not_trigger_mismatch_warning(self) -> None:
        xml = compile_network_ir(self._clean_ir())
        result = score_view(xml, view="network")
        self.assertFalse(
            any(v.code == "edge_vocab_mismatch" for v in result.violations),
            result.violations,
        )


if __name__ == "__main__":
    unittest.main()
