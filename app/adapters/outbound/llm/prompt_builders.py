"""Human-readable prompt assembly for diagram generation and edits."""

from __future__ import annotations

from typing import Any

from app.adapters.outbound.llm.prompts.system import XML_EDIT_VALIDATION_FEEDBACK
from app.adapters.outbound.utils.chat import (
    build_applied_suggestions_block,
    replace_historical_tool_inputs,
    trim_chat_messages_for_prompt,
)

PLATFORM_LABELS = {
    "aws": "Amazon Web Services (AWS)",
    "gcp": "Google Cloud Platform (GCP)",
    "azure": "Microsoft Azure",
    "multi": "Multi-Cloud / Hybrid",
}

_PLATFORM_EDGE_COLORS = {
    "aws": "#232F3E",
    "gcp": "#4285F4",
    "azure": "#0078D4",
    "multi": "#8C4FFF",
}

# Cap XML snippets in LLM human prompts (same ballpark as platform-convert path).
_MAX_XML_PROMPT_CHARS = 12_000

_NODE_DUMP_KEYS = ("id", "label", "shape_type", "is_container", "parent_id", "step_number", "description")
_EDGE_DUMP_KEYS = ("source_id", "target_id", "label", "style", "color")


def architecture_prompt_json(architecture: Any) -> str:
    """Compact ArchitectureDiagram JSON for prompts — drops null geometry and empty fields."""
    import json

    if architecture is None:
        return "{}"
    nodes_out: list[dict] = []
    for node in architecture.nodes:
        raw = node.model_dump() if hasattr(node, "model_dump") else dict(node)
        item: dict[str, Any] = {
            "id": raw["id"],
            "label": raw.get("label") or "",
            "shape_type": raw.get("shape_type") or "",
        }
        if raw.get("is_container"):
            item["is_container"] = True
        if raw.get("parent_id") is not None:
            item["parent_id"] = raw["parent_id"]
        if "step_number" in raw:
            item["step_number"] = raw["step_number"]
        desc = raw.get("description")
        if desc:
            item["description"] = desc
        nodes_out.append(item)

    edges_out: list[dict] = []
    for edge in architecture.edges:
        raw = edge.model_dump() if hasattr(edge, "model_dump") else dict(edge)
        item = {
            "source_id": raw["source_id"],
            "target_id": raw["target_id"],
        }
        for k in ("label", "style", "color"):
            if raw.get(k) is not None and raw.get(k) != "":
                item[k] = raw[k]
        edges_out.append(item)

    payload: dict[str, Any] = {
        "nodes": nodes_out,
        "edges": edges_out,
        "suggestions": list(getattr(architecture, "suggestions", None) or []),
    }
    title = getattr(architecture, "title", None)
    if title:
        payload["title"] = title
    pattern_id = getattr(architecture, "pattern_id", None)
    if pattern_id:
        payload["pattern_id"] = pattern_id
    flow_steps = getattr(architecture, "flow_steps", None) or []
    if flow_steps:
        payload["flow_steps"] = [
            fs.model_dump() if hasattr(fs, "model_dump") else fs for fs in flow_steps
        ]
    return json.dumps(payload, separators=(",", ":"))


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


def build_platform_conversion_block(
    previous_platform: str,
    target_platform: str,
    *,
    current_xml: str | None = None,
) -> str:
    previous = (previous_platform or "aws").strip().lower()
    target = (target_platform or "aws").strip().lower()
    previous_label = PLATFORM_LABELS.get(previous, previous.upper())
    target_label = PLATFORM_LABELS.get(target, target.upper())

    lines = [
        "PLATFORM CONVERSION (authoritative):",
        f"Convert the current architecture from {previous_label} to {target_label}.",
        "- Preserve topology: same node ids, container hierarchy, step_number order, and edge pairs.",
        f"- Replace every shape_type with the equivalent {target} key from the Platform Shape Catalogue.",
    ]
    if target == "multi":
        lines.append(
            "- Multi-cloud target: use mixed providers only where the design is explicitly hybrid; "
            "otherwise map each tier to the closest single-provider equivalent."
        )
    else:
        lines.append(f"- Never mix aws_*, gcp_*, azure_* shape keys — use {target}_* keys only.")
    lines.extend([
        f"- Edge color for {target_label}: {_PLATFORM_EDGE_COLORS.get(target, '#232F3E')}.",
        "- Return the FULL updated ArchitectureDiagram JSON.",
    ])
    if current_xml and len(current_xml.strip()) > 300:
        snippet = current_xml[:_MAX_XML_PROMPT_CHARS]
        lines.append(
            f"\nCurrent draw.io XML (infer topology if architecture JSON is missing):\n"
            f'"""xml\n{snippet}\n"""'
        )
    return "\n".join(lines)


def build_chat_prompt(
    messages: list[dict[str, str]],
    platform_label: str,
    current_architecture=None,
    previous_architecture=None,
    validation_feedback: str | None = None,
    *,
    platform_convert: bool = False,
    previous_platform: str | None = None,
    target_platform: str = "aws",
    current_xml: str | None = None,
    applied_suggestions: list[str] | None = None,
) -> str:
    trimmed = trim_chat_messages(messages)
    parts: list[str] = []

    applied_block = build_applied_suggestions_block(applied_suggestions)
    if applied_block:
        parts.append(applied_block)

    if platform_convert:
        parts.append(
            build_platform_conversion_block(
                previous_platform or "aws",
                target_platform,
                current_xml=current_xml if current_architecture is None else None,
            )
        )
    # On edits, current architecture is authoritative — skip redundant previous dump
    # unless converting platforms (diff context helps).
    if previous_architecture is not None and platform_convert:
        parts.append(
            "Previous architecture JSON (before the user's last message — for comparison only):\n"
            f"{architecture_prompt_json(previous_architecture)}"
        )
    if current_architecture is not None:
        parts.append(
            "Current architecture JSON (AUTHORITATIVE — modify this; return the full updated object):\n"
            f"{architecture_prompt_json(current_architecture)}"
        )

    parts.extend(conversation_history_lines(trimmed))
    parts.append(f"Target cloud platform: {platform_label}")
    parts.append(f"Latest user request:\n{trimmed[-1].get('content', '')}")

    if validation_feedback:
        parts.append(
            f"---\nVALIDATION FEEDBACK (fix these issues in your next JSON output):\n{validation_feedback}"
        )
    return "\n".join(parts)


def build_edit_prompt(
    messages: list[dict[str, str]],
    current_xml: str,
    previous_xml: str | None = None,
    validation_feedback: str | None = None,
    context_text: str | None = None,
    applied_suggestions: list[str] | None = None,
) -> str:
    trimmed = trim_chat_messages(messages)
    parts: list[str] = []

    applied_block = build_applied_suggestions_block(applied_suggestions)
    if applied_block:
        parts.append(applied_block)

    if previous_xml:
        parts.append(
            "Previous diagram XML (before the user's last message — for comparison only):\n"
            f'"""xml\n{previous_xml[:_MAX_XML_PROMPT_CHARS]}\n"""'
        )
    parts.append(
        "Current diagram XML (AUTHORITATIVE — apply operations to this; copy ids/styles from here):\n"
        f'"""xml\n{current_xml[:_MAX_XML_PROMPT_CHARS]}\n"""'
    )
    if context_text:
        parts.append(f"Additional context from uploaded file or URL:\n{context_text[:8000]}")

    parts.extend(conversation_history_lines(trimmed))
    parts.append(f"Latest user request:\n{trimmed[-1].get('content', '')}")

    if validation_feedback:
        parts.append(f"---\n{XML_EDIT_VALIDATION_FEEDBACK}\n{validation_feedback}")
    return "\n".join(parts)
