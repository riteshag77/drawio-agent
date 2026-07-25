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
from app.adapters.outbound.llm.pricing import display_currency, estimate_cost_aud
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
            "system_prompt": system_prompt.replace(CACHE_BREAKPOINT, ""),
            "human_content": human_content,
        })

    method = spec.structured_output
    messages = [build_system_message(spec, system_prompt), _build_human_message(human_content, attachment_documents)]
    timeout = get_config().llm_timeout_seconds
    llm = build_langchain_client(spec, timeout=timeout)

    kwargs = {"method": "json_mode"} if method == "json_mode" else {}
    if logger:
        logger.log("llm", "Using structured output", details={"structured_output": method or "function_calling"})
    usage_handler = UsageMetadataCallbackHandler()
    # include_raw=True keeps the underlying AIMessage (and its response_metadata —
    # finish_reason/stop_reason, safety_ratings) alongside the parsed object,
    # instead of with_structured_output()'s default of discarding everything but
    # the parsed result. Without this there was no way to tell a genuine schema
    # failure apart from the provider silently stopping early (MAX_TOKENS/SAFETY/
    # RECITATION) — both looked identical from here: a parsed-but-truncated object.
    structured_llm = llm.with_structured_output(output_type, include_raw=True, **kwargs)
    raw_result: dict[str, Any] = {}

    def _invoke(msgs: list) -> dict[str, Any]:
        return structured_llm.invoke(msgs, config={"callbacks": [usage_handler]})

    try:
        try:
            raw_result = _invoke(messages)
            if raw_result.get("parsing_error") is not None or raw_result.get("parsed") is None:
                raise OutputParserException(str(raw_result.get("parsing_error") or "Structured output parsing failed"))
        except OutputParserException as exc:
            if logger:
                logger.log("llm", f"Structured output failed validation, retrying once: {exc}", level="warning")
            schema = json.dumps(output_type.model_json_schema(), ensure_ascii=False)
            corrective = HumanMessage(content=(
                "Your previous response failed schema validation: " + _truncate(str(exc), 800) +
                "\n\nReturn ONLY valid JSON that conforms exactly to this JSON schema — every field must be a "
                "top-level key of this schema's type, not restructured or decomposed into a different shape:\n" + schema
            ))
            raw_result = _invoke(messages + [corrective])
            if raw_result.get("parsed") is None:
                raise OutputParserException(str(raw_result.get("parsing_error") or "Structured output parsing failed after retry")) from exc
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
                raw_result = _invoke(messages)
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

    parsed = raw_result.get("parsed")
    ai_message = raw_result.get("raw")
    response_metadata = getattr(ai_message, "response_metadata", None) or {}
    # finish_reason (Google) / stop_reason (Anthropic) / finish_reason (OpenAI)
    # tells us definitively *why* generation stopped — "STOP"/"end_turn" is a
    # normal completion, but "MAX_TOKENS"/"length" means the output was cut off
    # by a token ceiling and "SAFETY"/"content_filter"/"RECITATION" means the
    # provider itself refused/truncated the content. Logging the raw dict here
    # (small — a handful of keys) means a future truncation is diagnosed from
    # this one field instead of inferred from token counts and XML inspection.
    finish_reason = response_metadata.get("finish_reason") or response_metadata.get("stop_reason")
    if logger and finish_reason and finish_reason not in ("stop", "STOP", "end_turn"):
        logger.log(
            "llm",
            f"Provider stopped generation with finish_reason={finish_reason!r} — response may be incomplete",
            level="warning",
            details={"response_metadata": response_metadata},
        )

    if logger:
        try:
            raw_text = parsed.model_dump_json() if parsed is not None else "null"
        except Exception:
            raw_text = str(parsed)
        logger.log("llm_output", "LLM response", details={"raw": raw_text, "response_metadata": response_metadata})
        for model_name, usage in usage_handler.usage_metadata.items():
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            token_details = usage.get("input_token_details") or {}
            cache_read = token_details.get("cache_read", 0)
            cache_creation = token_details.get("cache_creation", 0)
            cost = estimate_cost_aud(model_name, input_tokens, output_tokens)
            logger.log("usage", f"{model_name}: {usage.get('total_tokens', 0)} tokens", details={
                "provider": provider_key,
                "model": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "total_tokens": usage.get("total_tokens", 0),
                "estimated_cost": cost,
                "currency": display_currency(),
                # Back-compat for older UI / captures that read the USD-named field.
                "estimated_cost_usd": cost,
            })
    return parsed
