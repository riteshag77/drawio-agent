"""Cross-provider failover for LLM calls.

Wraps ``call_llm`` so a provider outage or exhausted quota degrades to the next
configured provider instead of failing the request. Applied at the ``LlmPort``
boundary, so every caller — primary generation, focused views, ADR/RAID, gap
analysis — inherits it without changing a single call site.

Deliberately narrow about *what* is worth retrying elsewhere:

* **Retried on another provider** — quota/rate limits (429), transient upstream
  failures (502/503/504), and a missing/invalid API key for the chosen provider.
  These are properties of the *provider*, so a different one may well succeed.
* **Not retried** — schema-validation failures (``OutputParserException``).
  Those mean the *prompt or schema* is the problem; every provider would fail
  the same way, so failing over just multiplies cost and latency.

Off unless ``PROVIDER_FALLBACK_ORDER`` is set, so default behaviour is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.exceptions import OutputParserException

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.llm.llm_client import call_llm, is_quota_or_rate_limit_error
from app.adapters.outbound.llm.providers import (
    MissingApiKeyError,
    get_provider,
    normalize_provider_key,
)

if TYPE_CHECKING:
    from app.core.logging import StepLogger

_TRANSIENT_TEXT_MARKERS = (
    "overloaded", "unavailable", "timeout", "timed out",
    "502", "503", "504", "connection error", "connection reset",
)


def should_failover(exc: BaseException) -> bool:
    """Whether ``exc`` is the kind of failure a *different provider* could fix."""
    if isinstance(exc, OutputParserException):
        return False  # the schema/prompt is at fault, not the vendor
    if isinstance(exc, MissingApiKeyError):
        return True
    if is_quota_or_rate_limit_error(exc):
        return True
    text = str(exc).lower()
    cause = exc.__cause__
    if cause is not None:
        text += " " + str(cause).lower()
    return any(marker in text for marker in _TRANSIENT_TEXT_MARKERS)


def resolve_chain(primary: str) -> list[str]:
    """The ordered provider chain for a request: primary first, then fallbacks.

    Fallbacks that are not registered, or that duplicate an earlier entry, are
    dropped here so the retry loop never burns an attempt on an impossible hop.
    """
    primary_key = normalize_provider_key(primary)
    chain = [primary_key]
    for candidate in get_config().provider_fallback_order:
        key = normalize_provider_key(candidate)
        if key in chain:
            continue
        try:
            get_provider(key)
        except Exception:
            continue  # unregistered provider key in config — skip it
        chain.append(key)
    return chain


def call_llm_with_fallback(
    system_prompt: str,
    human_content: str,
    provider: str,
    output_type: type,
    logger: "StepLogger | None" = None,
    attachment_documents: list[dict] | None = None,
) -> Any:
    """``call_llm``, retried across ``PROVIDER_FALLBACK_ORDER`` on vendor failures."""
    chain = resolve_chain(provider)
    last_exc: BaseException | None = None

    for index, provider_key in enumerate(chain):
        try:
            result = call_llm(
                system_prompt, human_content, provider_key, output_type,
                logger, attachment_documents,
            )
            if index > 0 and logger:
                logger.log(
                    "llm_fallback",
                    f"Succeeded on fallback provider '{provider_key}' after "
                    f"'{chain[0]}' failed",
                    level="success",
                    details={"primary": chain[0], "used": provider_key},
                )
            return result
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            last_exc = exc
            is_last = index == len(chain) - 1
            if is_last or not should_failover(exc):
                raise
            if logger:
                logger.log(
                    "llm_fallback",
                    f"Provider '{provider_key}' failed ({exc}) — trying '{chain[index + 1]}'",
                    level="warning",
                    details={"failed_provider": provider_key, "next": chain[index + 1]},
                )

    # Unreachable: the loop either returns or raises. Kept for type-checkers.
    raise last_exc if last_exc else RuntimeError("no provider attempted")
