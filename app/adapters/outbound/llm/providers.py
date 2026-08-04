"""LLM provider registry and routing policy.

Provider wiring lives in ``config/providers.json`` so adding a new provider —
including a new OpenAI-compatible gateway — never requires touching this
module. Only a genuinely new client *library* (not currently one of
openai/anthropic/google) needs a code change, in ``_resolve_client_class``
below.

Client libraries (``langchain_anthropic``, ``langchain_openai``,
``langchain_google_genai``) are imported lazily, one at a time, only when a
client of that kind is actually constructed — not at module import time.
``langchain_google_genai`` in particular pulls in grpc/protobuf, and eagerly
importing all three regardless of which provider is configured dominates
cold-start time for no benefit.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.adapters.outbound.config.env_config import EnvConfigAdapter, get_config

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

PROVIDERS_CONFIG_PATH: Path = EnvConfigAdapter.base_dir() / "config" / "providers.json"

# Client-library discriminators accepted in providers.json's "client" field.
# Validated eagerly (cheap — just strings); the actual library import happens
# lazily in _resolve_client_class, only for the provider(s) actually used.
_CLIENT_KEYS = frozenset({"openai", "anthropic", "google"})

_VALID_STRUCTURED_OUTPUT = {"function_calling", "json_mode", "none"}
_VALID_PROMPT_CACHE = {"anthropic", "auto", "none"}

# Default prompt-caching strategy per client library, used when providers.json
# does not set "prompt_cache" explicitly:
#   anthropic → explicit cache_control breakpoints (ChatAnthropic)
#   openai    → "auto": provider does native prefix caching, send a plain string
#   google    → "none": no supported prefix caching in this path
_DEFAULT_PROMPT_CACHE: dict[str, str] = {
    "anthropic": "anthropic",
    "openai": "auto",
    "google": "none",
}

# No LangChain client library's own default output-token ceiling is generous
# enough for a large/complex draw.io XML diagram — ChatAnthropic in particular
# defaults to just 1024 tokens if max_tokens is never set. Leaving this unset
# caused two live failures where generation was silently hard-stopped
# mid-attribute (observed output_tokens ~6700-7800, close to a common ~8192
# library/model default) — the resulting XML is missing content, not just
# malformed, so no downstream repair pass can recover it. Set explicitly on
# every client instead of trusting per-library defaults; override per-provider
# via providers.json's "max_output_tokens" field if a specific model needs a
# different ceiling.
_DEFAULT_MAX_OUTPUT_TOKENS = 32000

# LangChain kwarg name for the output-token ceiling differs per client library.
_MAX_TOKENS_KWARG: dict[str, str] = {
    "google": "max_output_tokens",
    "anthropic": "max_tokens",
    "openai": "max_tokens",
}


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
    base_url_env: str = ""
    base_url_default: str = ""
    structured_output: str = "function_calling"  # function_calling | json_mode | none
    label: str = ""
    default_model_env: str = ""
    omit_temperature: bool = False
    prompt_cache: str = "none"  # anthropic | auto | none
    # Lazy client-library discriminator ("openai" | "anthropic" | "google"), set for
    # specs built from providers.json. When client_class is also set (test/override
    # path), client_class wins and the library is never imported via this key.
    client_key: str = ""
    # 0 means "not set" (falls back to _DEFAULT_MAX_OUTPUT_TOKENS at client-construction
    # time) — see _cached_langchain_client. Explicit per-provider override available via
    # providers.json's "max_output_tokens" field.
    max_output_tokens: int = 0
    # Set only on a per-request custom (BYOK) provider spec built by
    # _spec_from_custom_payload — bypasses api_key_env lookup entirely.
    api_key_inline: str = ""
    # True only for a synthetic spec built from a request's custom_provider payload.
    # Such a spec is never registry-backed and must never be cached — see
    # build_langchain_client — since it carries a user-supplied secret that must not
    # outlive the request it came from.
    ephemeral: bool = False
    # Additional selectable models for one provider entry (e.g. every model NVIDIA
    # NIM exposes under one API key/base_url) — keeps one JSON record per *provider*
    # instead of duplicating a whole record per model. Empty means "just
    # default_model, no picker." A request selects one via `model` (ChatRequest) —
    # see using_model_override/get_provider below. First entry is the one shown
    # pre-selected in the UI.
    models: tuple[str, ...] = ()


#: Reserved provider key for a per-request BYOK (bring-your-own-key) provider —
#: never a real registry entry. A request selects it by sending provider="custom"
#: alongside a custom_provider payload; see using_custom_provider/get_provider below.
CUSTOM_PROVIDER_KEY = "custom"


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
    if client not in _CLIENT_KEYS:
        raise ValueError(
            f"providers.json: provider '{key}' has unknown client '{client}' — "
            f"supported clients: {sorted(_CLIENT_KEYS)}"
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
    prompt_cache = str(record.get("prompt_cache") or _DEFAULT_PROMPT_CACHE.get(client, "none")).strip().lower()
    if prompt_cache not in _VALID_PROMPT_CACHE:
        raise ValueError(
            f"providers.json: provider '{key}' has invalid prompt_cache "
            f"'{prompt_cache}' — must be one of {sorted(_VALID_PROMPT_CACHE)}"
        )
    models_field = record.get("models")
    if models_field is not None and not isinstance(models_field, list):
        raise ValueError(f"providers.json: provider '{key}' field 'models' must be a JSON array")
    models = tuple(str(m).strip() for m in (models_field or []) if str(m).strip())
    return ProviderSpec(
        key=key,
        client_key=client,  # lazily resolved to the real class on first use — see _resolve_client_class
        default_model=_resolve_default_model(record, key),
        api_key_env=api_key_env,
        base_url_env=str(record.get("base_url_env") or "").strip(),
        base_url_default=str(record.get("base_url_default") or "").strip(),
        structured_output=structured_output,
        label=str(record.get("label") or key).strip(),
        default_model_env=str(record.get("default_model_env") or "").strip(),
        omit_temperature=bool(record.get("omit_temperature")),
        prompt_cache=prompt_cache,
        max_output_tokens=int(record.get("max_output_tokens") or 0),
        models=models,
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
        if spec.key == CUSTOM_PROVIDER_KEY:
            raise ValueError(
                f"providers.json: '{CUSTOM_PROVIDER_KEY}' is a reserved provider key "
                "(used for per-request bring-your-own-key providers) and cannot be configured here."
            )
        registry[spec.key] = spec
    if not registry:
        raise ValueError("providers.json contains zero providers")
    return registry


def reload_providers() -> dict[str, ProviderSpec]:
    """Clear the cache and rebuild — used by tests, and available for a future ops reload path."""
    global PROVIDERS, MODEL_IDS
    _build_registry.cache_clear()
    _cached_langchain_client.cache_clear()  # old ProviderSpec instances would otherwise leak in the client cache
    PROVIDERS = _build_registry()
    MODEL_IDS = {key: spec.default_model for key, spec in PROVIDERS.items()}
    return PROVIDERS


PROVIDERS: dict[str, ProviderSpec] = _build_registry()
MODEL_IDS: dict[str, str] = {key: spec.default_model for key, spec in PROVIDERS.items()}


def normalize_provider_key(provider: str) -> str:
    return (provider or default_provider_key()).strip().lower()


#: Per-request custom (BYOK) provider payload — set for the duration of one
#: request via using_custom_provider, read by get_provider when the caller
#: asks for CUSTOM_PROVIDER_KEY. asyncio.to_thread (used by run_llm_sync)
#: propagates the current context into its worker thread, so this is visible
#: to the actual LLM call even though it runs off the request's own coroutine.
_custom_provider_ctx: ContextVar[dict[str, Any] | None] = ContextVar(
    "custom_provider_payload", default=None
)


@contextmanager
def using_custom_provider(payload: dict[str, Any] | None):
    """Scope a custom_provider payload to one request.

    Always reset on exit (even on error) so a payload never leaks into a
    later request reusing the same worker thread/task.
    """
    token = _custom_provider_ctx.set(payload)
    try:
        yield
    finally:
        _custom_provider_ctx.reset(token)


def _validate_custom_base_url(base_url: str) -> str:
    """Light well-formedness check only — no IP-range/SSRF blocking.

    Deliberate: this app is a personal/local dev tool, and the most realistic
    custom-provider use case (a local Ollama/LM Studio/LAN gateway) *is* a
    private address. Still reject obviously malformed input.
    """
    url = (base_url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"custom provider base_url {url!r} must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError(f"custom provider base_url {url!r} must include a hostname")
    return url.rstrip("/")


def _spec_from_custom_payload(payload: dict[str, Any]) -> ProviderSpec:
    """Build a synthetic, ephemeral ProviderSpec from one request's custom_provider
    payload (label/client/api_key/base_url/model — see CustomProviderConfig)."""
    client = str(payload.get("client") or "").strip().lower()
    if client not in _CLIENT_KEYS:
        raise ValueError(
            f"custom provider: unsupported client {client!r} — must be one of {sorted(_CLIENT_KEYS)}"
        )
    model = str(payload.get("model") or "").strip()
    if not model:
        raise ValueError("custom provider: 'model' is required")
    base_url = _validate_custom_base_url(str(payload.get("base_url") or ""))
    return ProviderSpec(
        key=CUSTOM_PROVIDER_KEY,
        client_key=client,
        default_model=model,
        api_key_inline=str(payload.get("api_key") or "").strip(),
        base_url_default=base_url,
        label=str(payload.get("label") or "Custom").strip() or "Custom",
        ephemeral=True,
    )


#: Per-request model override for a multi-model provider (e.g. picking one of
#: NVIDIA NIM's several models under a single provider entry) — same
#: request-scoped-contextvar pattern as _custom_provider_ctx, and for the same
#: reason: threading a `model` parameter through every intermediate function
#: that currently passes a plain `provider: str` would touch ~25+ call sites
#: for no benefit, since get_provider is the one place a spec is resolved.
_model_override_ctx: ContextVar[str | None] = ContextVar("model_override", default=None)


@contextmanager
def using_model_override(model: str | None):
    """Scope a model override (one of a provider's ``models``) to one request."""
    token = _model_override_ctx.set(model or None)
    try:
        yield
    finally:
        _model_override_ctx.reset(token)


def get_provider(provider: str) -> ProviderSpec:
    key = normalize_provider_key(provider)
    if key == CUSTOM_PROVIDER_KEY:
        payload = _custom_provider_ctx.get()
        if payload is None:
            raise ValueError(
                "provider='custom' requires a custom_provider payload on the request"
            )
        return _spec_from_custom_payload(payload)
    spec = PROVIDERS.get(key)
    if not spec:
        raise ValueError(f"Unsupported provider: {key}")
    override = _model_override_ctx.get()
    if override:
        if spec.models and override not in spec.models:
            raise ValueError(
                f"model {override!r} is not available for provider {key!r} — "
                f"choose one of {list(spec.models)}"
            )
        spec = replace(spec, default_model=override)
    return spec


def default_provider_key() -> str:
    configured = get_config().provider_default.strip().lower()
    if configured in PROVIDERS:
        return configured
    return "gemini" if os.environ.get("GOOGLE_API_KEY") else "openai"


class MissingApiKeyError(ValueError):
    """A provider was selected but its API key env var is unset.

    Subclasses ValueError so existing ``except ValueError`` handlers are
    unaffected; the distinct type lets the fallback layer tell "this vendor is
    not configured" apart from "this vendor rejected the request".
    """


def require_api_key(spec: ProviderSpec) -> None:
    if spec.ephemeral:
        if not spec.api_key_inline:
            raise MissingApiKeyError("Custom provider requires an API key.")
        return
    if spec.api_key_env and not _env_api_key(spec.api_key_env):
        raise MissingApiKeyError(f"{spec.api_key_env} environment variable is not set.")


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


def _resolve_client_class(client_key: str) -> type:
    """Import and return the LangChain client class for one provider library.

    Imported one at a time, on first actual use of that provider — not at
    module load. ``langchain_google_genai`` especially (grpc/protobuf) should
    never be paid for by a process that only ever calls Claude or OpenAI.
    """
    if client_key == "anthropic":
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
        return ChatAnthropic
    if client_key == "openai":
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
        return ChatOpenAI
    if client_key == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415
        return ChatGoogleGenerativeAI
    raise ValueError(f"Unknown client library key: {client_key!r}")


def _env_api_key(env_name: str) -> str | None:
    """Read an API key from the environment, stripping accidental quotes."""
    raw = os.getenv(env_name)
    if raw is None:
        return None
    key = raw.strip().strip("'").strip('"')
    return key or None


def _client_kwargs(spec: ProviderSpec, timeout: int) -> dict[str, Any]:
    # max_retries=0: every underlying SDK (openai/anthropic/google) has its own
    # hidden retry-on-timeout loop (openai defaults to 2) that stacks silently
    # on top of `timeout` — one "call" can become 2-3x `timeout` of wall-clock
    # before our own llm_client retry/backoff or provider fallback ever sees
    # the failure. Our own retry policy (config.llm_max_retries, deliberately
    # narrow — see env_config.llm_max_retries) is the only retry loop we want.
    kwargs: dict[str, Any] = {"model": spec.default_model, "timeout": timeout, "max_retries": 0}
    if not spec.omit_temperature:
        kwargs["temperature"] = 0
    # Always pass the key explicitly when configured — ChatAnthropic/OpenAI will
    # otherwise only see a raw env var, and quoted .env values can silently 401.
    # A custom (ephemeral) spec's inline key always wins over any env lookup.
    key = spec.api_key_inline or (_env_api_key(spec.api_key_env) if spec.api_key_env else None)
    if key:
        kwargs["api_key"] = key
    # A literal base_url_default (no base_url_env) must still be honoured —
    # that's exactly the shape a custom-provider spec has.
    if spec.base_url_env:
        kwargs["base_url"] = resolve_gateway_base_url(spec)
    elif spec.base_url_default:
        kwargs["base_url"] = normalize_gateway_base_url(spec.base_url_default, spec.base_url_default)
    token_kwarg = _MAX_TOKENS_KWARG.get(spec.client_key, "max_tokens")
    kwargs[token_kwarg] = spec.max_output_tokens or _DEFAULT_MAX_OUTPUT_TOKENS
    return kwargs


@lru_cache(maxsize=64)
def _cached_langchain_client(spec: ProviderSpec, timeout: int) -> BaseChatModel:
    client_cls = spec.client_class or _resolve_client_class(spec.client_key)
    return client_cls(**_client_kwargs(spec, timeout))


def build_langchain_client(spec: ProviderSpec, *, timeout: int) -> BaseChatModel:
    """Construct (or reuse a cached) LangChain chat client for the given provider spec.

    Memoized by ``(spec, timeout)`` — ``ProviderSpec`` is a frozen, hashable
    dataclass — so the underlying client and its httpx connection pool are
    reused across the multiple LLM calls one request can make (generate →
    repair → compliance) and across requests, instead of rebuilding a fresh
    client (and connection pool) on every call. The client *library* import
    itself is deferred to this first call — see ``_resolve_client_class``.

    ``spec.ephemeral`` (a per-request custom/BYOK spec) always skips this
    cache and builds directly — a user-supplied secret must never sit in a
    long-lived process-wide cache across requests.
    """
    if spec.client_class is None and not spec.client_key:
        raise ValueError(f"Provider {spec.key} does not use a LangChain client.")
    if spec.ephemeral:
        client_cls = spec.client_class or _resolve_client_class(spec.client_key)
        return client_cls(**_client_kwargs(spec, timeout))
    return _cached_langchain_client(spec, timeout)
