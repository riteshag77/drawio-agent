"""Rough monthly cloud-architecture spend estimates for the Cost insights panel.

Service rates live in ``config/pricing.json`` (USD list prices + ``usd_to_aud``).
All amounts returned to the UI are in the configured display currency (AUD).
"""

from __future__ import annotations

from typing import Any

from app.adapters.outbound.diagram.xml_diagnostics import _ACTOR_SHAPE_TYPES
from app.adapters.outbound.diagram.xml_tools import count_service_keys
from app.adapters.outbound.llm.pricing import (
    display_currency,
    load_pricing_config,
    usd_to_display,
)

#: find_service_keys returns every vendor-prefixed key it finds, including
#: generic diagram actors that aren't billed cloud resources at all — a
#: client browser, an on-prem server, a router, a bare "Users" node.
#: Confirmed live: a compiled AWS diagram's Users actor carries
#: shape_type="aws_user" (an internal style-resolution key, never listed
#: in the LLM-facing aws_stencils.txt catalog since it's assigned from an
#: external_actor's `kind`, not chosen by the model) — it fell through to
#: the flat fallback rate and inflated the total for any diagram showing
#: the (extremely common) "Users -> ... -> Cloud" journey, on every
#: platform. Built on xml_diagnostics.py's own canonical actor-key set
#: (_is_external_actor_cell's data: aws_internet, aws_iot, on_prem, singular
#: azure_user/gcp_user and more) rather than duplicating it outright — but
#: that list is scoped to actor icons specifically and doesn't cover the
#: aws_stencils.txt "Generic / non-AWS actors" entries for a router or an
#: on-prem server, so those two are added explicitly rather than assuming
#: either single source is complete on its own.
_NON_BILLED_ACTOR_KEYS = _ACTOR_SHAPE_TYPES | {"aws_router", "aws_traditional_server"}


def estimate_monthly_cost(xml: str, platform: str = "aws") -> dict[str, Any]:
    cfg = load_pricing_config()
    counts = {
        key: n for key, n in count_service_keys(xml).items()
        if key not in _NON_BILLED_ACTOR_KEYS
    }
    breakdown: dict[str, list[dict[str, Any]]] = {}
    total = 0.0
    matched = 0
    unmatched = 0
    fallback_aud = usd_to_display(cfg.cloud_fallback_monthly_usd)

    for key, quantity in counts.items():
        info = cfg.cloud_services.get(key)
        if info:
            cat = info["category"]
            each = usd_to_display(float(info["monthly_usd"]))
            name = info["name"]
        else:
            cat = "Other"
            each = fallback_aud
            name = key.replace("aws_", "").replace("gcp_", "").replace("azure_", "").replace("_", " ").title()
        line_total = each * quantity
        entry: dict[str, Any] = {
            "key": key, "name": name, "monthly": round(line_total, 2),
        }
        # Quantity is only surfaced when it is more than one, so the common
        # single-instance line reads exactly as it always did.
        if quantity > 1:
            entry["quantity"] = quantity
            entry["monthly_each"] = round(each, 2)
            entry["name"] = f"{name} x{quantity}"
        breakdown.setdefault(cat, []).append(entry)
        total += line_total
        if info:
            matched += 1
        else:
            unmatched += 1

    return {
        "total": round(total, 2),
        "currency": display_currency(),
        "period": "monthly",
        "breakdown": breakdown,
        "services_matched": matched,
        "services_unmatched": unmatched,
        "instances_counted": sum(counts.values()),
    }
