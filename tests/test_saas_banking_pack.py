"""Brand SaaS icons on all views + Logical (SAAS) labels."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from app.adapters.outbound.diagram.architecture_docs import (
    assign_logical_shape_types,
    assign_saas_shape_types,
    ensure_saas_labels_on_logical,
    match_saas_shape_type,
)
from app.adapters.outbound.diagram.drawio_mcp_client import _SAAS_BUILTIN_STYLES, _builtin_style
from app.adapters.outbound.diagram.view_fixers import apply_view_fixers
from app.adapters.outbound.llm.prompts.system import get_xml_gen_system_prompt


def _wrap(cells: str) -> str:
    return (
        '<mxfile><diagram name="Page" id="p1">'
        '<mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        f"{cells}"
        "</root></mxGraphModel></diagram></mxfile>"
    )


class LogicalBrandSaasTests(unittest.TestCase):
    def test_salesforce_gets_saas_suffix_and_brand_icon(self) -> None:
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
        self.assertEqual(by_id["ord"].get("shape_type"), "logical_capability")

    def test_view_fixers_keep_brand_saas_on_logical(self) -> None:
        page = _wrap(
            '<mxCell id="gw" value="Guidewire" shape_type="saas_guidewire" '
            'style="fillColor=#E87722;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="80" height="60" as="geometry"/></mxCell>'
            '<mxCell id="api" value="Claims API" style="shape=hexagon;" vertex="1" parent="1">'
            '<mxGeometry x="200" y="40" width="80" height="60" as="geometry"/></mxCell>'
        )
        fixed = apply_view_fixers(page, view="logical")
        tagged = assign_logical_shape_types(fixed)
        out = assign_saas_shape_types(tagged)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertTrue((by_id["gw"].get("value") or "").endswith("(SAAS)"))
        self.assertEqual(by_id["gw"].get("shape_type"), "saas_guidewire")
        self.assertIn("data:image/svg+xml,", by_id["gw"].get("style") or "")

    def test_unknown_saas_keeps_logical_saas(self) -> None:
        page = _wrap(
            '<mxCell id="ext" value="Acme SaaS" style="shape=ellipse;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="80" height="60" as="geometry"/></mxCell>'
        )
        labeled = ensure_saas_labels_on_logical(page)
        tagged = assign_logical_shape_types(labeled)
        out = assign_saas_shape_types(tagged)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertTrue((by_id["ext"].get("value") or "").endswith("(SAAS)"))
        self.assertEqual(by_id["ext"].get("shape_type"), "logical_saas")
        self.assertIn("FFF2CC", by_id["ext"].get("style") or "")


class NonLogicalSaasPackTests(unittest.TestCase):
    def test_workday_heuristic_gets_saas_builtin(self) -> None:
        page = _wrap(
            '<mxCell id="wd" value="Workday" style="rounded=0;fillColor=#F5F5F5;" '
            'vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = assign_saas_shape_types(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["wd"].get("shape_type"), "saas_workday")
        style = by_id["wd"].get("style") or ""
        self.assertIn("shape=image", style)
        self.assertIn("data:image/svg+xml,", style)
        self.assertEqual(match_saas_shape_type("Workday HCM"), "saas_workday")

    def test_builtin_skips_mcp_for_saas_keys(self) -> None:
        style = _builtin_style("saas_salesforce") or ""
        self.assertIn("shape=image", style)
        self.assertIn("data:image/svg+xml,", style)
        self.assertTrue(style.endswith(";"))
        self.assertIn("saas_guidewire", _SAAS_BUILTIN_STYLES)
        self.assertIn("saas_office365", _SAAS_BUILTIN_STYLES)

    def test_assign_does_not_override_aws_shape_type(self) -> None:
        page = _wrap(
            '<mxCell id="lam" value="Salesforce sync worker" shape_type="aws_lambda" '
            'style="fillColor=#ED7100;" vertex="1" parent="1">'
            '<mxGeometry x="40" y="40" width="78" height="78" as="geometry"/></mxCell>'
        )
        out = assign_saas_shape_types(page)
        by_id = {c.get("id"): c for c in ET.fromstring(out).iter("mxCell") if c.get("id")}
        self.assertEqual(by_id["lam"].get("shape_type"), "aws_lambda")


class SaasPromptAndCatalogTests(unittest.TestCase):
    def test_catalog_file_lists_shape_types(self) -> None:
        path = Path(__file__).resolve().parents[1] / "config" / "references" / "saas.txt"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        for key in ("saas_salesforce", "saas_workday", "saas_guidewire", "saas_temenos"):
            self.assertIn(key, text)

    def test_prompt_includes_saas_catalog_for_vendor_hint(self) -> None:
        prompt = get_xml_gen_system_prompt(
            platform="aws",
            primary_view="network",
            user_hint="insurance claims with Guidewire and Salesforce",
        )
        self.assertIn("saas_guidewire", prompt)
        self.assertIn("saas_salesforce", prompt)
        self.assertIn("shape_type=saas_", prompt)

    def test_logical_primary_includes_saas_brand_catalog(self) -> None:
        prompt = get_xml_gen_system_prompt(
            platform="aws",
            primary_view="logical",
            user_hint="salesforce workday guidewire banking",
        )
        self.assertIn("(SAAS)", prompt)
        self.assertIn("Enterprise SaaS shape_types (XML mode)", prompt)
        self.assertIn("saas_salesforce", prompt)
        self.assertIn("Emit a service **leaf** with grey placeholder style", prompt)
        # Still no cloud classify pack on Logical primary.
        self.assertNotIn("Service Classification — AWS", prompt)

    def test_stencil_svgs_on_disk_and_embedded(self) -> None:
        icons = Path(__file__).resolve().parents[1] / "static" / "saas-icons"
        self.assertTrue((icons / "salesforce.svg").is_file())
        self.assertTrue((icons / "workday.svg").is_file())
        style = _builtin_style("saas_salesforce") or ""
        self.assertIn("shape=image", style)
        self.assertIn("data:image/svg+xml,", style)
        wd = _builtin_style("saas_workday") or ""
        self.assertIn("data:image/svg+xml,", wd)
        self.assertIn("saas_salesforce", _SAAS_BUILTIN_STYLES)


if __name__ == "__main__":
    unittest.main()
