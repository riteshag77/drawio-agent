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
    """Return the default LLM adapter (lazy import — keeps adapters out of core import path)."""
    from app.adapters.outbound.llm.llm_client import call_llm  # noqa: PLC0415
    return call_llm  # type: ignore[return-value]
