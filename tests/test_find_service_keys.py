"""Regression: find_service_keys — shared by cost_estimator.py, well_architected.py,
and compliance.py — must detect services from BOTH a raw shape_type attribute
AND an already-MCP-resolved style, in the same document, and must normalize
the two spellings the IR/compiler vocabulary and the static service configs
disagree on for load balancers.

Found via a live capture: a surgical edit added an ALB node, MCP resolved and
styled it (correctly stripping its shape_type per apply_mcp_styles), but
well_architected.py still reported "No load balancer detected" — the old
either/or fallback (`if not keys: <scan styles>`) never ran the style scan at
all once ANY other cell on the page still carried a raw shape_type attribute,
and even when it did run, the resolved stencil name
("application_load_balancer") never matched the short form ("aws_alb") the
static configs check for.
"""

from __future__ import annotations

import unittest

from app.adapters.outbound.diagram.xml_tools import find_service_keys


class FindServiceKeysTests(unittest.TestCase):
    def test_detects_from_shape_type_attribute(self) -> None:
        xml = '<mxCell id="a" shape_type="aws_rds" vertex="1"/>'
        self.assertIn("aws_rds", find_service_keys(xml))

    def test_detects_from_resolved_style_when_no_shape_type_anywhere(self) -> None:
        xml = (
            '<mxCell id="a" style="shape=mxgraph.aws4.resourceIcon;'
            'resIcon=mxgraph.aws4.lambda;" vertex="1"/>'
        )
        self.assertIn("aws_lambda", find_service_keys(xml))

    def test_detects_both_sources_in_the_same_mixed_document(self) -> None:
        """The exact real-world shape after a surgical edit: some leaves still
        carry a raw shape_type (unresolved), others have already been
        MCP-resolved and had shape_type stripped."""
        xml = (
            '<mxCell id="a" shape_type="aws_rds" vertex="1"/>'
            '<mxCell id="b" style="shape=mxgraph.aws4.resourceIcon;'
            'resIcon=mxgraph.aws4.lambda;" vertex="1"/>'
        )
        keys = find_service_keys(xml)
        self.assertIn("aws_rds", keys)
        self.assertIn("aws_lambda", keys)

    def test_normalizes_long_form_load_balancer_stencil_names(self) -> None:
        xml = (
            '<mxCell id="alb" style="shape=mxgraph.aws4.application_load_balancer;'
            'fillColor=#8C4FFF;" vertex="1"/>'
        )
        keys = find_service_keys(xml)
        self.assertIn("aws_alb", keys)
        self.assertNotIn("aws_application_load_balancer", keys)

    def test_normalizes_network_load_balancer_too(self) -> None:
        xml = (
            '<mxCell id="nlb" style="shape=mxgraph.aws4.network_load_balancer;" vertex="1"/>'
        )
        self.assertIn("aws_nlb", find_service_keys(xml))

    def test_no_duplicate_keys_when_both_sources_agree(self) -> None:
        xml = '<mxCell id="a" shape_type="aws_s3" vertex="1"/>'
        keys = find_service_keys(xml)
        self.assertEqual(keys.count("aws_s3"), 1)

    def test_normalizes_route53_underscore_variant(self) -> None:
        xml = '<mxCell id="a" style="shape=mxgraph.aws4.productIcon;prIcon=mxgraph.aws4.route_53;" vertex="1"/>'
        self.assertIn("aws_route53", find_service_keys(xml))

    def test_normalizes_elasticache_cache_node_stencil(self) -> None:
        xml = '<mxCell id="a" style="shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.cache_node;" vertex="1"/>'
        self.assertIn("aws_elasticache", find_service_keys(xml))

    def test_normalizes_cloudwatch_alarm_stencil(self) -> None:
        xml = '<mxCell id="a" style="shape=mxgraph.aws4.alarm;" vertex="1"/>'
        self.assertIn("aws_cloudwatch", find_service_keys(xml))

    def test_normalizes_azure_load_balancer(self) -> None:
        xml = '<mxCell id="a" style="shape=mxgraph.azure2.load_balancer;" vertex="1"/>'
        self.assertIn("azure_lb", find_service_keys(xml))

    def test_normalizes_azure_application_gateway(self) -> None:
        xml = '<mxCell id="a" style="shape=mxgraph.azure2.application_gateway;" vertex="1"/>'
        self.assertIn("azure_agw", find_service_keys(xml))

    def test_normalizes_gcp_cloud_load_balancing(self) -> None:
        xml = '<mxCell id="a" style="shape=mxgraph.gcp2.cloud_load_balancing;" vertex="1"/>'
        self.assertIn("gcp_lb", find_service_keys(xml))

    def test_stencil_wrapper_name_is_not_treated_as_a_service(self) -> None:
        """resourceIcon/productIcon are the stencil-wrapper prefix, not a
        service — matching them as if they were one is a false positive that
        showed up in a live capture (aws_resourceicon, aws_producticon)."""
        xml = (
            '<mxCell id="a" style="shape=mxgraph.aws4.resourceIcon;'
            'resIcon=mxgraph.aws4.kms;" vertex="1"/>'
            '<mxCell id="b" style="shape=mxgraph.aws4.productIcon;'
            'prIcon=mxgraph.aws4.sqs;" vertex="1"/>'
        )
        keys = find_service_keys(xml)
        self.assertNotIn("aws_resourceicon", keys)
        self.assertNotIn("aws_producticon", keys)
        self.assertIn("aws_kms", keys)
        self.assertIn("aws_sqs", keys)


if __name__ == "__main__":
    unittest.main()
