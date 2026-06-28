"""Public LLM orchestration API for diagram generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.llm.llm_client import call_llm
from app.llm.prompt_builders import (
    PLATFORM_LABELS,
    build_chat_prompt,
    build_edit_prompt,
    build_platform_conversion_block,
)
from app.llm.prompts.simple import MERMAID_SYSTEM_PROMPT
from app.llm.prompts.system import get_cursor_system_prompt, get_edit_system_prompt, get_system_prompt
from app.llm.providers import MODEL_IDS, get_provider, normalize_provider_key
from app.llm.response_parse import extract_mermaid
from app.models import ArchitectureDiagram, EditResponse, MermaidDiagram
from app.patterns.library import find_matching_patterns, format_patterns_for_prompt

if TYPE_CHECKING:
    from app.core.logging import StepLogger

# Backward-compatible re-exports for tests and diagnostic scripts.
from app.llm.response_parse import message_text as _message_text
from app.llm.sdk_client import format_sdk_error as _format_cursor_error
from app.llm.providers import normalize_opencode_model as _normalize_opencode_model


def _platform_label(platform: str) -> str:
    key = (platform or "aws").strip().lower()
    return PLATFORM_LABELS.get(key, PLATFORM_LABELS["aws"])


def _build_system_prompt(
    provider_key: str,
    *,
    model_id: str,
    minimal_style: bool,
    custom_system_message: str,
    is_edit: bool,
    patterns_block: str,
) -> str:
    if provider_key == "cursor":
        return get_cursor_system_prompt(
            model_id=model_id,
            minimal_style=minimal_style,
            custom_system_message=custom_system_message,
            is_edit_mode=is_edit,
        )
    return get_system_prompt(
        model_id=model_id,
        minimal_style=minimal_style,
        provider=provider_key,
        custom_system_message=custom_system_message,
        is_edit_mode=is_edit,
        include_schema=True,
        patterns_block=patterns_block,
    )


def _diagram_summary(diagram: ArchitectureDiagram, *, platform_convert: bool, platform: str) -> str:
    if platform_convert:
        label = PLATFORM_LABELS.get(platform.lower(), platform)
        return f"Converted diagram to {label}: {len(diagram.nodes)} nodes, {len(diagram.edges)} edges."
    containers = sum(1 for node in diagram.nodes if node.is_container)
    summary = (
        f"Diagram updated: {len(diagram.nodes)} nodes ({containers} containers), "
        f"{len(diagram.edges)} edges."
    )
    if diagram.suggestions:
        summary += f" {len(diagram.suggestions)} suggestion(s) included."
    return summary


def generate_architecture_json(
    user_request: str,
    provider: str = "openai",
    platform: str = "aws",
    logger: StepLogger | None = None,
    messages: list[dict[str, str]] | None = None,
    current_architecture=None,
    previous_architecture=None,
    minimal_style: bool = False,
    custom_system_message: str = "",
    validation_feedback: str | None = None,
    platform_convert: bool = False,
    previous_platform: str | None = None,
    current_xml: str | None = None,
) -> ArchitectureDiagram:
    platform_key = (platform or "aws").strip().lower()
    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]
    is_edit = bool(current_architecture or len(chat_messages) > 1 or platform_convert)

    patterns_block = ""
    if not is_edit:
        match_text = user_request or chat_messages[-1]["content"]
        matched = find_matching_patterns(match_text, platform_key)
        if matched:
            patterns_block = format_patterns_for_prompt(matched, platform_key)

    if logger:
        logger.log(
            "prompt",
            "Prepared architecture request",
            details={
                "platform": platform_key,
                "provider": provider_key,
                "turns": len(chat_messages),
                "is_edit": is_edit,
                "platform_convert": platform_convert,
            },
        )

    spec = get_provider(provider_key)
    system_prompt = _build_system_prompt(
        provider_key,
        model_id=spec.default_model,
        minimal_style=minimal_style,
        custom_system_message=custom_system_message,
        is_edit=is_edit,
        patterns_block=patterns_block,
    )
    human_content = build_chat_prompt(
        chat_messages,
        _platform_label(platform_key),
        current_architecture,
        previous_architecture,
        validation_feedback,
        platform_convert=platform_convert,
        previous_platform=previous_platform,
        target_platform=platform_key,
        current_xml=current_xml,
    )

    result = call_llm(system_prompt, human_content, provider_key, ArchitectureDiagram, logger)

    if logger:
        containers = sum(1 for node in result.nodes if node.is_container)
        logger.log(
            "architecture",
            "Architecture JSON validated",
            level="success",
            details={"nodes": len(result.nodes), "containers": containers, "edges": len(result.edges)},
        )
    return result


def generate_architecture_from_chat(
    messages: list[dict[str, str]],
    provider: str = "openai",
    platform: str = "aws",
    current_architecture=None,
    previous_architecture=None,
    logger: StepLogger | None = None,
    minimal_style: bool = False,
    custom_system_message: str = "",
    validation_feedback: str | None = None,
    platform_convert: bool = False,
    previous_platform: str | None = None,
    current_xml: str | None = None,
) -> tuple[ArchitectureDiagram, str]:
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("Chat must end with a user message.")

    diagram = generate_architecture_json(
        user_request=messages[-1]["content"],
        provider=provider,
        platform=platform,
        logger=logger,
        messages=messages,
        current_architecture=current_architecture,
        previous_architecture=previous_architecture,
        minimal_style=minimal_style,
        custom_system_message=custom_system_message,
        validation_feedback=validation_feedback,
        platform_convert=platform_convert,
        previous_platform=previous_platform,
        current_xml=current_xml,
    )
    summary = _diagram_summary(diagram, platform_convert=platform_convert, platform=platform)
    return diagram, summary


def generate_edit_operations(
    messages: list[dict[str, str]],
    provider: str = "openai",
    current_xml: str = "",
    previous_xml: str | None = None,
    logger: StepLogger | None = None,
    custom_system_message: str = "",
    validation_feedback: str | None = None,
    context_text: str | None = None,
) -> EditResponse:
    provider_key = normalize_provider_key(provider)
    spec = get_provider(provider_key)
    system_prompt = get_edit_system_prompt(
        model_id=spec.default_model,
        provider=provider_key,
        custom_system_message=custom_system_message,
    )
    human_content = build_edit_prompt(messages, current_xml, previous_xml, validation_feedback, context_text)

    if logger:
        logger.log(
            "prompt",
            "Prepared XML edit request",
            details={"provider": provider_key, "xml_chars": len(current_xml)},
        )
    return call_llm(system_prompt, human_content, provider_key, EditResponse, logger)


def generate_mermaid_diagram(
    user_request: str,
    provider: str = "openai",
    logger: StepLogger | None = None,
    messages: list[dict[str, str]] | None = None,
) -> MermaidDiagram:
    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]

    if logger:
        logger.log(
            "prompt",
            "Prepared Mermaid request",
            details={"provider": provider_key, "turns": len(chat_messages)},
        )

    human_content = build_chat_prompt(chat_messages, "Generic (Mermaid)", None, None, None)
    parsed = call_llm(MERMAID_SYSTEM_PROMPT, human_content, provider_key, MermaidDiagram, logger)
    parsed.mermaid = extract_mermaid(parsed.mermaid)
    if not parsed.assistant_message:
        parsed.assistant_message = "Generated Mermaid diagram."

    if logger:
        logger.log(
            "mermaid",
            "Mermaid diagram generated",
            level="success",
            details={"chars": len(parsed.mermaid), "lines": parsed.mermaid.count(chr(10)) + 1},
        )
    return parsed
