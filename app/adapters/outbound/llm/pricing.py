"""Approximate LLM API pricing for the generation token/cost estimate shown to users.

Static USD-per-1M-token table, in the same spirit as ``cost_estimator.py``'s
cloud-infrastructure pricing: a clearly-labeled estimate for the UI, not a
billing source of truth. Verify against each provider's current pricing page
before trusting exact numbers — these drift as providers update pricing.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float


# Only the providers with a statically-known default model (providers.py) are
# priced here. openrouter/opencode/bazaarlink resolve their actual model from
# an env var at runtime, so a fixed price can't be mapped to them in source.
_PRICING: dict[str, ModelPrice] = {
    "gpt-4o": ModelPrice(input_per_million=2.50, output_per_million=10.00),
    "claude-sonnet-4-6": ModelPrice(input_per_million=3.00, output_per_million=15.00),
    "gemini-2.5-flash": ModelPrice(input_per_million=0.30, output_per_million=2.50),
    "kimi-k3": ModelPrice(input_per_million=3.00, output_per_million=15.00),
}


def estimate_cost_usd(model_name: str, input_tokens: int, output_tokens: int) -> float | None:
    """Approximate USD cost for one call, or None if this model has no pricing entry."""
    if not model_name:
        return None
    price = _PRICING.get(model_name)
    if price is None:
        # Providers often report a dated snapshot id (e.g. "gpt-4o-2024-08-06")
        # rather than the plain alias — fall back to a prefix match.
        for key, candidate in _PRICING.items():
            if model_name.startswith(key):
                price = candidate
                break
    if price is None:
        return None
    return (input_tokens / 1_000_000) * price.input_per_million + (output_tokens / 1_000_000) * price.output_per_million
