"""
Chat helpers — patterns from next-ai-draw-io lib/chat-helpers.ts
"""

from __future__ import annotations

import re

from app.models import ArchitectureDiagram

ARCHITECTURE_OMITTED_PLACEHOLDER = (
    "[Previous diagram state omitted — see current architecture JSON in the latest turn.]"
)

XML_OMITTED_PLACEHOLDER = (
    "[Previous diagram XML omitted — see current diagram XML in the latest turn.]"
)

MAX_CHAT_TURNS_WITH_FULL_HISTORY = 4
MIN_REAL_DIAGRAM_LENGTH = 300


def is_minimal_diagram(xml: str | None) -> bool:
    """
    True when draw.io XML has no real diagram content (only root cells 0/1).
    Works for both next-ai style (id=\"2\") and semantic cell ids from draftsman.
    """
    if not xml or not xml.strip():
        return True
    if len(xml.strip()) < MIN_REAL_DIAGRAM_LENGTH:
        return True
    cell_ids = re.findall(r'<mxCell\b[^>]*\bid="([^"]+)"', xml)
    content_cells = [cid for cid in cell_ids if cid not in ("0", "1")]
    return len(content_cells) == 0


def is_minimal_architecture(architecture: ArchitectureDiagram | None) -> bool:
    """True when the diagram is empty or trivially small (skip VLM validation)."""
    if architecture is None:
        return True
    services = [n for n in architecture.nodes if not n.is_container]
    return len(services) <= 1 and len(architecture.edges) == 0


def replace_historical_tool_inputs(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Replace large historical diagram XML/JSON in assistant messages with placeholders.
    Port of replaceHistoricalToolInputs (chat-helpers.ts).
    """
    result: list[dict[str, str]] = []
    for msg in messages:
        role = (msg.get("role") or "user").lower()
        content = msg.get("content") or ""
        if role == "assistant" and len(content) > 800:
            if "<mxCell" in content or "<mxfile" in content:
                result.append({"role": "assistant", "content": XML_OMITTED_PLACEHOLDER})
                continue
            if '"nodes"' in content and '"edges"' in content:
                result.append({"role": "assistant", "content": ARCHITECTURE_OMITTED_PLACEHOLDER})
                continue
        result.append(msg)
    return result


def normalize_suggestion(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def filter_applied_suggestions(
    suggestions: list[str] | None,
    applied: list[str] | None,
) -> list[str]:
    if not suggestions:
        return []
    if not applied:
        return list(suggestions)
    applied_norm = {normalize_suggestion(item) for item in applied}
    return [item for item in suggestions if normalize_suggestion(item) not in applied_norm]


def build_applied_suggestions_block(applied: list[str] | None) -> str:
    if not applied:
        return ""
    lines = [
        "Already implemented suggestions (do NOT repeat these in `suggestions`; propose new improvements only):"
    ]
    lines.extend(f"- {item}" for item in applied)
    return "\n".join(lines)


def trim_chat_messages_for_prompt(
    messages: list[dict[str, str]],
    *,
    keep_full_turns: int = MAX_CHAT_TURNS_WITH_FULL_HISTORY,
) -> list[dict[str, str]]:
    """
    Trim older assistant messages to save tokens while preserving recent conversation.
    Mirrors replaceHistoricalToolInputs — large diagram JSON stays only in current_architecture.
    """
    if len(messages) <= keep_full_turns * 2:
        return messages

    trimmed: list[dict[str, str]] = []
    preserve_from = len(messages) - (keep_full_turns * 2)

    for idx, msg in enumerate(messages):
        role = (msg.get("role") or "user").lower()
        content = msg.get("content") or ""

        if idx < preserve_from and role == "assistant" and len(content) > 400:
            trimmed.append({
                "role": "assistant",
                "content": ARCHITECTURE_OMITTED_PLACEHOLDER,
            })
        else:
            trimmed.append(msg)

    return trimmed
