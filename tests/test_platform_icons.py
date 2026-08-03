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
from app.adapters.outbound.llm.prompts.ir import get_network_ir_system_prompt
from app.adapters.outbound.llm.prompts.system import ESTIMATE_PROMPT_TOKENS


class PlatformPromptTests(unittest.TestCase):
    """IR network prompts: classify packs + filtered shape_type catalog."""

    def test_gcp_prompt_uses_gcp_classify(self) -> None:
        prompt = get_network_ir_system_prompt(platform="gcp", user_hint="cloud run")
        self.assertIn("Service Classification — GCP", prompt)
        self.assertIn("NEVER use mxgraph.aws4", prompt)
        self.assertIn("shape_type", prompt)

    def test_azure_prompt_uses_azure_classify(self) -> None:
        prompt = get_network_ir_system_prompt(platform="azure", user_hint="app service")
        self.assertIn("Service Classification — Azure", prompt)
        self.assertIn("NEVER use mxgraph.aws4", prompt)
        self.assertIn("shape_type", prompt)

    def test_aws_prompt_keeps_aws_classify_and_catalog(self) -> None:
        prompt = get_network_ir_system_prompt(platform="aws", user_hint="lambda")
        self.assertIn("Service Classification — AWS", prompt)
        self.assertIn("shape_type", prompt)
        # Filtered icon catalog still contributes stencil vocabulary for keys.
        self.assertTrue(
            "mxgraph.aws4" in prompt or "Lambda" in prompt or "lambda" in prompt.lower(),
            "expected AWS catalog or Lambda hint in prompt",
        )

    def test_unknown_platform_falls_back_to_aws(self) -> None:
        prompt = get_network_ir_system_prompt(platform="digitalocean", user_hint="lambda")
        self.assertIn("Service Classification — AWS", prompt)

    def test_multi_prompt_is_slim_and_prefix_aware(self) -> None:
        prompt = get_network_ir_system_prompt(platform="multi", user_hint="lambda cloud run app service")
        self.assertIn("Service Classification — Multi-cloud", prompt)
        self.assertNotIn("| Compute | #ED7100 |", prompt)
        self.assertNotIn("aws-icons-compute", prompt)
        tokens = ESTIMATE_PROMPT_TOKENS(prompt)
        self.assertLess(tokens, 12_000, f"multi IR prompt ~{tokens} tokens too heavy")



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

    def test_builtin_resolves_logical_early(self) -> None:
        # People/portals use AWS4 icons (reliable in iframe); apps stay rounded tiles
        actor = _builtin_style("logical_actor") or ""
        cap = _builtin_style("logical_capability") or ""
        self.assertIn("mxgraph.aws4.users", actor)
        self.assertIn("verticalLabelPosition=bottom", actor)
        self.assertIn("DAE8FC", cap)
        self.assertIn("verticalLabelPosition=middle", cap)
        api = _builtin_style("logical_api") or ""
        self.assertIn("rounded=1", api)
        self.assertIn("E1D5E7", api)
        self.assertIn("verticalAlign=middle", api)

    def test_pick_style_logical_allows_users_client_icons(self) -> None:
        styles = [
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.lambda;fillColor=#ED7100;",
            "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.users;fillColor=#232F3D;",
            "rounded=1;whiteSpace=wrap;html=1;fillColor=#DAE8FC;",
        ]
        pick = _pick_style(styles, "logical", shape_type="logical_actor") or ""
        self.assertIn("aws4.users", pick.lower())
        # Non-people roles still reject random cloud icons
        pick2 = _pick_style(styles, "logical", shape_type="logical_capability") or ""
        self.assertNotIn("lambda", pick2.lower())
        self.assertIn("rounded=1", pick2.lower())

    def test_builtin_resolves_security_partner_org(self) -> None:
        style = _builtin_style("security_partner_org") or ""
        self.assertIn("rounded=1", style)
        self.assertIn("strokeWidth=3", style)



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
