"""Tests for platform-specific XML icon prompts and style helpers."""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.drawio_mcp_client import (
    _builtin_style,
    _pick_style,
    _platform_from_shape_type,
    _shape_query,
)
from app.adapters.outbound.diagram.xml_tools import normalize_platform_styles
from app.adapters.outbound.llm.prompts.system import (
    ESTIMATE_PROMPT_TOKENS,
    _load_prompt,
    get_xml_gen_system_prompt,
)


class PlatformPromptTests(unittest.TestCase):
    def test_gcp_prompt_uses_gcp2_not_aws_resource_icon(self) -> None:
        prompt = get_xml_gen_system_prompt(platform="gcp")
        self.assertIn("mxgraph.gcp2", prompt)
        self.assertIn("shape_type", prompt)
        self.assertIn("NEVER use mxgraph.aws4.resourceIcon", prompt)
        self.assertIn("NEVER use mxgraph.gcp2020", prompt)
        # Must not teach AWS Pattern 1 as the primary GCP template
        self.assertNotIn("resIcon=mxgraph.aws4.<name>", prompt)

    def test_azure_prompt_uses_image_paths_not_resource_icon(self) -> None:
        prompt = get_xml_gen_system_prompt(platform="azure")
        self.assertIn("img/lib/azure2", prompt)
        self.assertIn("shape_type", prompt)
        self.assertIn("NEVER use shape=mxgraph.azure2.resourceIcon", prompt)
        self.assertNotIn("shape=mxgraph.aws4.resourceIcon;resIcon=", prompt)

    def test_aws_prompt_keeps_resource_icon(self) -> None:
        prompt = get_xml_gen_system_prompt(platform="aws")
        self.assertIn("mxgraph.aws4.resourceIcon", prompt)
        self.assertIn("resIcon=mxgraph.aws4.", prompt)

    def test_unknown_platform_falls_back_to_aws(self) -> None:
        prompt = get_xml_gen_system_prompt(platform="digitalocean")
        self.assertIn("mxgraph.aws4.resourceIcon", prompt)

    def test_multi_prompt_is_slim_and_prefix_aware(self) -> None:
        prompt = get_xml_gen_system_prompt(platform="multi")
        self.assertIn('shape_type="aws_', prompt)
        self.assertIn('shape_type="gcp_', prompt)
        self.assertIn('shape_type="azure_', prompt)
        self.assertIn("mxgraph.gcp2", prompt)
        self.assertIn("img/lib/azure2", prompt)
        # Must not dump full AWS category fillColor table
        self.assertNotIn("| Compute | #ED7100 |", prompt)
        self.assertNotIn("aws-icons-compute", prompt)
        # Token budget: icon overhead beyond the shared CORE+Network packs should stay lean.
        # Measured as a delta from CORE+Network (not the raw total) — those packs are a
        # fixed, cached cost shared by every platform, so they're not part of
        # "multi's icon overhead".
        shared = (
            ESTIMATE_PROMPT_TOKENS(_load_prompt("core.txt"))
            + ESTIMATE_PROMPT_TOKENS(_load_prompt("views/network.txt"))
        )
        overhead = ESTIMATE_PROMPT_TOKENS(prompt) - shared
        self.assertLess(overhead, 2000, f"multi platform overhead ~{overhead} tokens exceeds budget")



class PickStyleTests(unittest.TestCase):
    def test_pick_style_prefers_gcp2(self) -> None:
        styles = [
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;",
            "shape=mxgraph.gcp2.cloud_run;html=1;",
        ]
        pick = _pick_style(styles, "gcp")
        self.assertIsNotNone(pick)
        self.assertIn("mxgraph.gcp2", pick or "")

    def test_pick_style_prefers_azure2_image(self) -> None:
        styles = [
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;",
            "aspect=fixed;image;image=img/lib/azure2/App Services/AppService.svg;",
        ]
        pick = _pick_style(styles, "azure")
        self.assertIsNotNone(pick)
        self.assertIn("img/lib/azure2", pick or "")

    def test_pick_style_prefers_aws4(self) -> None:
        styles = [
            "aspect=fixed;image;image=img/lib/azure2/Compute/VM.svg;",
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.ec2;fillColor=#ED7100;",
        ]
        pick = _pick_style(styles, "aws")
        self.assertIsNotNone(pick)
        self.assertIn("mxgraph.aws4", pick or "")

    def test_prefix_infers_cloud_under_multi_fallback(self) -> None:
        self.assertEqual(_platform_from_shape_type("aws_lambda", "multi"), "aws")
        self.assertEqual(_platform_from_shape_type("gcp_cloud_run", "multi"), "gcp")
        self.assertEqual(_platform_from_shape_type("azure_aks", "multi"), "azure")
        self.assertEqual(_platform_from_shape_type("mystery", "multi"), "aws")

    def test_shape_query_adds_vendor_for_prefixed_types(self) -> None:
        self.assertEqual(_shape_query("gcp_cloud_run", "multi"), "gcp cloud run")
        self.assertEqual(_shape_query("azure_app_service", "multi"), "azure app service")
        self.assertEqual(_shape_query("aws_lambda", "multi"), "lambda")

    def test_shape_query_external_actors(self) -> None:
        self.assertEqual(_shape_query("aws_users", "aws"), "aws4 users")
        self.assertEqual(_shape_query("aws_client", "aws"), "aws4 client")

    def test_builtin_users_style(self) -> None:
        style = _builtin_style("aws_users") or ""
        self.assertIn("resIcon=mxgraph.aws4.users", style)
        self.assertIn("resourceIcon", style)

    def test_pick_style_prefers_users_resource_icon(self) -> None:
        styles = [
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.client_vpn;fillColor=#8C4FFF;",
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.users;fillColor=#232F3D;",
        ]
        pick = _pick_style(styles, "aws", shape_type="aws_users") or ""
        self.assertIn("resIcon=mxgraph.aws4.users", pick)

    def test_platform_from_logical_prefix(self) -> None:
        self.assertEqual(_platform_from_shape_type("logical_actor", "aws"), "logical")
        self.assertEqual(_platform_from_shape_type("logical_datastore", "multi"), "logical")

    def test_shape_query_logical_vocabulary(self) -> None:
        self.assertEqual(_shape_query("logical_actor", "aws"), "uml actor")
        self.assertEqual(_shape_query("logical_datastore", "aws"), "cylinder database")
        self.assertEqual(_shape_query("logical_capability", "aws"), "rounded rectangle application")

    def test_builtin_skips_logical_early(self) -> None:
        # logical_* must reach MCP search; builtins are miss-fallbacks only
        self.assertIsNone(_builtin_style("logical_actor"))
        self.assertIsNone(_builtin_style("logical_capability"))

    def test_pick_style_logical_rejects_cloud_icons(self) -> None:
        styles = [
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;fillColor=#ED7100;",
            "shape=umlActor;fillColor=#E6E6E6;strokeColor=#666666;",
            "rounded=1;whiteSpace=wrap;html=1;fillColor=#DAE8FC;",
        ]
        pick = _pick_style(styles, "logical", shape_type="logical_actor") or ""
        self.assertNotIn("aws4", pick.lower())
        self.assertNotIn("resourceicon", pick.lower())
        self.assertTrue("umlactor" in pick.lower() or "rounded=1" in pick.lower())



class NormalizePlatformStylesTests(unittest.TestCase):
    def test_gcp2020_rewritten_to_gcp2(self) -> None:
        xml = '<mxCell style="shape=mxgraph.gcp2020.cloud_run;html=1;" vertex="1"/>'
        out = normalize_platform_styles(xml, "gcp")
        self.assertIn("mxgraph.gcp2.cloud_run", out)
        self.assertNotIn("gcp2020", out)

    def test_azure_resource_icon_stripped(self) -> None:
        xml = (
            '<mxCell style="shape=mxgraph.azure2.resourceIcon;'
            'resIcon=mxgraph.azure2.appService;html=1;" vertex="1"/>'
        )
        out = normalize_platform_styles(xml, "azure")
        self.assertNotIn("azure2.resourceIcon", out)
        self.assertNotIn("resIcon=mxgraph.azure2", out)

    def test_aws_unchanged(self) -> None:
        xml = '<mxCell style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;"/>'
        self.assertEqual(normalize_platform_styles(xml, "aws"), xml)

    def test_multi_applies_gcp_and_azure_rewrites(self) -> None:
        xml = (
            '<mxCell style="shape=mxgraph.gcp2020.cloud_run;"/>'
            '<mxCell style="shape=mxgraph.azure2.resourceIcon;resIcon=mxgraph.azure2.vm;"/>'
        )
        out = normalize_platform_styles(xml, "multi")
        self.assertIn("mxgraph.gcp2.cloud_run", out)
        self.assertNotIn("gcp2020", out)
        self.assertNotIn("azure2.resourceIcon", out)
        self.assertNotIn("resIcon=mxgraph.azure2", out)


if __name__ == "__main__":
    unittest.main()
