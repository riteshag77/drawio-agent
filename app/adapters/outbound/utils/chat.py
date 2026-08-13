"""
Chat helpers — trim/format conversation history for LLM prompts.
"""

from __future__ import annotations

from typing import Any


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


def _suggestion_text(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("text") or item.get("suggestion") or "")
    return str(getattr(item, "text", "") or "")


def filter_applied_suggestions(
    suggestions: list[Any] | None,
    applied: list[Any] | None,
) -> list[dict[str, str]]:
    """Drop already-applied items; return JSON-ready {text, category} dicts."""
    from app.domain.entities.xml_diagram import normalize_suggestions

    normalized = normalize_suggestions(suggestions)
    if not normalized:
        return []
    applied_norm = {
        normalize_suggestion(_suggestion_text(item))
        for item in (applied or [])
        if _suggestion_text(item)
    }
    out: list[dict[str, str]] = []
    for item in normalized:
        if normalize_suggestion(item.text) in applied_norm:
            continue
        out.append({"text": item.text, "category": item.category})
    return out


def build_applied_suggestions_block(applied: list[Any] | None) -> str:
    if not applied:
        return ""
    lines = [
        "Already implemented suggestions (do NOT repeat these in `suggestions`; propose new improvements only):"
    ]
    for item in applied:
        text = _suggestion_text(item)
        if text:
            lines.append(f"- {text}")
    return "\n".join(lines)


def build_suggestions_instruction_block(include_suggestions: bool) -> str:
    """Tell the LLM whether `suggestions` must be populated this turn."""
    if include_suggestions:
        return (
            "## Suggestions (REQUIRED)\n"
            "Populate `suggestions` with exactly 2–3 objects, each with:\n"
            "- `text`: specific, actionable architecture improvement "
            "(e.g. services to add, security hardening, resilience, cost)\n"
            "- `category`: one of `network`, `logical`, `security`, `data_flow`, `general` "
            "— where the change should apply (match a selected diagram view when possible; "
            "use `general` for cross-cutting cost/ops ideas)\n"
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
