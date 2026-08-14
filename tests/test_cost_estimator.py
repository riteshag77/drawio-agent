"""Tests for the rough monthly cost estimator."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.adapters.outbound.cost_estimator import _NON_BILLED_ACTOR_KEYS, estimate_monthly_cost

_REFERENCES_DIR = Path(__file__).resolve().parent.parent / "config" / "references"
_PRICING_PATH = Path(__file__).resolve().parent.parent / "config" / "pricing.json"
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
    """Every real `shape_type` key currently in the icon catalogs — mirrors
    the line-parsing logic in test_compliance.py's own helper of the same
    name, kept separate rather than shared since the two test modules should
    be able to evolve independently."""
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


class MonthlyCostTests(unittest.TestCase):
    def test_matched_service_uses_its_own_price(self) -> None:
        xml = _xml_with_services("aws_rds")
        result = estimate_monthly_cost(xml, platform="aws")
        self.assertEqual(result["services_matched"], 1)
        self.assertEqual(result["services_unmatched"], 0)
        self.assertGreater(result["total"], 0)

    def test_unmatched_real_service_falls_back_but_is_counted(self) -> None:
        # A genuinely unknown key (not in pricing.json, not an actor placeholder)
        # still gets a rough estimate rather than vanishing from the total.
        xml = _xml_with_services("aws_some_brand_new_service_not_yet_priced")
        result = estimate_monthly_cost(xml, platform="aws")
        self.assertEqual(result["services_unmatched"], 1)
        self.assertGreater(result["total"], 0)
        self.assertIn("Other", result["breakdown"])

    def test_generic_actor_placeholders_are_excluded_entirely(self) -> None:
        """Confirmed live pre-fix: a diagram actor (a client browser, an
        on-prem server) isn't a billed cloud resource, but fell through to
        the flat fallback rate and inflated the total anyway — for the
        extremely common "Users -> ... -> Cloud" journey shape."""
        with_actor = estimate_monthly_cost(_xml_with_services("aws_rds", "aws_client"), platform="aws")
        without_actor = estimate_monthly_cost(_xml_with_services("aws_rds"), platform="aws")
        self.assertEqual(with_actor["total"], without_actor["total"])
        self.assertEqual(with_actor["services_matched"], without_actor["services_matched"])
        self.assertEqual(with_actor["services_unmatched"], without_actor["services_unmatched"])


class PricingCatalogDriftTests(unittest.TestCase):
    """config/pricing.json's cloud_services dict is a hand-maintained mirror
    of the icon catalogs, same drift risk as compliance.py's
    _SERVICE_CATEGORIES (see tests/test_compliance.py::CatalogDriftTests).
    Confirmed live: 70 of 143 real catalog keys (49%) had no price entry —
    including very common services (IAM, an Application Load Balancer, a
    NAT Gateway, Azure's actual identity key) — and 18 entries referenced
    key names (aws_alb, azure_sql, gcp_lb...) that no longer exist in any
    catalog, both silently degrading a "matched" cost estimate into a rough
    flat-rate guess."""

    def test_every_real_billable_catalog_key_has_a_price(self) -> None:
        cfg = json.loads(_PRICING_PATH.read_text(encoding="utf-8"))
        pricing_keys = set(cfg["cloud_services"].keys())
        catalog_keys = _catalog_shape_types() - _NON_BILLED_ACTOR_KEYS
        missing = sorted(catalog_keys - pricing_keys)
        self.assertEqual(missing, [], f"{len(missing)} catalog key(s) with no price entry")

    def test_no_price_entry_references_a_retired_shape_type(self) -> None:
        cfg = json.loads(_PRICING_PATH.read_text(encoding="utf-8"))
        pricing_keys = set(cfg["cloud_services"].keys())
        catalog_keys = _catalog_shape_types()
        stale = sorted(pricing_keys - catalog_keys)
        self.assertEqual(stale, [], f"{len(stale)} price entry/entries reference a key no catalog has")


if __name__ == "__main__":
    unittest.main()
