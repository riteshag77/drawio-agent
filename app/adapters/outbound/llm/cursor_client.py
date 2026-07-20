"""Cursor Cloud Agents API client (not OpenAI chat/completions)."""

from __future__ import annotations

import json
import os
import re
import time
from typing import TYPE_CHECKING, Any, Callable

import httpx

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.llm.providers import ProviderSpec, resolve_gateway_base_url

if TYPE_CHECKING:
    from app.core.logging import StepLogger

ProgressFn = Callable[[str, str, dict[str, Any] | None], None]

_TERMINAL_RUN_STATUSES = frozenset({"FINISHED", "ERROR", "CANCELLED", "EXPIRED"})
_ACTIVE_RUN_STATUSES = frozenset({"CREATING", "RUNNING", "PENDING"})


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _emit(
    logger: StepLogger | None,
    on_progress: ProgressFn | None,
    step: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> None:
    if logger:
        logger.log(step, message, details=details)
    if on_progress:
        on_progress(step, message, details)


def _resolve_model_id(spec: ProviderSpec) -> str | None:
    model = (spec.default_model or "").strip()
    if not model or model.lower() == "auto":
        return None
    return model


def _build_agent_prompt(system_prompt: str, human_content: str, output_schema: dict[str, Any]) -> str:
    schema_text = json.dumps(output_schema, ensure_ascii=False, indent=2)
    return (
        f"{system_prompt}\n\n"
        "---\n\n"
        f"{human_content}\n\n"
        "---\n\n"
        "Return ONLY a single JSON object (no markdown fences, no commentary) matching this schema:\n"
        f"{schema_text}"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Cursor agent returned empty text")

    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
    if fence:
        return json.loads(fence.group(1))

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])

    raise ValueError("Could not parse JSON from Cursor agent response")


def _create_agent(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    *,
    prompt_text: str,
    model_id: str | None,
    repo_url: str | None,
) -> tuple[str, str]:
    payload: dict[str, Any] = {
        "prompt": {"text": prompt_text},
        "mode": "agent",
    }
    if model_id:
        payload["model"] = {"id": model_id}
    if repo_url:
        payload["repos"] = [{"url": repo_url}]

    resp = client.post(f"{base_url}/v1/agents", headers=_auth_headers(api_key), json=payload)
    if resp.status_code >= 400:
        raise ValueError(f"Cursor agent create failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    agent = data.get("agent") or {}
    run = data.get("run") or {}
    agent_id = agent.get("id") or data.get("id")
    run_id = run.get("id") or agent.get("latestRunId")
    if not agent_id or not run_id:
        raise ValueError(f"Cursor agent create returned unexpected payload: {data}")
    return str(agent_id), str(run_id)


def _poll_run(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    *,
    agent_id: str,
    run_id: str,
    timeout_seconds: int,
    logger: StepLogger | None,
    on_progress: ProgressFn | None,
) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_status = ""
    while time.monotonic() < deadline:
        resp = client.get(
            f"{base_url}/v1/agents/{agent_id}/runs/{run_id}",
            headers=_auth_headers(api_key),
        )
        if resp.status_code >= 400:
            raise ValueError(f"Cursor run poll failed ({resp.status_code}): {resp.text}")

        data = resp.json()
        status = str(data.get("status") or "").upper()
        if status != last_status:
            _emit(logger, on_progress, "cursor", f"Agent run status: {status}", {"status": status})
            last_status = status

        if status in _TERMINAL_RUN_STATUSES:
            if status == "FINISHED":
                result_text = (data.get("result") or "").strip()
                if result_text:
                    return result_text
                raise ValueError("Cursor agent finished without a result payload")
            raise ValueError(f"Cursor agent run ended with status {status}")

        if status not in _ACTIVE_RUN_STATUSES and status:
            raise ValueError(f"Cursor agent run entered unexpected status {status}")

        time.sleep(2.0)

    raise ValueError(f"Cursor agent run timed out after {timeout_seconds}s")


def _delete_agent(client: httpx.Client, base_url: str, api_key: str, agent_id: str) -> None:
    try:
        client.delete(f"{base_url}/v1/agents/{agent_id}", headers=_auth_headers(api_key))
    except Exception:
        pass


def call_cursor_agents(
    system_prompt: str,
    human_content: str,
    spec: ProviderSpec,
    output_type: type,
    *,
    logger: StepLogger | None = None,
    on_progress: ProgressFn | None = None,
) -> Any:
    """Run a Cursor Cloud Agent and parse structured output from the final run text."""
    api_key = os.getenv(spec.api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"{spec.api_key_env} environment variable is not set.")

    base_url = resolve_gateway_base_url(spec)
    model_id = _resolve_model_id(spec)
    repo_url = (os.getenv("CURSOR_REPO_URL") or "").strip() or None
    timeout_seconds = get_config().cursor_timeout_seconds
    prompt_text = _build_agent_prompt(system_prompt, human_content, output_type.model_json_schema())

    _emit(logger, on_progress, "cursor", "Creating Cursor cloud agent…", {"model": model_id or "default"})
    verify = get_config().ssl_verify

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=30.0), verify=verify) as client:
        agent_id, run_id = _create_agent(
            client, base_url, api_key,
            prompt_text=prompt_text, model_id=model_id, repo_url=repo_url,
        )
        _emit(
            logger, on_progress, "cursor",
            "Waiting for Cursor agent run to complete…",
            {"agent_id": agent_id, "run_id": run_id},
        )
        try:
            result_text = _poll_run(
                client, base_url, api_key,
                agent_id=agent_id, run_id=run_id,
                timeout_seconds=timeout_seconds,
                logger=logger, on_progress=on_progress,
            )
        finally:
            _delete_agent(client, base_url, api_key, agent_id)

    parsed = _extract_json_object(result_text)
    return output_type.model_validate(parsed)
