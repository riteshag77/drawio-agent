"""Generic LangChain invocation via structured output."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from langchain_core.callbacks.usage import UsageMetadataCallbackHandler
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import HumanMessage, SystemMessage

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.llm.caching import build_system_message
from app.adapters.outbound.llm.pricing import estimate_cost_usd
from app.adapters.outbound.llm.prompts.system import CACHE_BREAKPOINT
from app.adapters.outbound.llm.providers import (
    build_langchain_client,
    get_provider,
    normalize_provider_key,
    require_api_key,
)

if TYPE_CHECKING:
    from app.core.logging import StepLogger


_TRANSIENT_STATUS_CODES = {429, 502, 503, 504}


def _is_transient(exc: Exception) -> bool:
    """Detect rate-limit / overloaded HTTP responses worth retrying once.

    Only matches when the exception carries an explicit HTTP status code
    (via ``status_code`` or ``response.status_code`` attribute) to avoid
    false-positives on generic errors whose message happens to mention a number.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
    return status in _TRANSIENT_STATUS_CODES


def _truncate(text: str, max_chars: int = 5000) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + f"... (truncated, {len(text)} total chars)"


def _build_human_message(human_content: str, attachments: list[dict] | None) -> HumanMessage:
    if not attachments:
        return HumanMessage(content=human_content)
    content_blocks: list[dict[str, Any]] = [{"type": "text", "text": human_content}]
    for att in attachments:
        mime = att.get("mime", "")
        block_type = "image" if mime.startswith("image/") else "file"
        block: dict[str, Any] = {
            "type": block_type,
            "source_type": "base64",
            "data": att["data"],
            "mime_type": mime,
        }
        if block_type == "file" and att.get("filename"):
            block["filename"] = att["filename"]
        content_blocks.append(block)
    return HumanMessage(content=content_blocks)


def call_llm(
    system_prompt: str,
    human_content: str,
    provider: str,
    output_type: type,
    logger: "StepLogger | None" = None,
    attachment_documents: list[dict] | None = None,
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
            "system_prompt": _truncate(system_prompt.replace(CACHE_BREAKPOINT, "")),
            "human_content": _truncate(human_content),
        })

    method = spec.structured_output
    messages = [build_system_message(spec, system_prompt), _build_human_message(human_content, attachment_documents)]
    timeout = get_config().llm_timeout_seconds
    llm = build_langchain_client(spec, timeout=timeout)

    kwargs = {"method": "json_mode"} if method == "json_mode" else {}
    if logger:
        logger.log("llm", "Using structured output", details={"structured_output": method or "function_calling"})
    usage_handler = UsageMetadataCallbackHandler()
    structured_llm = llm.with_structured_output(output_type, **kwargs)
    try:
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
    except OutputParserException:
        raise
    except Exception as exc:
        # Retry transient provider failures (429/502/503/504) with exponential backoff.
        if not _is_transient(exc):
            if attachment_documents:
                filenames = ", ".join(a.get("filename", "the attached file") for a in attachment_documents)
                raise ValueError(
                    f"The '{provider_key}' provider couldn't process the attached file(s) ({filenames}). "
                    "Try Claude or Gemini, or attach a text-based version."
                ) from exc
            raise  # re-raise original exception unchanged

        config = get_config()
        max_retries = config.llm_max_retries
        backoff_base = config.llm_retry_backoff_base
        backoff_max = config.llm_retry_backoff_max
        last_exc = exc
        for attempt in range(max_retries):
            delay = min(backoff_base * (2 ** attempt), backoff_max)
            if logger:
                logger.log(
                    "llm",
                    f"Transient provider error, retrying ({attempt + 1}/{max_retries}) in {delay:g}s: {last_exc}",
                    level="warning",
                )
            time.sleep(delay)
            try:
                result = structured_llm.invoke(messages, config={"callbacks": [usage_handler]})
                break
            except Exception as retry_exc:
                last_exc = retry_exc
        else:
            if attachment_documents:
                filenames = ", ".join(a.get("filename", "the attached file") for a in attachment_documents)
                raise ValueError(
                    f"The '{provider_key}' provider couldn't process the attached file(s) ({filenames}). "
                    "Try Claude or Gemini, or attach a text-based version."
                ) from last_exc
            raise ValueError(f"The '{provider_key}' provider (model {spec.default_model}) failed: {last_exc}") from last_exc
    if logger:
        try:
            raw_text = result.model_dump_json()
        except Exception:
            raw_text = str(result)
        logger.log("llm_output", "LLM response", details={"raw": _truncate(raw_text)})
        for model_name, usage in usage_handler.usage_metadata.items():
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            token_details = usage.get("input_token_details") or {}
            cache_read = token_details.get("cache_read", 0)
            cache_creation = token_details.get("cache_creation", 0)
            cost = estimate_cost_usd(model_name, input_tokens, output_tokens)
            logger.log("usage", f"{model_name}: {usage.get('total_tokens', 0)} tokens", details={
                "provider": provider_key,
                "model": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "total_tokens": usage.get("total_tokens", 0),
                "estimated_cost_usd": cost,
            })
    return result
