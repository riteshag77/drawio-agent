"""Parse model responses into structured payloads."""

from __future__ import annotations

import re


def extract_mermaid(text: str) -> str:
    """Pull Mermaid source from fenced or plain model output."""
    if not text or not text.strip():
        raise ValueError("Empty Mermaid response from model.")
    stripped = text.strip()
    fenced = re.search(r"```(?:mermaid)?\s*([\s\S]*?)```", stripped)
    return fenced.group(1).strip() if fenced else stripped
