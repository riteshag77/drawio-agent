"""Optional SDK-backed providers (isolated from LangChain path)."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.llm.providers import ProviderSpec
from app.adapters.outbound.llm.response_parse import extract_json

if TYPE_CHECKING:
    from app.core.logging import StepLogger


def format_sdk_error(error: Any, *, label: str = "SDK") -> str:
    """Format SDK errors with any metadata the client exposes."""
    parts: list[str] = [str(getattr(error, "message", error) or "unknown error")]
    for field, value in (
        ("code", getattr(error, "code", None)),
        ("status", getattr(error, "status", None)),
        ("request_id", getattr(error, "request_id", None)),
    ):
        if value:
            parts.append(f"{field}={value}")

    details = getattr(error, "details", None)
    if details:
        try:
            parts.append(f"details={json.dumps(details, default=str)[:800]}")
        except TypeError:
            parts.append(f"details={repr(details)[:800]}")

    if getattr(error, "is_retryable", False):
        parts.append("retryable=true")
    if cause := getattr(error, "cause", None):
        parts.append(f"cause={cause!r}")

    suffix = f" ({', '.join(parts[1:])})" if len(parts) > 1 else ""
    return f"{label} error: {parts[0]}{suffix}"


def call_sdk_provider(
    spec: ProviderSpec,
    system_prompt: str,
    user_prompt: str,
    output_type: type,
    logger: StepLogger | None = None,
) -> Any:
    """Dispatch to the SDK implementation registered for ``spec.key``."""
    if spec.key == "cursor":
        return _call_cursor_sdk(spec, system_prompt, user_prompt, output_type, logger)
    raise ValueError(f"No SDK client registered for provider: {spec.key}")


def _call_cursor_sdk(
    spec: ProviderSpec,
    system_prompt: str,
    user_prompt: str,
    output_type: type,
    logger: StepLogger | None,
) -> Any:
    try:
        from app.core.cursor_bridge import apply_windows_bridge_patch
        from cursor_sdk import Agent, AgentOptions, CloudAgentOptions, LocalAgentOptions
        from cursor_sdk.errors import CursorAgentError
    except ImportError as exc:
        raise ValueError(
            "Cursor provider requires the 'cursor-sdk' package. "
            "Install it with: pip install cursor-sdk"
        ) from exc

    api_key = os.getenv(spec.api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"{spec.api_key_env} environment variable is not set.")

    apply_windows_bridge_patch()
    runtime = os.getenv("CURSOR_RUNTIME", "local").strip().lower()
    combined = f"{system_prompt}\n\n---\nRespond with ONLY valid JSON.\n\n{user_prompt}"

    BASE_DIR = get_config().base_dir()

    if runtime == "cloud":
        options = AgentOptions(
            api_key=api_key,
            model=spec.default_model,
            name="drawio-agent",
            cloud=CloudAgentOptions(repos=[]),
        )
    else:
        options = AgentOptions(
            api_key=api_key,
            model=spec.default_model,
            name="drawio-agent",
            local=LocalAgentOptions(cwd=str(BASE_DIR), setting_sources=[]),
        )

    if logger:
        logger.log(
            "sdk",
            f"Calling {spec.key} ({spec.default_model}, {runtime})",
            details={"provider": spec.key, "runtime": runtime, "prompt_chars": len(combined)},
        )

    try:
        result = Agent.prompt(combined, options)
    except CursorAgentError as error:
        if logger:
            logger.log(
                "sdk",
                f"{spec.key} request failed",
                level="error",
                details={"message": error.message, "code": error.code, "request_id": error.request_id},
            )
        raise ValueError(format_sdk_error(error, label="Cursor SDK")) from error

    if result.status != "finished":
        preview = str(result.result or "")[:500]
        raise ValueError(
            f"{spec.key} ended with status={result.status}. "
            f"Result preview: {preview or 'no result text'}"
        )

    raw_text = str(result.result or "")
    if logger:
        logger.log("sdk", f"{spec.key} finished", level="success", details={"chars": len(raw_text)})

    return output_type.model_validate(extract_json(raw_text))
