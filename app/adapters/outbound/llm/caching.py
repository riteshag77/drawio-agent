"""Provider-aware system-message construction with prompt-cache breakpoints.

The system prompt assembled by ``prompts.system.get_xml_gen_system_prompt_marked``
carries a ``CACHE_BREAKPOINT`` sentinel dividing the stable (cacheable) prefix
from the per-request volatile suffix. This module turns that marked string into
the right ``SystemMessage`` shape for each provider's caching strategy:

- ``anthropic`` → structured content blocks with an explicit
  ``cache_control: {"type": "ephemeral"}`` breakpoint on the stable prefix
  (ChatAnthropic passes these through to the Anthropic API).
- ``auto`` → provider does native prefix caching (e.g. OpenAI); send a plain
  string with the sentinel stripped and prefix stability preserved.
- ``none`` → no caching; plain string, sentinel stripped.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import SystemMessage

from app.adapters.outbound.llm.prompts.system import CACHE_BREAKPOINT

if TYPE_CHECKING:
    from app.adapters.outbound.llm.providers import ProviderSpec


def build_system_message(spec: "ProviderSpec", system_prompt: str) -> SystemMessage:
    """Build a ``SystemMessage`` honoring the provider's prompt-cache strategy."""
    stable, sep, volatile = system_prompt.partition(CACHE_BREAKPOINT)
    if not sep:
        # No breakpoint marker (e.g. the Mermaid path) — single plain block.
        return SystemMessage(content=system_prompt)

    mode = getattr(spec, "prompt_cache", "none")
    if mode == "anthropic":
        blocks: list[dict[str, Any]] = [
            {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
        ]
        if volatile.strip():
            blocks.append({"type": "text", "text": volatile})
        return SystemMessage(content=blocks)

    # "auto" (native prefix caching) or "none": send a plain string; the stable
    # prefix keeps its byte-for-byte position, which is all native caching needs.
    return SystemMessage(content=stable + volatile)
