"""LlmPort — the dependency-inversion boundary between core and the LLM adapter.

Core services call ``get_llm()`` to obtain an ``LlmPort`` implementation,
which defaults to the real ``llm_client.call_llm`` wrapped in a thin adapter.
In tests, pass a fake implementation via the ``llm`` kwarg to ``generate_xml_diagram``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.core.logging import StepLogger


@runtime_checkable
class LlmPort(Protocol):
    """Minimal interface the core needs from any LLM backend."""

    def __call__(
        self,
        system_prompt: str,
        human_content: str,
        provider: str,
        output_type: type,
        logger: "StepLogger | None",
        attachment_documents: list[dict] | None,
    ) -> Any:
        ...


def get_llm() -> LlmPort:
    """Return the default LLM adapter (lazy import — keeps adapters out of core import path).

    Wrapped in cross-provider failover. With ``PROVIDER_FALLBACK_ORDER`` unset
    (the default) this is a pass-through to ``call_llm`` — the chain is just the
    requested provider — so behaviour is unchanged unless failover is configured.
    """
    from app.adapters.outbound.llm.fallback import call_llm_with_fallback  # noqa: PLC0415
    return call_llm_with_fallback  # type: ignore[return-value]
