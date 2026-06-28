"""Parse model responses into structured payloads."""

from __future__ import annotations

import json
import re
from typing import Any


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
