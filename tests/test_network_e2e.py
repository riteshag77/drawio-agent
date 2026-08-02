"""End-to-end (mocked LLM) coverage for Network path, WAF gate, suggestions."""

from __future__ import annotations

import asyncio
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from app.core.logging import StepLogger
from app.core.services import xml_pipeline
from app.domain.entities import XmlDiagram

NETWORK_FIXTURE = (
    '<mxfile host="app.diagrams.net"><diagram name="Network" id="d1">'
    "<mxGraphModel><root>"
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="cloud" value="AWS Cloud" style="rounded=1;container=1;" vertex="1" parent="1">'
    '<mxGeometry x="40" y="40" width="800" height="500" as="geometry"/></mxCell>'
    '<mxCell id="vpc" value="VPC" style="rounded=1;container=1;" vertex="1" parent="cloud">'
    '<mxGeometry x="40" y="60" width="100" height="80" as="geometry"/></mxCell>'
    '<mxCell id="az1" value="Availability Zone A" style="rounded=1;container=1;" '
    'vertex="1" parent="vpc">'
    '<mxGeometry x="24" y="48" width="200" height="160" as="geometry"/></mxCell>'
    '<mxCell id="ecs" value="ECS" shape_type="aws_ecs" style="rounded=0;" vertex="1" parent="az1">'
    '<mxGeometry x="24" y="48" width="78" height="78" as="geometry"/></mxCell>'
    '<mxCell id="overview_panel" value="Overview" style="rounded=1;arcSize=2;" '
    'vertex="1" parent="1">'
    '<mxGeometry x="1000" y="40" width="400" height="200" as="geometry"/></mxCell>'
    "</root></mxGraphModel></diagram></mxfile>"
)

LOGICAL_FIXTURE = (
    '<mxfile host="app.diagrams.net"><diagram name="Logical" id="d0">'
    "<mxGraphModel><root>"
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="domain" value="Experience domain" style="rounded=1;container=1;" '
    'vertex="1" parent="1">'
    '<mxGeometry x="40" y="40" width="280" height="160" as="geometry"/></mxCell>'
    '<mxCell id="ord" value="Orders capability" style="rounded=1;" vertex="1" parent="domain">'
    '<mxGeometry x="24" y="48" width="120" height="60" as="geometry"/></mxCell>'
    '<mxCell id="overview_panel" value="Logical overview" style="rounded=1;" '
    'vertex="1" parent="1">'
    '<mxGeometry x="900" y="40" width="400" height="180" as="geometry"/></mxCell>'
    "</root></mxGraphModel></diagram></mxfile>"
)


class NetworkE2ETests(unittest.TestCase):
    def test_network_only_success_and_fit(self) -> None:
        diagram = XmlDiagram(
            xml=NETWORK_FIXTURE,
            assistant_message="ok",
            suggestions=[{"text": "Add NAT Gateway", "category": "network"}],
            step_markers=[],
        )

        async def _fake_resolve(*_a, **_k):
            return {}

        with patch.object(xml_pipeline, "generate_xml_diagram_from_ir", return_value=diagram), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_fake_resolve):
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="HA web app on AWS",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                diagram_views=["network"],
                include_suggestions=True,
            ))
        self.assertEqual(result["status"], "success")
        self.assertIn("<diagram", result["xml"])
        self.assertEqual(result["xml"].count("<diagram"), 1)
        tree = ET.fromstring(result["xml"])
        vpc = next(c for c in tree.iter("mxCell") if c.get("id") == "vpc")
        g = vpc.find("mxGeometry")
        # Undersized VPC must grow after fit
        self.assertGreaterEqual(float(g.get("width") or 0), 100)
        sugg = result.get("suggestions") or []
        self.assertTrue(sugg)
        self.assertEqual(sugg[0]["category"], "network")
        self.assertEqual(sugg[0]["text"], "Add NAT Gateway")

    def test_cost_skipped_without_network_view(self) -> None:
        diagram = XmlDiagram(xml=LOGICAL_FIXTURE, assistant_message="ok", suggestions=[], step_markers=[])

        async def _fake_resolve(*_a, **_k):
            return {}

        with patch.object(xml_pipeline, "generate_xml_diagram_from_ir", return_value=diagram), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_fake_resolve), \
                patch.object(xml_pipeline, "estimate_monthly_cost") as cost:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="capability view only",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                diagram_views=["logical"],
                cost_estimate=True,
            ))
        cost.assert_not_called()
        self.assertIsNone(result.get("cost_estimate"))

    def test_waf_skipped_without_network_view(self) -> None:
        diagram = XmlDiagram(xml=LOGICAL_FIXTURE, assistant_message="ok", suggestions=[], step_markers=[])

        async def _fake_resolve(*_a, **_k):
            return {}

        with patch.object(xml_pipeline, "generate_xml_diagram_from_ir", return_value=diagram), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_fake_resolve), \
                patch.object(xml_pipeline, "assess_well_architected") as waf:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="capability view only",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                diagram_views=["logical"],
                well_architected=True,
            ))
        waf.assert_not_called()
        self.assertIsNone(result.get("well_architected"))

    def test_waf_runs_when_network_selected(self) -> None:
        diagram = XmlDiagram(xml=NETWORK_FIXTURE, assistant_message="ok", suggestions=[], step_markers=[])
        waf_payload = {
            "overall_status": "OK",
            "pillars": [],
            "summary": "",
            "services_analyzed": 1,
            "platform": "aws",
        }

        async def _fake_resolve(*_a, **_k):
            return {}

        with patch.object(xml_pipeline, "generate_xml_diagram_from_ir", return_value=diagram), \
                patch.object(xml_pipeline, "resolve_shape_styles", side_effect=_fake_resolve), \
                patch.object(xml_pipeline, "assess_well_architected", return_value=waf_payload) as waf:
            result = asyncio.run(xml_pipeline.run_xml_pipeline(
                user_prompt="network with waf",
                provider="gemini",
                platform="aws",
                logger=StepLogger(),
                diagram_renderer="local",
                diagram_views=["network"],
                well_architected=True,
            ))
        waf.assert_called_once()
        self.assertEqual(result.get("well_architected"), waf_payload)


if __name__ == "__main__":
    unittest.main()
