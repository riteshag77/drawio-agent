"""Tests for the rule-based compliance assessment."""

from __future__ import annotations

import unittest

from app.adapters.outbound.compliance import assess_compliance


def _xml_with_services(*shape_types: str) -> str:
    cells = "".join(
        f'<mxCell id="n{i}" style="shape_type=\"{st}\";" vertex="1" parent="1" />'
        for i, st in enumerate(shape_types)
    )
    return f'<mxfile><diagram name="A"><mxGraphModel><root>{cells}</root></mxGraphModel></diagram></mxfile>'


class MissingServicesHintTests(unittest.TestCase):
    def test_failing_control_carries_missing_services(self) -> None:
        # RDS present (storage/database category, so encryption_at_rest is
        # in-scope) but no KMS -> the control should fail with a concrete hint.
        xml = _xml_with_services("aws_rds")
        result = assess_compliance(xml, platform="aws", frameworks=["hipaa"])
        hipaa = result["frameworks"]["hipaa"]
        ctrl = next(c for c in hipaa["controls"] if c["id"] == "encryption_at_rest")
        self.assertEqual(ctrl["status"], "fail")
        finding = ctrl["findings"][0]
        self.assertEqual(finding["missing_services"], ["aws_kms"])

    def test_passing_control_has_no_findings(self) -> None:
        xml = _xml_with_services("aws_rds", "aws_kms")
        result = assess_compliance(xml, platform="aws", frameworks=["hipaa"])
        hipaa = result["frameworks"]["hipaa"]
        ctrl = next(c for c in hipaa["controls"] if c["id"] == "encryption_at_rest")
        self.assertEqual(ctrl["status"], "pass")
        self.assertEqual(ctrl["findings"], [])

    def test_missing_services_is_platform_scoped(self) -> None:
        xml = _xml_with_services("gcp_compute_engine")
        result = assess_compliance(xml, platform="gcp", frameworks=["hipaa"])
        hipaa = result["frameworks"]["hipaa"]
        ctrl = next(c for c in hipaa["controls"] if c["id"] == "access_control")
        self.assertEqual(ctrl["status"], "fail")
        finding = ctrl["findings"][0]
        self.assertEqual(finding["missing_services"], ["gcp_iam"])


if __name__ == "__main__":
    unittest.main()
