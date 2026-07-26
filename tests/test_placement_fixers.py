"""Placement fixer unit tests — no-op when already correct; safe LB hoist."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.placement_fixers import (
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


if __name__ == "__main__":
    unittest.main()
