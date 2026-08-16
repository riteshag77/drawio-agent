"""Tests for the rule-based compliance assessment."""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from app.adapters.outbound.compliance import COMPLIANCE_DIR, _SERVICE_CATEGORIES, assess_compliance

_REFERENCES_DIR = Path(__file__).resolve().parent.parent / "config" / "references"
_CATALOG_FILES = {
    "aws": _REFERENCES_DIR / "aws_stencils.txt",
    "gcp": _REFERENCES_DIR / "gcp-icons-common.txt",
    "azure": _REFERENCES_DIR / "azure-icons-common.txt",
}


def _catalog_shape_types() -> set[str]:
    """Every real `shape_type` key currently in the icon catalogs — mirrors
    the line-parsing logic in compliance.py's own module docstring: skip the
    "no usable icon" prohibition table and any multi-value compound cell."""
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


#: Generic actor placeholders (a client browser, a router, a bare "users"
#: node) — not real cloud services, deliberately excluded from
#: _SERVICE_CATEGORIES even though they're valid shape_type keys.
_NON_SERVICE_CATALOG_KEYS = {
    "aws_client", "aws_mobile_client", "aws_router", "aws_traditional_server",
    "azure_users", "gcp_users",
}


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


class CatalogDriftTests(unittest.TestCase):
    """_SERVICE_CATEGORIES is a hand-maintained mirror of the icon catalogs —
    nothing enforces that it stays in sync when a catalog changes. Confirmed
    live: it had drifted to the point where 72 of 134 real catalog keys
    (54%) were uncategorized and 29 entries referenced key names (aws_alb,
    azure_ad, gcp_kms...) that had been renamed or removed, silently
    dropping those services out of every compliance category check. These
    tests fail loudly the next time a catalog changes without this file
    being updated alongside it, instead of silently degrading."""

    def test_every_real_catalog_key_is_categorized(self) -> None:
        catalog_keys = _catalog_shape_types() - _NON_SERVICE_CATALOG_KEYS
        missing = sorted(catalog_keys - _SERVICE_CATEGORIES.keys())
        self.assertEqual(missing, [], f"{len(missing)} catalog key(s) invisible to compliance categorization")

    def test_no_category_key_references_a_retired_shape_type(self) -> None:
        catalog_keys = _catalog_shape_types()
        stale = sorted(_SERVICE_CATEGORIES.keys() - catalog_keys)
        self.assertEqual(stale, [], f"{len(stale)} compliance category key(s) no longer exist in any catalog")


class ComplianceYamlRequiredServicesTests(unittest.TestCase):
    """The five framework YAMLs' required_services lists name shape_type
    keys directly — same drift risk as _SERVICE_CATEGORIES, but worse when
    it happens: a control whose entire required_services list has gone
    stale fails for every diagram, forever, regardless of what the
    architecture actually contains (confirmed live pre-fix: encryption_at_
    rest[gcp] and access_control[azure] were exactly this, in all 5
    frameworks)."""

    def test_every_required_service_is_a_real_current_key(self) -> None:
        catalog_keys = _catalog_shape_types()
        offenders = []
        for path in sorted(COMPLIANCE_DIR.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for ctrl_id, ctrl in (data.get("controls") or {}).items():
                for platform, keys in (ctrl.get("required_services") or {}).items():
                    bad = [k for k in keys if k not in catalog_keys]
                    if bad:
                        offenders.append(f"{path.name}:{ctrl_id}[{platform}]={bad}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_no_required_services_list_is_empty(self) -> None:
        """An empty list makes _control_status auto-pass (see compliance.py)
        — silently disabling the check rather than evaluating it."""
        offenders = []
        for path in sorted(COMPLIANCE_DIR.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for ctrl_id, ctrl in (data.get("controls") or {}).items():
                for platform, keys in (ctrl.get("required_services") or {}).items():
                    if not keys:
                        offenders.append(f"{path.name}:{ctrl_id}[{platform}]")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_azure_entra_id_satisfies_access_control(self) -> None:
        """The concrete bug: access_control[azure] required the retired
        'azure_ad' key, so a diagram correctly using the current
        'azure_entra_id' identity provider always failed this control."""
        xml = _xml_with_services("azure_entra_id")
        result = assess_compliance(xml, platform="azure", frameworks=["gdpr"])
        ctrl = next(c for c in result["frameworks"]["gdpr"]["controls"] if c["id"] == "access_control")
        self.assertEqual(ctrl["status"], "pass")


if __name__ == "__main__":
    unittest.main()


class TopologyEvidenceTests(unittest.TestCase):
    """Presence is a proxy; the drawing is evidence. "Encryption in Transit"
    used to be answered by "is there a load balancer on the page?" — a diagram
    can carry an ALB and still route cardholder data over an unprotected hop."""

    def _compiled(self, *, db_tier: str, protection: str) -> str:
        from app.adapters.outbound.diagram.compiler.network import compile_network_ir
        from app.domain.ir import (
            AvailabilityZone, CloudBoundary, Flow, Leaf, NetworkIR, Region, Subnet, Vpc,
        )

        subnets = [
            Subnet(id="pub", tier="public", leaves=[
                Leaf(id="alb", shape_type="aws_application_load_balancer", label="ALB"),
                Leaf(id="waf", shape_type="aws_waf", label="WAF"),
            ]),
            Subnet(id="app", tier="app", leaves=[
                Leaf(id="svc", shape_type="aws_ecs", label="Checkout"),
            ]),
        ]
        db = Leaf(id="db", shape_type="aws_rds", label="Cardholder DB")
        if db_tier == "public":
            subnets[0].leaves.append(db)
        else:
            subnets.append(Subnet(id="data", tier="data", leaves=[db]))
        ir = NetworkIR(
            cloud=CloudBoundary(id="cloud", label="AWS Cloud", regions=[
                Region(id="r", label="us-east-1", vpcs=[
                    Vpc(id="vpc", zones=[AvailabilityZone(id="az", label="AZ", subnets=subnets)]),
                ]),
            ]),
            platform="aws",
        )
        ir.flows = [
            Flow(source="alb", target="svc", label="HTTPS", protection="tls"),
            Flow(source="svc", target="db", label="SQL",
                 classification="pci", protection=protection),
        ]
        return compile_network_ir(ir)

    def _control(self, xml: str, control_id: str) -> dict:
        result = assess_compliance(xml, platform="aws", frameworks=["pci_dss"])
        controls = result["frameworks"]["pci_dss"]["controls"]
        return next(c for c in controls if c["id"] == control_id)

    def test_an_unprotected_sensitive_flow_fails_transit_encryption(self) -> None:
        control = self._control(
            self._compiled(db_tier="data", protection="none"), "encryption_in_transit",
        )
        self.assertEqual(control["status"], "fail")
        self.assertEqual(control["assessed_from"], "topology")
        self.assertIn("Cardholder DB", control["evidence"])

    def test_tls_on_every_classified_flow_passes(self) -> None:
        control = self._control(
            self._compiled(db_tier="data", protection="tls"), "encryption_in_transit",
        )
        self.assertEqual(control["status"], "pass")
        self.assertEqual(control["assessed_from"], "topology")

    def test_a_datastore_in_a_public_subnet_fails_segmentation(self) -> None:
        control = self._control(
            self._compiled(db_tier="public", protection="tls"), "network_segmentation",
        )
        self.assertEqual(control["status"], "fail")
        self.assertIn("Cardholder DB", control["evidence"])

    def test_presence_still_decides_when_the_drawing_cannot_answer(self) -> None:
        """An uploaded diagram that never went through this compiler carries no
        tiers and no flow classifications — the control must fall back rather
        than report a pass nobody verified."""
        xml = _xml_with_services("aws_application_load_balancer", "aws_kms", "aws_iam")
        control = self._control(xml, "encryption_in_transit")
        self.assertEqual(control["assessed_from"], "service_presence")
        self.assertEqual(control["evidence"], "")
