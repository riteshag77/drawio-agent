"""Generic LangChain invocation with structured-output fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage, SystemMessage

import os

from app.core.config import LLM_TIMEOUT_SECONDS
from app.llm.providers import (
    build_langchain_client,
    get_provider,
    normalize_provider_key,
    require_api_key,
    resolve_gateway_base_url,
)
from app.llm.response_parse import invoke_gateway_json, invoke_json
from app.llm.sdk_client import call_sdk_provider

if TYPE_CHECKING:
    from app.core.logging import StepLogger


def call_llm(
    system_prompt: str,
    human_content: str,
    provider: str,
    output_type: type,
    logger: StepLogger | None = None,
) -> Any:
    """Run a structured LLM request for any registered provider."""
    provider_key = normalize_provider_key(provider)
    spec = get_provider(provider_key)

    if spec.kind == "sdk":
        return call_sdk_provider(spec, system_prompt, human_content, output_type, logger)

    require_api_key(spec)

    if logger:
        logger.log(
            "llm",
            f"Calling {provider_key} ({spec.default_model})",
            details={"provider": provider_key, "model": spec.default_model, "output_type": output_type.__name__},
        )

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]
    method = spec.structured_output

    if method == "none" and spec.base_url_env:
        base_url = resolve_gateway_base_url(spec)
        if logger:
            logger.log(
                "llm",
                "Using gateway HTTP JSON extraction",
                details={"structured_output": method, "base_url": base_url},
            )
        return invoke_gateway_json(
            base_url=base_url,
            api_key=os.getenv(spec.api_key_env, ""),
            model=spec.default_model,
            messages=messages,
            output_type=output_type,
            timeout=LLM_TIMEOUT_SECONDS,
        )

    llm = build_langchain_client(spec, timeout=LLM_TIMEOUT_SECONDS)

    if method == "none":
        if logger:
            logger.log("llm", "Using text JSON extraction", details={"structured_output": method})
        return invoke_json(llm, messages, output_type)

    try:
        kwargs = {"method": "json_mode"} if method == "json_mode" else {}
        if logger:
            logger.log("llm", "Using structured output", details={"structured_output": method or "function_calling"})
        return llm.with_structured_output(output_type, **kwargs).invoke(messages)
    except Exception as exc:
        if logger:
            logger.log(
                "llm",
                f"Structured output failed ({exc}), falling back to text JSON extraction",
                "warn",
                details={"provider": provider_key, "structured_output": method},
            )
        return invoke_json(llm, messages, output_type)
