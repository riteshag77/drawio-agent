"""Password-gated admin panel — manage provider API keys/base URLs and the
default provider from a browser instead of hand-editing `.env`.

Disabled entirely unless ADMIN_PASSWORD is set (env-only, never itself
overridable through the panel it gates). Auth is a single deterministic
token derived from ADMIN_PASSWORD via HMAC — no session store needed, same
threat model as the existing X-API-Key gate in app/main.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.adapters.outbound import admin_settings_store, governance_store
from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.llm import providers as providers_module
from app.domain.entities.governance import GovernanceRule

router = APIRouter(prefix="/admin")


@router.get("")
async def admin_page() -> RedirectResponse:
    return RedirectResponse(url="/static/admin.html")


def _expected_token() -> str | None:
    password = get_config().admin_password
    if not password:
        return None
    return hmac.new(password.encode("utf-8"), b"admin-session-v1", hashlib.sha256).hexdigest()


def _require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = _expected_token()
    if not expected:
        raise HTTPException(status_code=404, detail="Admin panel is not configured")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "••••" + value[-4:]


class LoginRequest(BaseModel):
    password: str = Field(max_length=500)


class ProviderSettingUpdate(BaseModel):
    api_key: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=500)


class DefaultProviderUpdate(BaseModel):
    provider: str = Field(max_length=100)


@router.get("/status")
async def admin_status() -> dict:
    return {"configured": get_config().admin_password is not None}


@router.post("/login")
async def admin_login(body: LoginRequest) -> dict:
    password = get_config().admin_password
    if not password:
        raise HTTPException(status_code=404, detail="Admin panel is not configured")
    if not secrets.compare_digest(body.password, password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    return {"token": _expected_token()}


@router.get("/providers")
async def admin_list_providers(x_admin_token: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    import os

    default_key = providers_module.default_provider_key()
    result = []
    for key, spec in sorted(providers_module.PROVIDERS.items()):
        api_key_value = os.environ.get(spec.api_key_env, "") if spec.api_key_env else ""
        base_url_value = os.environ.get(spec.base_url_env, "") if spec.base_url_env else ""
        result.append({
            "key": key,
            "label": spec.label,
            "api_key_env": spec.api_key_env,
            "api_key_set": bool(api_key_value),
            "api_key_hint": _mask(api_key_value),
            "api_key_source": "admin" if admin_settings_store.is_overridden(spec.api_key_env) else ("env" if api_key_value else "unset"),
            "base_url_env": spec.base_url_env or None,
            "base_url_value": base_url_value or None,
            "base_url_source": "admin" if (spec.base_url_env and admin_settings_store.is_overridden(spec.base_url_env)) else ("env" if base_url_value else "default"),
            "is_default": key == default_key,
        })
    return {"providers": result, "default_provider": default_key}


@router.post("/providers/{provider_key}")
async def admin_update_provider(
    provider_key: str, body: ProviderSettingUpdate, x_admin_token: str | None = Header(default=None)
) -> dict:
    _require_admin(x_admin_token)
    spec = providers_module.PROVIDERS.get(provider_key)
    if not spec:
        raise HTTPException(status_code=404, detail="Unknown provider")
    if body.api_key is not None and spec.api_key_env:
        admin_settings_store.set_setting(spec.api_key_env, body.api_key.strip())
    if body.base_url is not None and spec.base_url_env:
        admin_settings_store.set_setting(spec.base_url_env, body.base_url.strip())
    providers_module.reload_providers()
    return {"status": "ok"}


@router.delete("/providers/{provider_key}/api-key")
async def admin_clear_provider_key(provider_key: str, x_admin_token: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    spec = providers_module.PROVIDERS.get(provider_key)
    if not spec or not spec.api_key_env:
        raise HTTPException(status_code=404, detail="Unknown provider")
    admin_settings_store.set_setting(spec.api_key_env, "")
    providers_module.reload_providers()
    return {"status": "ok"}


@router.post("/providers/{provider_key}/test")
async def admin_test_provider(provider_key: str, x_admin_token: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    spec = providers_module.PROVIDERS.get(provider_key)
    if not spec:
        raise HTTPException(status_code=404, detail="Unknown provider")
    try:
        providers_module.require_api_key(spec)
        client = providers_module.build_langchain_client(spec, timeout=15)
        start = time.monotonic()
        await asyncio.to_thread(client.invoke, "Reply with exactly: OK")
        latency_ms = int((time.monotonic() - start) * 1000)
        return {"valid": True, "latency_ms": latency_ms}
    except providers_module.MissingApiKeyError as exc:
        return {"valid": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - surfaced to the admin as a validation result, not a 500
        return {"valid": False, "error": str(exc)[:300]}


@router.post("/default-provider")
async def admin_set_default_provider(body: DefaultProviderUpdate, x_admin_token: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    key = body.provider.strip().lower()
    if key not in providers_module.PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown provider")
    admin_settings_store.set_setting("PROVIDER_DEFAULT", key)
    return {"status": "ok", "default_provider": key}


# ── Diagram validation loop ──────────────────────────────────────────────
# Re-score a generated view and regenerate it while it keeps measurably
# improving. Every knob here is deliberately an *operator* decision: how many
# calls a diagram is worth, which model does the judging, how much of a gain
# earns another pass, and what "good enough" means in the first place.


class ValidationSettingsUpdate(BaseModel):
    enabled: bool | None = None
    max_iterations: int | None = Field(default=None, ge=0, le=5)
    #: Blank means "same provider that drew the diagram".
    provider: str | None = Field(default=None, max_length=100)
    #: 0 = continue on any measurable improvement.
    min_improvement: int | None = Field(default=None, ge=0, le=100)
    #: The quality gate itself — the score a view must reach to be accepted,
    #: and therefore what the loop is iterating *toward*. Tuning the loop
    #: without being able to move the target is half a control.
    min_score: int | None = Field(default=None, ge=0, le=100)


def _validation_settings() -> dict:
    cfg = get_config()
    return {
        "enabled": cfg.validation_enabled,
        "max_iterations": cfg.validation_max_iterations,
        "provider": cfg.validation_provider,
        "min_improvement": cfg.validation_min_improvement,
        "min_score": cfg.view_quality_min_score,
    }


@router.get("/validation")
async def admin_get_validation(x_admin_token: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    return {
        "status": "ok",
        "validation": _validation_settings(),
        "providers": sorted(providers_module.PROVIDERS),
    }


@router.post("/validation")
async def admin_update_validation(
    body: ValidationSettingsUpdate, x_admin_token: str | None = Header(default=None),
) -> dict:
    _require_admin(x_admin_token)
    if body.provider is not None:
        key = body.provider.strip().lower()
        # Blank is meaningful ("use the generation provider"), so it is the one
        # value that skips the known-provider check.
        if key and key not in providers_module.PROVIDERS:
            raise HTTPException(status_code=400, detail="Unknown provider")
        admin_settings_store.set_setting("DIAGRAM_VALIDATION_PROVIDER", key)
    if body.enabled is not None:
        admin_settings_store.set_setting(
            "DIAGRAM_VALIDATION_ENABLED", "true" if body.enabled else "false",
        )
    if body.max_iterations is not None:
        admin_settings_store.set_setting(
            "DIAGRAM_VALIDATION_MAX_ITERATIONS", str(body.max_iterations),
        )
    if body.min_improvement is not None:
        admin_settings_store.set_setting(
            "DIAGRAM_VALIDATION_MIN_IMPROVEMENT", str(body.min_improvement),
        )
    if body.min_score is not None:
        admin_settings_store.set_setting("VIEW_QUALITY_MIN_SCORE", str(body.min_score))
    return {"status": "ok", "validation": _validation_settings()}


# ── Governance / house-style rules ───────────────────────────────────────
# Soft-enforcement only (see app/core/services/governance.py's module
# docstring) — every rule created here feeds the existing semantic-warnings
# corrective pass and the conformance report shown in the Governance
# insights tab. Nothing here can block a generation from completing.


class GovernanceRuleUpdate(BaseModel):
    """Partial update — every field optional, only what's provided changes."""

    kind: str | None = None
    view: str | None = None
    platform: str | None = None
    enabled: bool | None = None
    message: str | None = Field(default=None, max_length=300)
    corridor_id: str | None = Field(default=None, max_length=80)
    shape_type: str | None = Field(default=None, max_length=120)
    overview_field: str | None = None
    min_count: int | None = Field(default=None, ge=1, le=20)
    min_protection: str | None = None
    boundary_only: bool | None = None
    label_pattern: str | None = Field(default=None, max_length=200)


@router.get("/governance-rules")
async def admin_list_governance_rules(x_admin_token: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    rules = await asyncio.to_thread(governance_store.list_rules)
    return {"rules": [r.model_dump() for r in rules]}


@router.post("/governance-rules")
async def admin_create_governance_rule(
    body: GovernanceRule, x_admin_token: str | None = Header(default=None)
) -> dict:
    _require_admin(x_admin_token)
    created = await asyncio.to_thread(governance_store.create_rule, body)
    if created is None:
        raise HTTPException(status_code=400, detail="Could not create rule — message is required")
    return created.model_dump()


@router.patch("/governance-rules/{rule_id}")
async def admin_update_governance_rule(
    rule_id: str, body: GovernanceRuleUpdate, x_admin_token: str | None = Header(default=None)
) -> dict:
    _require_admin(x_admin_token)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = await asyncio.to_thread(governance_store.update_rule, rule_id, **updates)
    if updated is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return updated.model_dump()


@router.delete("/governance-rules/{rule_id}")
async def admin_delete_governance_rule(rule_id: str, x_admin_token: str | None = Header(default=None)) -> dict:
    _require_admin(x_admin_token)
    deleted = await asyncio.to_thread(governance_store.delete_rule, rule_id)
    return {"id": rule_id, "deleted": deleted}
