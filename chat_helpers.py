"""
Chat helpers — patterns from next-ai-draw-io lib/chat-helpers.ts
"""

from __future__ import annotations

from models import ArchitectureDiagram

ARCHITECTURE_OMITTED_PLACEHOLDER = (
    "[Previous diagram state omitted — see current architecture JSON in the latest turn.]"
)

MAX_CHAT_TURNS_WITH_FULL_HISTORY = 4


def is_minimal_architecture(architecture: ArchitectureDiagram | None) -> bool:
    """True when the diagram is empty or trivially small (skip VLM validation)."""
    if architecture is None:
        return True
    services = [n for n in architecture.nodes if not n.is_container]
    return len(services) <= 1 and len(architecture.edges) == 0


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
