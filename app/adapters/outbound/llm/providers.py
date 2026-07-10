"""LLM provider registry and routing policy.

Provider-specific values live here so call sites stay generic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI


def normalize_gateway_model_id(model_id: str, *, default: str, prefix: str = "") -> str:
    """Strip optional gateway prefix (e.g. ``provider/model`` → ``model``)."""
    model_id = (model_id or default).strip()
    if prefix and model_id.startswith(prefix):
        return model_id.split("/", 1)[1]
    return model_id or default


def normalize_gateway_base_url(base_url: str, default: str) -> str:
    """Normalize OpenAI-compatible gateway base URLs (strip trailing slash)."""
    return (base_url or default).strip().rstrip("/")


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Static provider wiring. Runtime secrets come from env vars."""

    key: str
    client_class: type | None = None
    default_model: str = ""
    api_key_env: str = ""
    kind: str = "langchain"  # langchain | sdk
    base_url_env: str = ""
    base_url_default: str = ""
    structured_output: str = "function_calling"  # function_calling | json_mode | none


def _build_registry() -> dict[str, ProviderSpec]:
    return {
        "openai": ProviderSpec(
            key="openai",
            client_class=ChatOpenAI,
            default_model="gpt-4o",
            api_key_env="OPENAI_API_KEY",
            structured_output="function_calling",
        ),
        "openrouter": ProviderSpec(
            key="openrouter",
            client_class=ChatOpenAI,
            default_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            api_key_env="OPENROUTER_API_KEY",
            base_url_env="OPENROUTER_BASE_URL",
            base_url_default="https://openrouter.ai/api/v1",
            structured_output="none",
        ),
        "gemini": ProviderSpec(
            key="gemini",
            client_class=ChatGoogleGenerativeAI,
            default_model="gemini-2.5-flash",
            api_key_env="GOOGLE_API_KEY",
            structured_output="json_mode",
        ),
        "claude": ProviderSpec(
            key="claude",
            client_class=ChatAnthropic,
            default_model="claude-sonnet-4-6",
            api_key_env="ANTHROPIC_API_KEY",
            structured_output="function_calling",
        ),
        "opencode": ProviderSpec(
            key="opencode",
            client_class=ChatOpenAI,
            default_model=os.getenv("OPENCODE_MODEL", "big-pickle"),
            api_key_env="OPENCODE_API_KEY",
            base_url_env="OPENCODE_BASE_URL",
            base_url_default="https://opencode.ai/zen/v1",
            structured_output="none",
        ),
    }


PROVIDERS: dict[str, ProviderSpec] = _build_registry()
MODEL_IDS: dict[str, str] = {key: spec.default_model for key, spec in PROVIDERS.items()}


def normalize_provider_key(provider: str) -> str:
    return (provider or "openai").strip().lower()


def get_provider(provider: str) -> ProviderSpec:
    key = normalize_provider_key(provider)
    spec = PROVIDERS.get(key)
    if not spec:
        raise ValueError(f"Unsupported provider: {key}")
    return spec


def require_api_key(spec: ProviderSpec) -> None:
    if spec.api_key_env and not os.getenv(spec.api_key_env):
        raise ValueError(f"{spec.api_key_env} environment variable is not set.")


def resolve_gateway_base_url(spec: ProviderSpec) -> str:
    if not spec.base_url_env:
        return spec.base_url_default
    return normalize_gateway_base_url(os.getenv(spec.base_url_env, spec.base_url_default), spec.base_url_default)


def build_langchain_client(spec: ProviderSpec, *, timeout: int) -> BaseChatModel:
    """Construct a LangChain chat client for the given provider spec."""
    if spec.client_class is None:
        raise ValueError(f"Provider {spec.key} does not use a LangChain client.")

    kwargs: dict[str, Any] = {"model": spec.default_model, "temperature": 0, "timeout": timeout}
    if spec.base_url_env:
        kwargs["api_key"] = os.getenv(spec.api_key_env)
        kwargs["base_url"] = resolve_gateway_base_url(spec)
    return spec.client_class(**kwargs)
