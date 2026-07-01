"""Human-readable prompt assembly for diagram generation and edits."""

from __future__ import annotations

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


def trim_chat_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    if not messages:
        raise ValueError("At least one user message is required.")
    return replace_historical_tool_inputs(trim_chat_messages_for_prompt(messages))


def conversation_history_lines(trimmed: list[dict[str, str]]) -> list[str]:
    if len(trimmed) <= 1:
        return []
    lines = ["\nConversation so far:"]
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
        "- Update edge colors: AWS #ED7100, GCP #4285F4, Azure #0078D4.",
        "- Return the FULL updated ArchitectureDiagram JSON.",
    ])
    if current_xml and len(current_xml.strip()) > 300:
        lines.append(
            f"\nCurrent draw.io XML (infer topology if architecture JSON is missing):\n"
            f'"""xml\n{current_xml[:12000]}\n"""'
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
    if previous_architecture is not None:
        parts.append(
            "Previous architecture JSON (before the user's last message — for comparison only):\n"
            f"{previous_architecture.model_dump_json()}"
        )
    if current_architecture is not None:
        parts.append(
            "Current architecture JSON (AUTHORITATIVE — modify this; return the full updated object):\n"
            f"{current_architecture.model_dump_json()}"
        )

    parts.extend(conversation_history_lines(trimmed))
    parts.append(f"\nTarget cloud platform: {platform_label}")
    parts.append(f"\nLatest user request:\n{trimmed[-1].get('content', '')}")

    if validation_feedback:
        parts.append(
            f"\n---\nVALIDATION FEEDBACK (fix these issues in your next JSON output):\n{validation_feedback}"
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
            f'"""xml\n{previous_xml}\n"""'
        )
    parts.append(
        "Current diagram XML (AUTHORITATIVE — apply operations to this; copy ids/styles from here):\n"
        f'"""xml\n{current_xml}\n"""'
    )
    if context_text:
        parts.append(f"\nAdditional context from uploaded file or URL:\n{context_text}")

    parts.extend(conversation_history_lines(trimmed))
    parts.append(f"\nLatest user request:\n{trimmed[-1].get('content', '')}")

    if validation_feedback:
        parts.append(f"\n---\n{XML_EDIT_VALIDATION_FEEDBACK}\n{validation_feedback}")
    return "\n".join(parts)
