"""Generic SaaS journey placement + SuccessFactors icon."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from app.adapters.outbound.diagram.architecture_docs import (
    assign_logical_shape_types,
    assign_saas_shape_types,
    ensure_saas_labels_on_logical,
    match_saas_shape_type,
    saas_default_data_flow_direction,
    saas_default_network_side,
    saas_journey_role,
)
from app.adapters.outbound.diagram.drawio_mcp_client import _builtin_style
from app.adapters.outbound.diagram.placement_fixers import resolve_misplaced_saas_leaves


def _wrap(cells: str) -> str:
    return (
        '<mxfile><diagram name="Network" id="n1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class SuccessFactorsIconTests(unittest.TestCase):
    def test_match_and_svg_builtin(self) -> None:
        self.assertEqual(match_saas_shape_type("SAP SuccessFactors"), "saas_successfactors")
        self.assertEqual(match_saas_shape_type("Success Factors"), "saas_successfactors")
        self.assertEqual(saas_journey_role("saas_successfactors"), "workforce")
        style = _builtin_style("saas_successfactors") or ""
        self.assertIn("data:image/svg+xml,", style)
        self.assertTrue((Path(__file__).resolve().parents[1] / "static" / "saas-icons" / "successfactors.svg").is_file())

    def test_logical_successfactors_brand(self) -> None:
        page = _wrap(
            '<mxCell id="sf" value="SuccessFactors" style="shape=ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="80" height="60" as="geometry"/></mxCell>'
            '<mxCell id="ord" value="Orders" style="shape=rectangle;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="100" height="60" as="geometry"/></mxCell>'
        )
        out = assign_saas_shape_types(assign_logical_shape_types(ensure_saas_labels_on_logical(page)))
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertTrue((by_id["sf"].get("value") or "").endswith("(SAAS)"))
        self.assertEqual(by_id["sf"].get("shape_type"), "saas_successfactors")
        self.assertIn("data:image/svg+xml,", by_id["sf"].get("style") or "")


class JourneyOrderTests(unittest.TestCase):
    def test_okta_ingress_left_salesforce_and_sfs_egress_right(self) -> None:
        page = _wrap(
            '<mxCell id="cloud" value="AWS Cloud" style="container=1;rounded=1;" vertex="1" parent="1">'
            '<mxGeometry x="500" y="40" width="900" height="500" as="geometry"/></mxCell>'
            '<mxCell id="sf" value="Salesforce" shape_type="saas_salesforce" style="shape=image;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="ok" value="Okta" shape_type="saas_okta" style="shape=image;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="140" y="40" width="78" height="78" as="geometry"/></mxCell>'
            '<mxCell id="sfs" value="SuccessFactors" shape_type="saas_successfactors" style="shape=image;" '
            'vertex="1" parent="cloud">'
            '<mxGeometry x="240" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = resolve_misplaced_saas_leaves(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        for cid in ("ok", "sf", "sfs"):
            self.assertEqual(by_id[cid].get("parent"), "1")
        cloud_x = float(by_id["cloud"].find("mxGeometry").get("x") or 0)
        cloud_w = float(by_id["cloud"].find("mxGeometry").get("width") or 0)
        ok_x = float(by_id["ok"].find("mxGeometry").get("x") or 0)
        sf_x = float(by_id["sf"].find("mxGeometry").get("x") or 0)
        sfs_x = float(by_id["sfs"].find("mxGeometry").get("x") or 0)
        # IdP ingress left of Cloud; workforce/experience egress right
        self.assertLess(ok_x + 78, cloud_x)
        self.assertGreaterEqual(sf_x, cloud_x + cloud_w - 1)
        self.assertGreaterEqual(sfs_x, cloud_x + cloud_w - 1)
        # Within egress band, experience before workforce
        self.assertLessEqual(sf_x, sfs_x)


class CrossAxisIndependenceTests(unittest.TestCase):
    """Network's ingress/egress axis and Data-flow's producer/consumer axis
    are answering different questions about the same vendor and must not be
    conflated — a vendor can legitimately default to opposite-sounding
    answers on each axis independently (Fix 1)."""

    def test_okta_is_network_ingress_and_data_flow_producer(self) -> None:
        self.assertEqual(saas_default_network_side("saas_okta"), "ingress")
        self.assertEqual(saas_default_data_flow_direction("saas_okta"), "producer")

    def test_workday_is_network_egress_and_data_flow_producer(self) -> None:
        """Same vendor, same underlying 'workforce' role, different axis —
        the two functions must reach their answers independently, not by
        reusing one boolean for both."""
        self.assertEqual(saas_default_network_side("saas_workday"), "egress")
        self.assertEqual(saas_default_data_flow_direction("saas_workday"), "producer")

    def test_stripe_is_network_egress_and_data_flow_consumer(self) -> None:
        self.assertEqual(saas_default_network_side("saas_stripe"), "egress")
        self.assertEqual(saas_default_data_flow_direction("saas_stripe"), "consumer")


if __name__ == "__main__":
    unittest.main()
