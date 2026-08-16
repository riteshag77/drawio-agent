"""Tests for the Well-Architected pillar assessment."""

from __future__ import annotations

import unittest
from pathlib import Path

from app.adapters.outbound.well_architected import (
    COST_SERVICES,
    OPS_SERVICES,
    PERFORMANCE_SERVICES,
    RELIABILITY_SERVICES,
    SECURITY_SERVICES,
    SUSTAINABILITY_SERVICES,
    _MISSING_RECOMMENDATIONS,
    assess_well_architected,
)

_REFERENCES_DIR = Path(__file__).resolve().parent.parent / "config" / "references"
_CATALOG_FILES = {
    "aws": _REFERENCES_DIR / "aws_stencils.txt",
    "gcp": _REFERENCES_DIR / "gcp-icons-common.txt",
    "azure": _REFERENCES_DIR / "azure-icons-common.txt",
}


def _xml_with_services(*shape_types: str) -> str:
    cells = "".join(
        f'<mxCell id="n{i}" style="shape_type=\"{st}\";" vertex="1" parent="1" />'
        for i, st in enumerate(shape_types)
    )
    return f'<mxfile><diagram name="A"><mxGraphModel><root>{cells}</root></mxGraphModel></diagram></mxfile>'


def _catalog_shape_types() -> set[str]:
    """Every real `shape_type` key currently in the icon catalogs — see
    test_compliance.py's helper of the same name for the parsing logic."""
    keys: set[str] = set()
    for platform, path in _CATALOG_FILES.items():
        in_broken_table = False
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("##"):
                in_broken_table = "no usable icon" in line.lower()
                continue
            if in_broken_table or not line.startswith("|") or set(line) <= set("|-: "):
                continue
            for cell in (c.strip() for c in line.strip("|").split("|")):
                if cell.startswith(f"{platform}_") and "," not in cell:
                    keys.add(cell)
    return keys


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


class PlatformApplicabilityTests(unittest.TestCase):
    """A rule this platform cannot satisfy is not applicable, not a failure."""

    def _ops_findings(self, xml: str, platform: str) -> list[dict]:
        result = assess_well_architected(xml, platform=platform)
        ops = next(p for p in result["pillars"] if p["name"].startswith("Operational"))
        return ops["findings"]

    def test_gcp_audit_logging_is_not_reported_as_a_missing_aws_service(self) -> None:
        """The rule listed only ``aws_cloudtrail``, so a GCP diagram *carrying
        Cloud Logging* was told "No audit logging detected (AWS) — Enable
        CloudTrail": a failure naming a service that cannot exist on GCP."""
        xml = _xml_with_services(
            "gcp_cloud_load_balancing", "gcp_cloud_run",
            "gcp_cloud_monitoring", "gcp_cloud_logging",
        )
        messages = [f["message"] for f in self._ops_findings(xml, "gcp")]
        self.assertFalse([m for m in messages if "audit" in m.lower()], messages)

    def test_the_same_gap_is_still_reported_on_aws(self) -> None:
        messages = [f["message"] for f in self._ops_findings(_xml_with_services("aws_ec2"), "aws")]
        self.assertTrue([m for m in messages if "audit" in m.lower()], messages)

    def test_an_equivalent_from_another_cloud_still_satisfies_a_rule(self) -> None:
        """Applicability decides whether the question is asked; any listed
        equivalent answers it. Failing an `aws` diagram that fronts its app
        with Cloud Armor would be a house-style opinion, not a WAF finding."""
        result = assess_well_architected(_xml_with_services("gcp_cloud_armor"), platform="aws")
        security = next(p for p in result["pillars"] if p["name"] == "Security")
        messages = [f["message"] for f in security["findings"]]
        self.assertFalse([m for m in messages if "WAF" in m], messages)

    def test_multi_cloud_keeps_every_rule_in_scope(self) -> None:
        before = len(self._ops_findings(_xml_with_services("aws_ec2"), "multi"))
        self.assertGreaterEqual(before, 2)


class KmsVsCloudArmorRegressionTests(unittest.TestCase):
    """Confirmed live: the "encryption key management" recommendation's
    services list was ["aws_kms", "azure_key_vault", "gcp_cloud_armor"] — a
    copy-paste of the WAF finding directly above it. Cloud Armor is a WAF/
    DDoS service, not encryption, so a GCP diagram with Cloud Armor but no
    KMS silently passed this check, and one with real KMS but no Cloud
    Armor never could pass it at all."""

    def test_gcp_cloud_armor_alone_does_not_satisfy_the_kms_check(self) -> None:
        xml = _xml_with_services("gcp_compute_engine", "gcp_cloud_armor")
        result = assess_well_architected(xml, platform="gcp")
        security = next(p for p in result["pillars"] if p["name"] == "Security")
        kms_findings = [f for f in security["findings"] if "key management" in f.get("message", "")]
        self.assertEqual(len(kms_findings), 1)
        self.assertNotIn("gcp_cloud_armor", kms_findings[0]["missing_services"])

    def test_gcp_cloud_kms_alone_satisfies_the_kms_check(self) -> None:
        xml = _xml_with_services("gcp_compute_engine", "gcp_cloud_kms")
        result = assess_well_architected(xml, platform="gcp")
        security = next(p for p in result["pillars"] if p["name"] == "Security")
        kms_findings = [f for f in security["findings"] if "key management" in f.get("message", "")]
        self.assertEqual(kms_findings, [])


class CatalogDriftTests(unittest.TestCase):
    """The six pillar service sets and _MISSING_RECOMMENDATIONS are a
    hand-maintained mirror of the icon catalogs — same drift risk as
    compliance.py's _SERVICE_CATEGORIES and pricing.json's cloud_services
    (see their own CatalogDriftTests). Confirmed live: this file referenced
    aws_alb/aws_elb/aws_nlb/azure_lb/azure_agw/gcp_lb/azure_blob/azure_aci/
    gcp_storage — none of which exist in any current catalog — so a
    diagram's real load balancer, storage account, or container-instances
    leaf never counted as "good practice detected" for any pillar."""

    def test_no_pillar_service_references_a_retired_shape_type(self) -> None:
        catalog_keys = _catalog_shape_types()
        all_pillar_keys = (
            SECURITY_SERVICES | RELIABILITY_SERVICES | PERFORMANCE_SERVICES
            | COST_SERVICES | OPS_SERVICES | SUSTAINABILITY_SERVICES
        )
        stale = sorted(all_pillar_keys - catalog_keys)
        self.assertEqual(stale, [], f"{len(stale)} pillar service key(s) no catalog has")

    def test_no_recommendation_service_references_a_retired_shape_type(self) -> None:
        catalog_keys = _catalog_shape_types()
        rec_keys: set[str] = set()
        for recs in _MISSING_RECOMMENDATIONS.values():
            for rec in recs:
                rec_keys |= set(rec["services"])
        stale = sorted(rec_keys - catalog_keys)
        self.assertEqual(stale, [], f"{len(stale)} recommendation service key(s) no catalog has")


if __name__ == "__main__":
    unittest.main()
