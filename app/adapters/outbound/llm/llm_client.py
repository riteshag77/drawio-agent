"""Generic LangChain invocation via structured output."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks.usage import UsageMetadataCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.llm.pricing import estimate_cost_usd
from app.adapters.outbound.llm.providers import (
    build_langchain_client,
    get_provider,
    normalize_provider_key,
    require_api_key,
)
if TYPE_CHECKING:
    from app.core.logging import StepLogger


def _truncate(text: str, max_chars: int = 5000) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + f"... (truncated, {len(text)} total chars)"


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
    require_api_key(spec)

    if logger:
        logger.log(
            "llm",
            f"Calling {provider_key} ({spec.default_model})",
            details={"provider": provider_key, "model": spec.default_model, "output_type": output_type.__name__},
        )
        logger.log("llm_input", "LLM request", details={
            "system_prompt": _truncate(system_prompt),
            "human_content": _truncate(human_content),
        })

    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]
    method = spec.structured_output
    timeout = get_config().llm_timeout_seconds
    llm = build_langchain_client(spec, timeout=timeout)

    kwargs = {"method": "json_mode"} if method == "json_mode" else {}
    if logger:
        logger.log("llm", "Using structured output", details={"structured_output": method or "function_calling"})
    usage_handler = UsageMetadataCallbackHandler()
    structured_llm = llm.with_structured_output(output_type, **kwargs)
    try:
        result = structured_llm.invoke(messages, config={"callbacks": [usage_handler]})
    except OutputParserException as exc:
        if logger:
            logger.log("llm", f"Structured output failed validation, retrying once: {exc}", level="warning")
        schema = json.dumps(output_type.model_json_schema(), ensure_ascii=False)
        corrective = HumanMessage(content=(
            "Your previous response failed schema validation: " + _truncate(str(exc), 800) +
            "\n\nReturn ONLY valid JSON that conforms exactly to this JSON schema — every field must be a "
            "top-level key of this schema's type, not restructured or decomposed into a different shape:\n" + schema
        ))
        result = structured_llm.invoke(messages + [corrective], config={"callbacks": [usage_handler]})
    if logger:
        try:
            raw_text = result.model_dump_json()
        except Exception:
            raw_text = str(result)
        logger.log("llm_output", "LLM response", details={"raw": _truncate(raw_text)})
        for model_name, usage in usage_handler.usage_metadata.items():
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cost = estimate_cost_usd(model_name, input_tokens, output_tokens)
            logger.log("usage", f"{model_name}: {usage.get('total_tokens', 0)} tokens", details={
                "provider": provider_key,
                "model": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": usage.get("total_tokens", 0),
                "estimated_cost_usd": cost,
            })
    return result
