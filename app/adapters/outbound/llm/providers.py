"""LLM provider registry and routing policy.

Provider wiring lives in ``config/providers.json`` so adding a new provider —
including a new OpenAI-compatible gateway — never requires touching this
module. Only a genuinely new client *library* (not currently one of
openai/anthropic/google) needs a code change, in ``_CLIENT_CLASSES`` below.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from app.adapters.outbound.config.env_config import EnvConfigAdapter

PROVIDERS_CONFIG_PATH: Path = EnvConfigAdapter.base_dir() / "config" / "providers.json"

_CLIENT_CLASSES: dict[str, type] = {
    "openai": ChatOpenAI,
    "anthropic": ChatAnthropic,
    "google": ChatGoogleGenerativeAI,
}

_VALID_STRUCTURED_OUTPUT = {"function_calling", "json_mode", "none"}


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
    label: str = ""
    default_model_env: str = ""


def _resolve_default_model(record: dict, key: str) -> str:
    env_name = (record.get("default_model_env") or "").strip()
    literal = (record.get("default_model") or "").strip()
    model = os.getenv(env_name, literal).strip() if env_name else literal
    if not model:
        raise ValueError(f"providers.json: provider '{key}' has no usable default_model")
    return model


def _spec_from_record(record: dict) -> ProviderSpec:
    if not isinstance(record, dict):
        raise ValueError(f"providers.json: each provider record must be an object, got {type(record).__name__}")
    key = str(record.get("key") or "").strip().lower()
    if not key:
        raise ValueError("providers.json: provider record is missing required field 'key'")
    client = str(record.get("client") or "").strip().lower()
    if client not in _CLIENT_CLASSES:
        raise ValueError(
            f"providers.json: provider '{key}' has unknown client '{client}' — "
            f"supported clients: {sorted(_CLIENT_CLASSES)}"
        )
    structured_output = str(record.get("structured_output") or "function_calling").strip().lower()
    if structured_output not in _VALID_STRUCTURED_OUTPUT:
        raise ValueError(
            f"providers.json: provider '{key}' has invalid structured_output "
            f"'{structured_output}' — must be one of {sorted(_VALID_STRUCTURED_OUTPUT)}"
        )
    api_key_env = str(record.get("api_key_env") or "").strip()
    if not api_key_env:
        raise ValueError(f"providers.json: provider '{key}' is missing required field 'api_key_env'")
    return ProviderSpec(
        key=key,
        client_class=_CLIENT_CLASSES[client],
        default_model=_resolve_default_model(record, key),
        api_key_env=api_key_env,
        kind=str(record.get("kind") or "langchain").strip().lower(),
        base_url_env=str(record.get("base_url_env") or "").strip(),
        base_url_default=str(record.get("base_url_default") or "").strip(),
        structured_output=structured_output,
        label=str(record.get("label") or key).strip(),
        default_model_env=str(record.get("default_model_env") or "").strip(),
    )


@lru_cache(maxsize=1)
def _build_registry() -> dict[str, ProviderSpec]:
    if not PROVIDERS_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Provider config not found: {PROVIDERS_CONFIG_PATH}")
    try:
        records = json.loads(PROVIDERS_CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {PROVIDERS_CONFIG_PATH.name}: {exc}") from exc
    if not isinstance(records, list):
        raise ValueError("providers.json must be a JSON array of provider records")

    registry: dict[str, ProviderSpec] = {}
    for record in records:
        spec = _spec_from_record(record)
        if spec.key in registry:
            raise ValueError(f"Duplicate provider key in providers.json: '{spec.key}'")
        registry[spec.key] = spec
    if not registry:
        raise ValueError("providers.json contains zero providers")
    return registry


def reload_providers() -> dict[str, ProviderSpec]:
    """Clear the cache and rebuild — used by tests, and available for a future ops reload path."""
    global PROVIDERS, MODEL_IDS
    _build_registry.cache_clear()
    PROVIDERS = _build_registry()
    MODEL_IDS = {key: spec.default_model for key, spec in PROVIDERS.items()}
    return PROVIDERS


PROVIDERS: dict[str, ProviderSpec] = _build_registry()
MODEL_IDS: dict[str, str] = {key: spec.default_model for key, spec in PROVIDERS.items()}


def normalize_provider_key(provider: str) -> str:
    return (provider or "claude").strip().lower()


def get_provider(provider: str) -> ProviderSpec:
    key = normalize_provider_key(provider)
    spec = PROVIDERS.get(key)
    if not spec:
        raise ValueError(f"Unsupported provider: {key}")
    return spec


def default_provider_key() -> str:
    return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "openai"


def require_api_key(spec: ProviderSpec) -> None:
    if spec.api_key_env and not os.getenv(spec.api_key_env):
        raise ValueError(f"{spec.api_key_env} environment variable is not set.")


def resolve_gateway_base_url(spec: ProviderSpec) -> str:
    if not spec.base_url_env:
        return spec.base_url_default
    base_url = normalize_gateway_base_url(os.getenv(spec.base_url_env, spec.base_url_default), spec.base_url_default)
    if not base_url:
        raise ValueError(
            f"{spec.base_url_env} environment variable is not set for provider '{spec.key}' "
            f"and no base_url_default is configured — set {spec.base_url_env} in .env."
        )
    return base_url


def build_langchain_client(spec: ProviderSpec, *, timeout: int) -> BaseChatModel:
    """Construct a LangChain chat client for the given provider spec."""
    if spec.client_class is None:
        raise ValueError(f"Provider {spec.key} does not use a LangChain client.")

    kwargs: dict[str, Any] = {"model": spec.default_model, "temperature": 0, "timeout": timeout}
    if spec.base_url_env:
        kwargs["api_key"] = os.getenv(spec.api_key_env)
        kwargs["base_url"] = resolve_gateway_base_url(spec)
    return spec.client_class(**kwargs)
