"""Enterprise SaaS placement, empty AZ shrink, Logical brand icons."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET

from app.adapters.outbound.diagram.architecture_docs import (
    assign_logical_shape_types,
    assign_saas_shape_types,
    ensure_saas_labels_on_logical,
)
from app.adapters.outbound.diagram.xml_tools import fit_containers_to_children
from app.adapters.outbound.llm.prompts.ir import get_network_ir_system_prompt


def _wrap(cells: str) -> str:
    return (
        '<mxfile><diagram name="Network" id="n1">'
        '<mxGraphModel pageWidth="2400" pageHeight="1400"><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class EmptyAzShrinkTests(unittest.TestCase):
    def test_childless_zone_shrinks(self) -> None:
        page = _wrap(
            '<mxCell id="vpc" value="VPC" style="container=1;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="800" height="400" as="geometry"/></mxCell>'
            '<mxCell id="azb" value="Availability Zone B" style="container=1;rounded=1;" '
            'vertex="1" parent="vpc">'
            '<mxGeometry x="400" y="60" width="360" height="300" as="geometry"/></mxCell>'
        )
        out = fit_containers_to_children(page)
        az = next(c for c in ET.fromstring(out).iter("mxCell") if c.get("id") == "azb")
        geo = az.find("mxGeometry")
        self.assertLessEqual(float(geo.get("width") or 0), 130.0)
        self.assertLessEqual(float(geo.get("height") or 0), 90.0)


class LogicalBrandIconTests(unittest.TestCase):
    def test_preserves_existing_saas_shape_type(self) -> None:
        page = _wrap(
            '<mxCell id="sf" value="Salesforce (SAAS)" shape_type="saas_salesforce" '
            'style="shape=image;image=data:image/svg+xml,abc;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = assign_logical_shape_types(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["sf"].get("shape_type"), "saas_salesforce")

    def test_salesforce_saas_label_gets_svg(self) -> None:
        page = _wrap(
            '<mxCell id="sf" value="Salesforce" style="shape=ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="80" height="60" as="geometry"/></mxCell>'
            '<mxCell id="ord" value="Orders" style="shape=rectangle;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="100" height="60" as="geometry"/></mxCell>'
        )
        labeled = ensure_saas_labels_on_logical(page)
        tagged = assign_logical_shape_types(labeled)
        out = assign_saas_shape_types(tagged)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertTrue((by_id["sf"].get("value") or "").endswith("(SAAS)"))
        self.assertEqual(by_id["sf"].get("shape_type"), "saas_salesforce")
        self.assertIn("data:image/svg+xml,", by_id["sf"].get("style") or "")


class PromptSaasPlacementTests(unittest.TestCase):
    def test_network_prompt_keeps_saas_as_external_actors(self) -> None:
        prompt = get_network_ir_system_prompt(
            platform="aws",
            user_hint="salesforce workday guidewire",
        )
        self.assertIn("saas_ingress", prompt)
        self.assertIn("saas_egress", prompt)
        self.assertIn("Never place an external actor inside the cloud hierarchy", prompt)


if __name__ == "__main__":
    unittest.main()
