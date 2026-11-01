"""Approximate LLM API pricing for the generation token/cost estimate shown to users.

Rates live in ``config/pricing.json`` (USD list prices + ``usd_to_aud``). Estimates
returned to the UI are always in the configured display currency (AUD). Same spirit
as ``cost_estimator.py``: a clearly-labeled estimate, not a billing source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.adapters.outbound.config.env_config import EnvConfigAdapter

PRICING_CONFIG_PATH: Path = EnvConfigAdapter.base_dir() / "config" / "pricing.json"

_DEFAULT_USD_TO_AUD = 1.55
_DEFAULT_CURRENCY = "AUD"
_DEFAULT_SYMBOL = "A$"


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """USD list price per 1M tokens (converted to AUD at estimate time)."""

    input_per_million: float
    output_per_million: float


@dataclass(frozen=True, slots=True)
class PricingConfig:
    display_currency: str
    display_symbol: str
    usd_to_aud: float
    models: dict[str, ModelPrice]
    cloud_services: dict[str, dict[str, Any]]
    cloud_fallback_monthly_usd: float


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@lru_cache(maxsize=1)
def load_pricing_config() -> PricingConfig:
    """Load ``config/pricing.json`` once per process."""
    raw: dict[str, Any] = {}
    try:
        raw = json.loads(PRICING_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}

    models: dict[str, ModelPrice] = {}
    for key, row in (raw.get("llm_models") or {}).items():
        if not isinstance(row, dict):
            continue
        inp = row.get("input_per_million_usd", row.get("input_per_million"))
        out = row.get("output_per_million_usd", row.get("output_per_million"))
        if inp is None or out is None:
            continue
        models[str(key).strip().lower()] = ModelPrice(
            input_per_million=_as_float(inp, 0.0),
            output_per_million=_as_float(out, 0.0),
        )

    cloud: dict[str, dict[str, Any]] = {}
    for key, row in (raw.get("cloud_services") or {}).items():
        if not isinstance(row, dict):
            continue
        monthly = row.get("monthly_usd", row.get("monthly"))
        if monthly is None:
            continue
        cloud[str(key)] = {
            "name": row.get("name") or key,
            "monthly_usd": _as_float(monthly, 0.0),
            "category": row.get("category") or "Other",
        }

    return PricingConfig(
        display_currency=str(raw.get("display_currency") or _DEFAULT_CURRENCY).upper(),
        display_symbol=str(raw.get("display_symbol") or _DEFAULT_SYMBOL),
        usd_to_aud=max(_as_float(raw.get("usd_to_aud"), _DEFAULT_USD_TO_AUD), 0.01),
        models=models,
        cloud_services=cloud,
        cloud_fallback_monthly_usd=_as_float(
            raw.get("cloud_fallback_monthly_usd", raw.get("cloud_fallback_monthly")),
            15.0,
        ),
    )


def usd_to_display(amount_usd: float) -> float:
    """Convert a USD amount into the configured display currency (AUD)."""
    cfg = load_pricing_config()
    return amount_usd * cfg.usd_to_aud


def display_currency() -> str:
    return load_pricing_config().display_currency


def display_symbol() -> str:
    return load_pricing_config().display_symbol


def _gemini_family_price(name: str, pricing: dict[str, ModelPrice]) -> ModelPrice | None:
    """Map Gemini ids whose version sits between family tokens (e.g. gemini-3.1-flash-lite)."""
    if "gemini" not in name:
        return None
    if "flash-lite" in name or "flash_lite" in name:
        if "2.5" in name:
            return pricing.get("gemini-2.5-flash-lite") or pricing.get("gemini-flash-lite")
        return pricing.get("gemini-3.1-flash-lite") or pricing.get("gemini-flash-lite")
    if "flash" in name:
        if "3.5" in name:
            return pricing.get("gemini-3.5-flash") or pricing.get("gemini-flash")
        if "gemini-3" in name or "-3-" in name or "-3." in name:
            return pricing.get("gemini-3-flash") or pricing.get("gemini-flash")
        return pricing.get("gemini-2.5-flash") or pricing.get("gemini-flash")
    if "pro" in name:
        if "gemini-3" in name or "-3-" in name or "-3." in name:
            return pricing.get("gemini-3.1-pro") or pricing.get("gemini-pro")
        return pricing.get("gemini-2.5-pro") or pricing.get("gemini-pro")
    return None


def _lookup_price(model_name: str) -> ModelPrice | None:
    pricing = load_pricing_config().models
    name = (model_name or "").strip().lower()
    if not name or not pricing:
        return None
    # Strip vendor prefixes like "openai/" or "anthropic/" from OpenRouter ids,
    # and Google's "models/" resource prefix.
    bare = name.split("/")[-1]
    if bare.startswith("models:"):
        bare = bare.split(":", 1)[-1]

    if name in pricing:
        return pricing[name]
    if bare in pricing:
        return pricing[bare]

    # Longer keys first so "gpt-4o-mini" / "gemini-3.1-flash-lite" win over shorter siblings.
    for key, candidate in sorted(pricing.items(), key=lambda kv: len(kv[0]), reverse=True):
        if name.startswith(key) or bare.startswith(key) or key in name or key in bare:
            return candidate

    return _gemini_family_price(bare, pricing) or _gemini_family_price(name, pricing)


def estimate_cost_aud(model_name: str, input_tokens: int, output_tokens: int) -> float | None:
    """Approximate AUD cost for one call, or None if this model has no pricing entry."""
    price = _lookup_price(model_name)
    if price is None:
        return None
    usd = (input_tokens / 1_000_000) * price.input_per_million + (
        output_tokens / 1_000_000
    ) * price.output_per_million
    return usd_to_display(usd)


# Back-compat alias — callers should prefer estimate_cost_aud.
estimate_cost_usd = estimate_cost_aud
