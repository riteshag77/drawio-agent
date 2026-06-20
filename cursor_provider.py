"""Generate architecture JSON via Cursor Cloud Agents REST API (no local bridge)."""

from __future__ import annotations

import ssl_config  # noqa: F401 — trust OS CA store before httpx

import json
import os
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from models import ArchitectureDiagram

if TYPE_CHECKING:
    from pipeline_log import StepLogger

API_BASE = "https://api.cursor.com/v1"
POLL_INTERVAL_SEC = 2.5
MAX_WAIT_SEC = 300
TERMINAL_STATUSES = frozenset({"FINISHED", "ERROR", "CANCELLED", "EXPIRED"})


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a free-form LLM/agent response."""
    if not text or not text.strip():
        raise ValueError("Cursor returned an empty response.")

    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return json.loads(stripped[start : end + 1])

    raise ValueError("Cursor response did not contain valid architecture JSON.")


def _api_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            for key in ("message", "error", "detail"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    except Exception:
        pass
    text = (response.text or "").strip()
    return text[:500] if text else f"HTTP {response.status_code}"


def _raise_for_api(response: httpx.Response) -> None:
    if response.is_success:
        return

    detail = _api_error_message(response)
    if response.status_code == 401:
        raise ValueError(
            "Cursor API rejected CURSOR_API_KEY (401 Unauthorized). "
            "Create a user API key at https://cursor.com/dashboard → API Keys "
            "(not a Team Admin key)."
        )
    if response.status_code == 403:
        raise ValueError(
            f"Cursor API access denied (403): {detail}. "
            "Ensure your account has Cloud Agents / API access enabled."
        )
    if response.status_code == 429:
        raise ValueError(f"Cursor API rate limit exceeded (429): {detail}")

    raise ValueError(f"Cursor API error ({response.status_code}): {detail}")


def _poll_run(
    client: httpx.Client,
    agent_id: str,
    run_id: str,
    logger: "StepLogger | None",
) -> dict[str, Any]:
    deadline = time.monotonic() + MAX_WAIT_SEC
    last_status = ""

    while time.monotonic() < deadline:
        response = client.get(f"{API_BASE}/agents/{agent_id}/runs/{run_id}")
        _raise_for_api(response)
        run = response.json()
        status = str(run.get("status", "")).upper()

        if status != last_status:
            last_status = status
            if logger:
                logger.log("cursor", f"Run status: {status}", details={"run_id": run_id})

        if status in TERMINAL_STATUSES:
            return run

        time.sleep(POLL_INTERVAL_SEC)

    raise ValueError(
        f"Cursor agent run timed out after {MAX_WAIT_SEC}s (last status: {last_status or 'unknown'})"
    )


def generate_with_cursor(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    logger: "StepLogger | None" = None,
) -> ArchitectureDiagram:
    """
    Call Cursor Cloud Agents API and parse structured ArchitectureDiagram JSON.

    Uses the REST API directly (no cursor-sdk local bridge) so it works on Windows.

    Requires:
      - pip install httpx
      - CURSOR_API_KEY in environment (Cursor Dashboard → API Keys)
    """
    api_key = os.getenv("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "CURSOR_API_KEY environment variable is not set. "
            "Create a key at https://cursor.com/dashboard (API Keys section)."
        )

    model_id = (model or os.getenv("CURSOR_MODEL", "")).strip()
    combined_prompt = (
        f"{system_prompt}\n\n"
        "---\n"
        "IMPORTANT: Respond with ONLY one valid JSON object matching the schema above. "
        "Do not use tools, edit files, or wrap the JSON in markdown.\n\n"
        f"{user_prompt}"
    )

    body: dict[str, Any] = {
        "prompt": {"text": combined_prompt},
        "name": "drawio-architecture",
    }
    if model_id:
        body["model"] = {"id": model_id}

    if logger:
        logger.log(
            "cursor",
            "Creating Cursor cloud agent (REST API)",
            details={
                "model": model_id or "(account default)",
                "prompt_chars": len(combined_prompt),
                "api_base": API_BASE,
            },
        )

    agent_id: str | None = None

    with httpx.Client(
        auth=(api_key, ""),
        timeout=httpx.Timeout(60.0, read=120.0),
        verify=ssl_config.SSL_VERIFY_ENABLED,
    ) as client:
        try:
            create_resp = client.post(f"{API_BASE}/agents", json=body)
            _raise_for_api(create_resp)
            payload = create_resp.json()

            agent = payload.get("agent") or {}
            run = payload.get("run") or {}
            agent_id = str(agent.get("id") or "")
            run_id = str(run.get("id") or agent.get("latestRunId") or "")

            if not agent_id or not run_id:
                raise ValueError(
                    "Cursor API returned an unexpected create response (missing agent/run id)."
                )

            if logger:
                logger.log(
                    "cursor",
                    "Cloud agent created — waiting for run",
                    details={"agent_id": agent_id, "run_id": run_id},
                )

            finished = _poll_run(client, agent_id, run_id, logger)
            status = str(finished.get("status", "")).upper()

            if status != "FINISHED":
                raise ValueError(
                    f"Cursor agent run ended with status={status}. "
                    f"Result: {finished.get('result') or 'no result text'}"
                )

            raw_text = str(finished.get("result") or "")
            if logger:
                logger.log(
                    "cursor",
                    "Run finished",
                    level="success",
                    details={
                        "duration_ms": finished.get("durationMs"),
                        "response_chars": len(raw_text),
                        "preview": raw_text[:240] + ("…" if len(raw_text) > 240 else ""),
                    },
                )

            diagram = ArchitectureDiagram.model_validate(_extract_json(raw_text))

            if logger:
                logger.log(
                    "cursor",
                    "Parsed response into ArchitectureDiagram",
                    level="success",
                    details={"nodes": len(diagram.nodes), "edges": len(diagram.edges)},
                )

            return diagram
        finally:
            if agent_id:
                try:
                    client.post(f"{API_BASE}/agents/{agent_id}/archive")
                except Exception:
                    pass
