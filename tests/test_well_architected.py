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


class ServiceKeyDriftTests(unittest.TestCase):
    """Exact keys track the *catalog*; what arrives is whatever the model and
    the MCP resolver produced between them."""

    def test_a_real_alb_is_not_reported_as_missing(self) -> None:
        """Measured on an actual generation: the diagram carried
        `aws_elastic_load_balancing`, the rule listed
        `aws_application_load_balancer`, and the user was told to add the load
        balancer they were looking at — a high finding that flipped the whole
        Reliability pillar to fail."""
        result = assess_well_architected(
            _xml_with_services("aws_elastic_load_balancing", "aws_ec2"), platform="aws",
        )
        reliability = next(p for p in result["pillars"] if p["name"] == "Reliability")
        messages = [f["message"] for f in reliability["findings"]]
        self.assertFalse([m for m in messages if "load balancer" in m.lower()], messages)

    def test_the_finding_still_fires_when_there_is_genuinely_no_balancer(self) -> None:
        result = assess_well_architected(_xml_with_services("aws_ec2"), platform="aws")
        reliability = next(p for p in result["pillars"] if p["name"] == "Reliability")
        messages = [f["message"] for f in reliability["findings"]]
        self.assertTrue([m for m in messages if "load balancer" in m.lower()], messages)

    def test_every_high_severity_rule_carries_markers(self) -> None:
        """A `high` finding is the one a user acts on immediately, so it is the
        one that must not fire on a diagram that already has the control."""
        for pillar, recs in _MISSING_RECOMMENDATIONS.items():
            for rec in recs:
                if rec["severity"] == "high":
                    self.assertTrue(
                        rec.get("markers"),
                        f"{pillar}: {rec['message']!r} is high severity with no markers",
                    )


class TopologyFindingTests(unittest.TestCase):
    """Findings about how the diagram is wired, not what is on it."""

    def _compiled(self, *, db_tier: str = "data", zones: int = 2, protection: str = "tls") -> str:
        from app.adapters.outbound.diagram.compiler.network import compile_network_ir
        from app.domain.ir import (
            AvailabilityZone, CloudBoundary, Flow, Leaf, NetworkIR, Region, Subnet, Vpc,
        )

        az = []
        for z in range(zones):
            subnets = [
                Subnet(id=f"pub{z}", tier="public", leaves=[
                    Leaf(id=f"alb{z}", shape_type="aws_application_load_balancer", label="ALB"),
                ]),
                Subnet(id=f"app{z}", tier="app", leaves=[
                    Leaf(id=f"svc{z}", shape_type="aws_ecs", label="Checkout"),
                ]),
            ]
            db_subnet = next(s for s in subnets if s.tier == db_tier) if db_tier != "data" else None
            if db_subnet is not None:
                db_subnet.leaves.append(Leaf(id=f"db{z}", shape_type="aws_rds", label="Primary DB"))
            else:
                subnets.append(Subnet(id=f"data{z}", tier="data", leaves=[
                    Leaf(id=f"db{z}", shape_type="aws_rds", label="Primary DB"),
                ]))
            az.append(AvailabilityZone(id=f"az{z}", label=f"AZ-{z}", subnets=subnets))

        ir = NetworkIR(
            cloud=CloudBoundary(id="cloud", label="AWS Cloud", regions=[
                Region(id="r", label="us-east-1", vpcs=[Vpc(id="vpc", zones=az)]),
            ]),
            platform="aws",
        )
        ir.flows = [
            Flow(source=f"svc{z}", target=f"db{z}", label="SQL",
                 classification="pii", protection=protection)
            for z in range(zones)
        ]
        return compile_network_ir(ir)

    def _messages(self, xml: str, pillar: str) -> list[str]:
        result = assess_well_architected(xml, platform="aws")
        entry = next(p for p in result["pillars"] if p["name"].startswith(pillar))
        return [f["message"] for f in entry["findings"]]

    def test_datastore_in_a_public_subnet_is_reported_by_name(self) -> None:
        messages = self._messages(self._compiled(db_tier="public"), "Security")
        self.assertTrue(
            [m for m in messages if "Primary DB" in m and "public subnet" in m], messages,
        )

    def test_a_correctly_placed_datastore_produces_no_such_finding(self) -> None:
        messages = self._messages(self._compiled(db_tier="data"), "Security")
        self.assertFalse([m for m in messages if "public subnet" in m], messages)

    def test_unprotected_sensitive_flow_is_reported_with_both_endpoints(self) -> None:
        messages = self._messages(self._compiled(protection="none"), "Security")
        hit = [m for m in messages if "PII" in m and "Checkout" in m and "Primary DB" in m]
        self.assertTrue(hit, messages)

    def test_a_tls_protected_sensitive_flow_is_not_reported(self) -> None:
        messages = self._messages(self._compiled(protection="tls"), "Security")
        self.assertFalse([m for m in messages if "PII" in m], messages)

    def test_single_zone_with_a_datastore_is_a_reliability_finding(self) -> None:
        messages = self._messages(self._compiled(zones=1), "Reliability")
        self.assertTrue([m for m in messages if "Single availability zone" in m], messages)

    def test_two_zones_do_not_trigger_the_single_zone_finding(self) -> None:
        messages = self._messages(self._compiled(zones=2), "Reliability")
        self.assertFalse([m for m in messages if "Single availability zone" in m], messages)

    def test_a_diagram_with_no_tiers_produces_no_topology_findings(self) -> None:
        """Absence of structure is the presence checks' job. Saying it twice in
        two vocabularies is how a report stops being read."""
        messages = self._messages(_xml_with_services("aws_rds", "aws_ec2"), "Security")
        self.assertFalse([m for m in messages if "public subnet" in m], messages)


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
