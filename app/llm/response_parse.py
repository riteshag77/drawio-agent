"""Parse model responses into structured payloads."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

import app.core.ssl_trust  # noqa: F401 — OS CA store before gateway HTTP calls


def extract_json(text: str) -> dict:
    """Extract a JSON object from raw model text (plain, fenced, or embedded)."""
    if not text or not text.strip():
        raise ValueError("Empty response from model.")

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

    raise ValueError("Response did not contain valid JSON.")


def message_text(response: Any) -> str:
    """Normalize LangChain or plain-string responses to text."""
    if isinstance(response, str):
        return response
    content = getattr(response, "content", None)
    if isinstance(content, str):
        return content
    if content is not None:
        return str(content)
    return str(response)


def openai_chat_messages(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """Convert LangChain chat messages to OpenAI-compatible role/content dicts."""
    payload: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, SystemMessage):
            role = "system"
        elif isinstance(message, HumanMessage):
            role = "user"
        elif isinstance(message, AIMessage):
            role = "assistant"
        else:
            role = getattr(message, "type", "user")
            if role == "human":
                role = "user"
            elif role == "ai":
                role = "assistant"
        content = message.content
        if not isinstance(content, str):
            content = str(content)
        payload.append({"role": role, "content": content})
    return payload


def normalize_message_content(content: Any) -> str:
    """Flatten OpenAI message content (string or text blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text is not None:
                    parts.append(str(text))
            elif block is not None:
                parts.append(str(block))
        return "\n".join(parts)
    if content is not None:
        return str(content)
    return ""


def parse_http_json(response: httpx.Response) -> dict[str, Any]:
    """Parse an HTTP response body as JSON with a readable error on failure."""
    raw = response.text
    if not raw.strip():
        raise ValueError(f"Gateway returned empty response body (HTTP {response.status_code}).")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = raw.strip()[:500]
        raise ValueError(
            f"Gateway returned non-JSON body (HTTP {response.status_code}): {preview!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Gateway JSON root must be an object, got {type(payload).__name__}.")
    return payload


def chat_completion_text(payload: dict[str, Any]) -> str:
    """Extract assistant text from an OpenAI-style chat completion JSON body."""
    if error := payload.get("error"):
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or str(error)
        else:
            message = str(error)
        raise ValueError(f"Chat completion API error: {message}")

    choices = payload.get("choices")
    if not choices:
        raise ValueError(f"Chat completion response missing choices: {list(payload.keys())}")

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValueError("Chat completion response missing message content.")

    content = normalize_message_content(message.get("content"))
    if content.strip():
        return content
    reasoning = normalize_message_content(message.get("reasoning_content"))
    if reasoning.strip():
        return reasoning
    raise ValueError("Chat completion returned empty message content.")


def invoke_gateway_json(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[BaseMessage],
    output_type: type,
    timeout: int,
) -> Any:
    """Call an OpenAI-compatible gateway directly and validate JSON output.

    LangChain's ChatOpenAI assumes OpenAI SDK response objects; some gateways
    return payloads that break ``response.model_dump()`` — this path avoids that.
    """
    if not api_key:
        raise ValueError("Gateway API key is not set.")

    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "temperature": 0,
        "messages": openai_chat_messages(messages),
    }

    try:
        response = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text[:500]
        raise ValueError(
            f"Gateway chat completion failed ({exc.response.status_code}): {detail or exc}"
        ) from exc
    except httpx.HTTPError as exc:
        raise ValueError(f"Gateway chat completion request failed: {exc}") from exc

    api_payload = parse_http_json(response)
    payload = extract_json(chat_completion_text(api_payload))
    return output_type.model_validate(payload)


def invoke_json(llm: Any, messages: list, output_type: type) -> Any:
    """Call the model and validate the response as a Pydantic ``output_type``."""
    raw = llm.invoke(messages)
    payload = extract_json(message_text(raw))
    return output_type.model_validate(payload)


def extract_mermaid(text: str) -> str:
    """Pull Mermaid source from fenced or plain model output."""
    if not text or not text.strip():
        raise ValueError("Empty Mermaid response from model.")
    stripped = text.strip()
    fenced = re.search(r"```(?:mermaid)?\s*([\s\S]*?)```", stripped)
    return fenced.group(1).strip() if fenced else stripped
