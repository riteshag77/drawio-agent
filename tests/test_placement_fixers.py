"""Placement fixer unit tests — no-op when already correct; safe LB hoist."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.placement_fixers import (
    _classify_data_flow_saas_direction,
    _consolidate_data_flow_saas_shelf,
    _is_saas_leaf_cell,
    resolve_misplaced_edge_services,
    resolve_misplaced_load_balancers,
)


def _wrap(body: str) -> str:
    return (
        '<mxfile host="app.diagrams.net"><diagram name="Network" id="d1">'
        f"<mxGraphModel><root><mxCell id=\"0\"/><mxCell id=\"1\" parent=\"0\"/>{body}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class PlacementFixerTests(unittest.TestCase):
    def test_alb_already_vpc_sibling_unchanged(self) -> None:
        page = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="900" height="600" as="geometry"/></mxCell>'
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="60" width="700" height="480" as="geometry"/></mxCell>'
            '<mxCell id="az1" value="Availability Zone A" style="container=1;" vertex="1" parent="vpc">'
            '<mxGeometry x="40" y="160" width="300" height="280" as="geometry"/></mxCell>'
            '<mxCell id="alb" value="ALB" shape_type="aws_application_load_balancer" '
            'style="rounded=0;" vertex="1" parent="vpc">'
            '<mxGeometry x="40" y="60" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_load_balancers(page)
        alb = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "alb")
        self.assertEqual(alb.get("parent"), "vpc")
        g = alb.find("mxGeometry")
        self.assertEqual(g.get("y"), "60")

    def test_alb_in_subnet_hoisted_above_zone(self) -> None:
        page = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="900" height="600" as="geometry"/></mxCell>'
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="60" width="700" height="480" as="geometry"/></mxCell>'
            '<mxCell id="az1" value="Availability Zone A" style="container=1;" vertex="1" parent="vpc">'
            '<mxGeometry x="40" y="200" width="300" height="280" as="geometry"/></mxCell>'
            '<mxCell id="subnet" value="Public Subnet" style="container=1;" vertex="1" parent="az1">'
            '<mxGeometry x="24" y="48" width="250" height="200" as="geometry"/></mxCell>'
            '<mxCell id="alb" value="ALB" shape_type="aws_alb" style="rounded=0;" '
            'vertex="1" parent="subnet">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_load_balancers(page)
        alb = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "alb")
        self.assertEqual(alb.get("parent"), "vpc")
        g = alb.find("mxGeometry")
        y = float(g.get("y") or 0)
        h = float(g.get("height") or 78)
        # Must sit above zone top (200) with clear gap
        self.assertLessEqual(y + h + 24, 200)
        self.assertGreaterEqual(y, 48)

    def test_cloudfront_already_under_edge_unchanged(self) -> None:
        page = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="900" height="400" as="geometry"/></mxCell>'
            '<mxCell id="edge" value="Edge / Global Layer" style="container=1;" vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="48" width="320" height="160" as="geometry"/></mxCell>'
            '<mxCell id="cf" value="CloudFront" shape_type="aws_cloudfront" '
            'style="rounded=0;" vertex="1" parent="edge">'
            '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_edge_services(page)
        cf = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "cf")
        self.assertEqual(cf.get("parent"), "edge")
        self.assertNotIn("edge_layer_auto", out)


def _cell(id_: str, *, shape_type: str = "", value: str = "") -> ET.Element:
    attrs = {"id": id_, "vertex": "1"}
    if shape_type:
        attrs["shape_type"] = shape_type
    if value:
        attrs["value"] = value
    return ET.Element("mxCell", attrs)


class DataFlowSaasDirectionClassifierTests(unittest.TestCase):
    """Unit tests for _classify_data_flow_saas_direction — the edges-first,
    vendor-default-fallback classifier backing the producer/consumer SaaS
    shelf split (Fix 1)."""

    def test_edge_into_ingest_or_process_classifies_as_producer(self) -> None:
        leaf = _cell("sf", shape_type="saas_salesforce")
        edges = [("sf", "apigw")]  # SaaS -> ingest leaf
        direction = _classify_data_flow_saas_direction(leaf, edges, {"apigw"}, set())
        self.assertEqual(direction, "producer")

    def test_edge_from_process_or_store_classifies_as_consumer(self) -> None:
        leaf = _cell("stripe", shape_type="saas_stripe")
        edges = [("db", "stripe")]  # store leaf -> SaaS
        direction = _classify_data_flow_saas_direction(leaf, edges, set(), {"db"})
        self.assertEqual(direction, "consumer")

    def test_edge_direction_wins_over_vendor_default(self) -> None:
        """Stripe defaults to 'consumer' by vendor role, but a real producer-shaped
        edge must override that default — edges are ground truth, defaults are a
        last resort."""
        leaf = _cell("stripe", shape_type="saas_stripe")
        edges = [("stripe", "apigw")]
        direction = _classify_data_flow_saas_direction(leaf, edges, {"apigw"}, set())
        self.assertEqual(direction, "producer")

    def test_no_edges_falls_back_to_vendor_default(self) -> None:
        producer_leaf = _cell("wd", shape_type="saas_workday")  # workforce -> producer default
        consumer_leaf = _cell("slack", shape_type="saas_slack")  # collab -> consumer default
        self.assertEqual(_classify_data_flow_saas_direction(producer_leaf, [], set(), set()), "producer")
        self.assertEqual(_classify_data_flow_saas_direction(consumer_leaf, [], set(), set()), "consumer")

    def test_unrecognized_vendor_defaults_to_consumer(self) -> None:
        leaf = _cell("mystery", shape_type="saas_some_unknown_vendor")
        self.assertEqual(_classify_data_flow_saas_direction(leaf, [], set(), set()), "consumer")


class ConsolidateDataFlowSaasShelfTests(unittest.TestCase):
    """Integration-level tests for the shelf-creation/reparenting side of Fix 1."""

    def _page(self, extra: str) -> str:
        return (
            '<mxfile host="app.diagrams.net"><diagram name="Data flow" id="d1">'
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="phase_ingest" value="Ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="apigw" value="API GW" vertex="1" parent="phase_ingest">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="phase_store" value="Store" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="0" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="db" value="DB" vertex="1" parent="phase_store">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            f"{extra}"
            "</root></mxGraphModel></diagram></mxfile>"
        )

    def test_stray_leaves_split_into_producer_and_consumer_shelves(self) -> None:
        from app.adapters.outbound.diagram.placement_fixers import enforce_view_journey_columns

        page = self._page(
            '<mxCell id="sf" value="Salesforce" shape_type="saas_salesforce" vertex="1" parent="1">'
            '<mxGeometry x="400" y="400" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="stripe" value="Stripe" shape_type="saas_stripe" vertex="1" parent="1">'
            '<mxGeometry x="500" y="400" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="e1" edge="1" parent="1" source="sf" target="apigw">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
            '<mxCell id="e2" edge="1" parent="1" source="db" target="stripe">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>'
        )
        out = enforce_view_journey_columns(page)
        cells = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(cells["sf"].get("parent"), "phase_saas_producer")
        self.assertEqual(cells["stripe"].get("parent"), "phase_saas_consumer")

    def test_no_saas_leaves_returns_empty_and_leaves_page_untouched(self) -> None:
        page = self._page("")
        tree = ET.fromstring(page).find(".//root")
        cells = [c for c in tree.findall("mxCell") if c.get("id")]
        by_id = {c.get("id"): c for c in cells}
        created = _consolidate_data_flow_saas_shelf(tree, cells, by_id)
        self.assertEqual(created, [])


class SaasLeafCellExcludesAnnotationsTests(unittest.TestCase):
    """Regression for a real defect found via data/captures replay (final
    verification step): the LLM occasionally emits overview_panel with a
    stray leftover shape_type="saas_*" attribute (copy-paste artifact from a
    nearby SaaS leaf), which without this guard got reparented into a SaaS
    shelf and ballooned its size to the panel's own text-block geometry."""

    def test_overview_panel_with_stray_saas_shape_type_is_not_a_saas_leaf(self) -> None:
        cell = _cell("overview_panel", shape_type="saas_okta", value="Some overview text")
        self.assertFalse(_is_saas_leaf_cell(cell))

    def test_legend_box_with_stray_saas_shape_type_is_not_a_saas_leaf(self) -> None:
        cell = _cell("legend_box", shape_type="saas_stripe")
        self.assertFalse(_is_saas_leaf_cell(cell))

    def test_real_saas_leaf_still_recognized(self) -> None:
        cell = _cell("okta", shape_type="saas_okta", value="Okta")
        self.assertTrue(_is_saas_leaf_cell(cell))


class ActorContainerizationTests(unittest.TestCase):
    """Fix 2c: external actors on Security/Data-flow are wrapped in a grid-sized
    band container instead of floating as bare parent="1" leaves."""

    def _data_flow_page(self, extra: str) -> str:
        return (
            '<mxfile host="app.diagrams.net"><diagram name="Data flow" id="d1">'
            '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="phase_ingest" value="Ingest" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="0" width="200" height="150" as="geometry"/></mxCell>'
            '<mxCell id="apigw" value="API GW" vertex="1" parent="phase_ingest">'
            '<mxGeometry x="20" y="40" width="78" height="78" as="geometry"/></mxCell>'
            f"{extra}"
            "</root></mxGraphModel></diagram></mxfile>"
        )

    def test_actors_wrapped_in_band_container(self) -> None:
        from app.adapters.outbound.diagram.placement_fixers import enforce_corridor_gap_consistency

        page = self._data_flow_page(
            '<mxCell id="users" value="Users" shape_type="aws_users" vertex="1" parent="1">'
            '<mxGeometry x="20" y="10" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="onprem" value="On-Prem" shape_type="aws_corporate_data_center" vertex="1" parent="1">'
            '<mxGeometry x="20" y="300" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = enforce_corridor_gap_consistency(page)
        cells = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertIn("actors_left", cells)
        band = cells["actors_left"]
        self.assertEqual(band.get("parent"), "1")
        for aid in ("users", "onprem"):
            self.assertEqual(cells[aid].get("parent"), "actors_left")
        # The band is sized to actually contain both actors, not an arbitrary default.
        band_geo = band.find("mxGeometry")
        band_h = float(band_geo.get("height"))
        for aid in ("users", "onprem"):
            g = cells[aid].find("mxGeometry")
            self.assertLessEqual(float(g.get("y")) + float(g.get("height")), band_h)

    def test_no_actors_leaves_page_untouched(self) -> None:
        from app.adapters.outbound.diagram.placement_fixers import enforce_corridor_gap_consistency

        page = self._data_flow_page("")
        out = enforce_corridor_gap_consistency(page)
        cells = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertNotIn("actors_left", cells)


if __name__ == "__main__":
    unittest.main()
