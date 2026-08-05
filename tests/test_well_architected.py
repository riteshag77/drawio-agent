"""Tests for the Well-Architected pillar assessment."""

from __future__ import annotations

import unittest

from app.adapters.outbound.well_architected import assess_well_architected


def _xml_with_services(*shape_types: str) -> str:
    cells = "".join(
        f'<mxCell id="n{i}" style="shape_type=\"{st}\";" vertex="1" parent="1" />'
        for i, st in enumerate(shape_types)
    )
    return f'<mxfile><diagram name="A"><mxGraphModel><root>{cells}</root></mxGraphModel></diagram></mxfile>'


class MissingServicesHintTests(unittest.TestCase):
    def test_missing_finding_carries_platform_filtered_services(self) -> None:
        # No WAF/KMS/secrets services anywhere -> "security" pillar should fail
        # and its findings should carry AWS-only missing_services hints.
        xml = _xml_with_services("aws_ec2")
        result = assess_well_architected(xml, platform="aws")
        security = next(p for p in result["pillars"] if p["name"] == "Security")
        waf_finding = next(
            f for f in security["findings"] if "WAF" in f.get("message", "")
        )
        self.assertIn("missing_services", waf_finding)
        self.assertTrue(waf_finding["missing_services"])
        for svc in waf_finding["missing_services"]:
            self.assertTrue(svc.startswith("aws_"), svc)

    def test_missing_services_falls_back_to_cross_cloud_list_for_other_platforms(self) -> None:
        xml = _xml_with_services("gcp_compute_engine")
        result = assess_well_architected(xml, platform="gcp")
        security = next(p for p in result["pillars"] if p["name"] == "Security")
        waf_finding = next(
            f for f in security["findings"] if "WAF" in f.get("message", "")
        )
        # gcp_cloud_armor is the only gcp-prefixed candidate for this rec.
        self.assertIn("gcp_cloud_armor", waf_finding["missing_services"])

    def test_present_service_produces_no_finding_for_that_recommendation(self) -> None:
        xml = _xml_with_services("aws_waf", "aws_kms", "aws_secrets_manager")
        result = assess_well_architected(xml, platform="aws")
        security = next(p for p in result["pillars"] if p["name"] == "Security")
        waf_findings = [f for f in security["findings"] if "WAF" in f.get("message", "")]
        self.assertEqual(waf_findings, [])

    def test_unknown_platform_falls_back_to_aws(self) -> None:
        xml = _xml_with_services("aws_ec2")
        result = assess_well_architected(xml, platform="not-a-real-cloud")
        security = next(p for p in result["pillars"] if p["name"] == "Security")
        waf_finding = next(
            f for f in security["findings"] if "WAF" in f.get("message", "")
        )
        for svc in waf_finding["missing_services"]:
            self.assertTrue(svc.startswith("aws_"), svc)


if __name__ == "__main__":
    unittest.main()
