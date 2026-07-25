"""
Chat helpers — trim/format conversation history for LLM prompts.
"""

from __future__ import annotations

import re

ARCHITECTURE_OMITTED_PLACEHOLDER = (
    "[Previous diagram state omitted — see current architecture JSON in the latest turn.]"
)

XML_OMITTED_PLACEHOLDER = (
    "[Previous diagram XML omitted — see current diagram XML in the latest turn.]"
)

MAX_CHAT_TURNS_WITH_FULL_HISTORY = 4


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


def build_suggestions_instruction_block(include_suggestions: bool) -> str:
    """Tell the LLM whether `suggestions` must be populated this turn."""
    if include_suggestions:
        return (
            "## Suggestions (REQUIRED)\n"
            "Populate `suggestions` with exactly 2–3 specific, actionable architecture improvements "
            "(e.g. services to add, security hardening, resilience, cost). "
            "Never leave `suggestions` empty on this turn."
        )
    return (
        "## Suggestions\n"
        "Leave `suggestions` as an empty list — the user has suggestions disabled."
    )


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
