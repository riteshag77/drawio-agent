"""A1: Async LLM transport — native async I/O instead of thread-per-request.

Replaces synchronous LangChain client with native async httpx-based client
and per-API-key concurrency management via semaphores.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional, Union

import httpx

from app.adapters.outbound.config.config import Config
from app.adapters.outbound.llm.providers import get_provider, normalize_provider_key

logger = logging.getLogger("drawio-agent.llm")


class LlmConcurrencyManager:
    """Manages per-API-key concurrency limits to prevent any single provider from blocking others."""

    def __init__(self, max_concurrent_per_key: int = 3):
        self.max_concurrent_per_key = max_concurrent_per_key
        self.semaphores: dict[str, asyncio.Semaphore] = {}
        self.lock = asyncio.Lock()

    async def acquire(self, api_key: str) -> asyncio.Semaphore:
        """Get or create a semaphore for the given API key."""
        async with self.lock:
            if api_key not in self.semaphores:
                self.semaphores[api_key] = asyncio.Semaphore(self.max_concurrent_per_key)
        return self.semaphores[api_key]


class AsyncLlmClient:
    """Async LLM client with provider fallback and retry logic.
    
    Replaces synchronous requests with native async I/O via httpx.
    Implements:
    - Per-key concurrency limits (semaphores)
    - Provider fallback chain
    - Transient retry (429, 500, 502, 503)
    - Structured output (function calling, JSON mode, etc.)
    """

    def __init__(
        self,
        providers: Any,  # providers_module
        config: Config,
        on_retry: Optional[Callable[[str, int], None]] = None,
    ):
        self.providers = providers
        self.config = config
        self.concurrency_mgr = LlmConcurrencyManager(config.max_concurrent_llm_calls_per_key)
        self.on_retry = on_retry or (lambda msg, attempt: None)
        self.client = httpx.AsyncClient(timeout=config.llm_timeout_seconds)

    async def close(self) -> None:
        """Clean up HTTP client (call on shutdown)."""
        await self.client.aclose()

    async def call_llm(
        self,
        messages: list[dict],
        model: str,
        provider: Optional[str] = None,
        structured: bool = False,
        json_schema: Optional[dict] = None,
        on_token_usage: Optional[Callable[[int, int, float], None]] = None,
    ) -> Union[str, dict]:
        """Call LLM with async transport and provider fallback.
        
        Args:
            messages: OpenAI-format message list
            model: Model identifier (e.g., 'claude-3.5-sonnet')
            provider: Provider key ('anthropic', 'openai', etc.) or None for default
            structured: Whether to request structured output
            json_schema: JSON schema for structured output (optional)
            on_token_usage: Callback(tokens_in, tokens_out, cost_usd)
        
        Returns:
            String response or dict (if structured=True)
        
        Raises:
            RuntimeError: If all providers in fallback chain fail
        """
        if provider is None:
            provider = self.providers.default_provider_key()
        
        # Try primary provider, then fallback chain
        provider_chain = [provider] + self.config.provider_fallback_order
        provider_chain = list(dict.fromkeys(provider_chain))  # Deduplicate
        
        last_error = None
        for attempt_provider in provider_chain:
            try:
                result = await self._call_llm_single(
                    messages=messages,
                    model=model,
                    provider=attempt_provider,
                    structured=structured,
                    json_schema=json_schema,
                    on_token_usage=on_token_usage,
                )
                return result
            
            except Exception as exc:
                logger.warning(
                    f"Provider '{attempt_provider}' failed: {exc}. "
                    f"Trying next in fallback chain..."
                )
                last_error = exc
        
        raise RuntimeError(
            f"All providers in fallback chain failed. Last error: {last_error}"
        ) from last_error

    async def _call_llm_single(
        self,
        messages: list[dict],
        model: str,
        provider: str,
        structured: bool,
        json_schema: Optional[dict],
        on_token_usage: Optional[Callable[[int, int, float], None]],
    ) -> Union[str, dict]:
        """Call a single provider with retry logic."""
        provider_spec = self.providers.get_provider(normalize_provider_key(provider))
        api_key = self.config._get(provider_spec.api_key_env) if provider_spec.api_key_env else None
        
        if not api_key:
            raise RuntimeError(f"No API key configured for provider '{provider}'")
        
        # Acquire semaphore for this API key
        semaphore = await self.concurrency_mgr.acquire(api_key)
        
        async with semaphore:
            # Retry logic: exponential backoff on transient errors
            for attempt in range(1, 4):  # Max 3 attempts
                try:
                    result = await self._call_provider_api(
                        messages=messages,
                        model=model,
                        provider_spec=provider_spec,
                        api_key=api_key,
                        structured=structured,
                        json_schema=json_schema,
                    )
                    
                    # Extract usage and invoke callback
                    if on_token_usage and isinstance(result, dict):
                        usage = result.get("usage", {})
                        tokens_in = usage.get("prompt_tokens", 0)
                        tokens_out = usage.get("completion_tokens", 0)
                        cost = usage.get("cost_usd", 0.0)
                        on_token_usage(tokens_in, tokens_out, cost)
                    
                    return result.get("content", result)
                
                except httpx.HTTPStatusError as exc:
                    # Transient errors: retry with backoff
                    if exc.response.status_code in (429, 500, 502, 503) and attempt < 3:
                        wait_time = 2 ** (attempt - 1)  # 1s, 2s, 4s
                        self.on_retry(
                            f"Transient error {exc.response.status_code} from {provider}",
                            attempt
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    
                    # Permanent errors: don't retry
                    raise RuntimeError(f"API error {exc.response.status_code}: {exc}") from exc
                
                except asyncio.TimeoutError as exc:
                    if attempt < 3:
                        wait_time = 2 ** (attempt - 1)
                        self.on_retry(f"Timeout calling {provider}", attempt)
                        await asyncio.sleep(wait_time)
                        continue
                    raise RuntimeError(f"Timeout calling {provider}") from exc

    async def _call_provider_api(
        self,
        messages: list[dict],
        model: str,
        provider_spec: Any,
        api_key: str,
        structured: bool,
        json_schema: Optional[dict],
    ) -> dict:
        """Make the actual HTTP call to the provider's API."""
        
        # Build request based on provider's structured output mode
        request_body = self._build_request_body(
            messages=messages,
            model=model,
            provider_spec=provider_spec,
            structured=structured,
            json_schema=json_schema,
        )
        
        # Call provider API
        response = await self.client.post(
            provider_spec.endpoint,
            json=request_body,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        
        response_data = response.json()
        
        # Parse response based on provider format
        return self._parse_response(response_data, provider_spec, structured)

    def _build_request_body(
        self,
        messages: list[dict],
        model: str,
        provider_spec: Any,
        structured: bool,
        json_schema: Optional[dict],
    ) -> dict:
        """Build provider-specific request body."""
        
        body = {
            "messages": messages,
            "model": model,
        }
        
        if structured and provider_spec.structured_output == "function_calling":
            # Add function schema for OpenAI-style function calling
            body["tools"] = [{"type": "function", "function": json_schema}]
        
        elif structured and provider_spec.structured_output == "json_mode":
            # JSON mode: model must output valid JSON
            body["response_format"] = {"type": "json_object"}
        
        return body

    def _parse_response(
        self,
        response_data: dict,
        provider_spec: Any,
        structured: bool,
    ) -> dict:
        """Parse provider-specific response format."""
        
        # Extract content from provider-specific format
        if "choices" in response_data:  # OpenAI-format
            content = response_data["choices"][0]["message"]["content"]
        elif "content" in response_data:  # Anthropic-format
            content = response_data["content"][0]["text"]
        else:
            content = str(response_data)
        
        # Parse JSON if structured
        if structured:
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse structured output: {content}")
                return {"raw": content}
        
        return {
            "content": content,
            "usage": response_data.get("usage", {}),
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
