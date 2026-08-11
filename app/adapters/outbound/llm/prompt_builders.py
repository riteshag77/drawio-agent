"""Human-readable prompt assembly — shared utilities for XML and Mermaid prompts."""

from __future__ import annotations

from typing import Any

from app.adapters.outbound.utils.chat import (
    build_applied_suggestions_block,
    build_suggestions_instruction_block,
    replace_historical_tool_inputs,
    trim_chat_messages_for_prompt,
)

PLATFORM_LABELS = {
    "aws": "Amazon Web Services (AWS)",
    "gcp": "Google Cloud Platform (GCP)",
    "azure": "Microsoft Azure",
    "multi": "Multi-Cloud / Hybrid",
}

def trim_chat_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if not messages:
        raise ValueError("At least one user message is required.")
    return replace_historical_tool_inputs(trim_chat_messages_for_prompt(messages))


def conversation_history_lines(trimmed: list[dict[str, str]]) -> list[str]:
    if len(trimmed) <= 1:
        return []
    lines = ["Conversation so far:"]
    for message in trimmed[:-1]:
        role = message.get("role", "user").upper()
        lines.append(f"{role}: {message.get('content', '')}")
    return lines


def build_chat_prompt(
    messages: list[dict[str, str]],
    platform_label: str,
    *args: Any,
    validation_feedback: str | None = None,
    applied_suggestions: list[str] | None = None,
    include_suggestions: bool = False,
    **kwargs: Any,
) -> str:
    """Build Mermaid chat human prompt. Extra *args/**kwargs ignored (legacy callers)."""
    trimmed = trim_chat_messages(messages)
    parts: list[str] = []

    sugg_block = build_suggestions_instruction_block(include_suggestions)
    if sugg_block:
        parts.append(sugg_block)

    applied_block = build_applied_suggestions_block(applied_suggestions)
    if applied_block:
        parts.append(applied_block)

    parts.extend(conversation_history_lines(trimmed))
    parts.append(f"Target cloud platform: {platform_label}")
    parts.append(f"Latest user request:\n{trimmed[-1].get('content', '')}")

    if validation_feedback:
        parts.append(
            f"---\nVALIDATION FEEDBACK (fix these issues in your next output):\n{validation_feedback}"
        )
    return "\n".join(parts)
