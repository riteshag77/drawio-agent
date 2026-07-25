"""Rough monthly cloud-architecture spend estimates for the Cost insights panel.

Service rates live in ``config/pricing.json`` (USD list prices + ``usd_to_aud``).
All amounts returned to the UI are in the configured display currency (AUD).
"""

from __future__ import annotations

from typing import Any

from app.adapters.outbound.diagram.xml_tools import find_service_keys
from app.adapters.outbound.llm.pricing import (
    display_currency,
    load_pricing_config,
    usd_to_display,
)


def estimate_monthly_cost(xml: str, platform: str = "aws") -> dict[str, Any]:
    cfg = load_pricing_config()
    services = find_service_keys(xml)
    breakdown: dict[str, list[dict[str, Any]]] = {}
    total = 0.0
    matched = 0
    unmatched = 0
    fallback_aud = usd_to_display(cfg.cloud_fallback_monthly_usd)

    for key in services:
        info = cfg.cloud_services.get(key)
        if info:
            cat = info["category"]
            monthly = usd_to_display(float(info["monthly_usd"]))
            breakdown.setdefault(cat, []).append({
                "key": key, "name": info["name"], "monthly": round(monthly, 2),
            })
            total += monthly
            matched += 1
        else:
            name = key.replace("aws_", "").replace("gcp_", "").replace("azure_", "").replace("_", " ").title()
            breakdown.setdefault("Other", []).append({
                "key": key, "name": name, "monthly": round(fallback_aud, 2),
            })
            total += fallback_aud
            unmatched += 1

    return {
        "total": round(total, 2),
        "currency": display_currency(),
        "period": "monthly",
        "breakdown": breakdown,
        "services_matched": matched,
        "services_unmatched": unmatched,
    }
