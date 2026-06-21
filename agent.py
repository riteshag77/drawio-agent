import ssl_config  # noqa: F401 — trust OS CA store before LangChain HTTP clients

import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from chat_helpers import trim_chat_messages_for_prompt
from models import ArchitectureDiagram
from dotenv import load_dotenv
from system_prompts import get_system_prompt

if TYPE_CHECKING:
    from pipeline_log import StepLogger

load_dotenv()

# ── Model names ────────────────────────────────────────────────────────────────
# claude-sonnet-4-20250514 was retired 15 Jun 2026; use claude-sonnet-4-6 instead
MODEL_IDS = {
    "openai":  "gpt-4o",
    "gemini":  "gemini-2.5-flash",
    "claude":  "claude-sonnet-4-6",          # ← BUG 1 FIX: was claude-sonnet-4-20250514
    "cursor":  os.getenv("CURSOR_MODEL", "composer-2.5"),
}

SUPPORTED_PROVIDERS = ("openai", "gemini", "claude", "cursor")

PLATFORM_LABELS = {
    "aws":   "Amazon Web Services (AWS)",
    "gcp":   "Google Cloud Platform (GCP)",
    "azure": "Microsoft Azure",
    "multi": "Multi-Cloud / Hybrid",
}


def _summarize_diagram(diagram: ArchitectureDiagram) -> str:
    containers = sum(1 for n in diagram.nodes if n.is_container)
    return (
        f"Diagram updated: {len(diagram.nodes)} nodes ({containers} containers), "
        f"{len(diagram.edges)} edges."
    )


def _build_chat_prompt(
    messages: list[dict[str, str]],
    platform_label: str,
    current_architecture: ArchitectureDiagram | None,
    previous_architecture: ArchitectureDiagram | None = None,
    validation_feedback: str | None = None,
) -> str:
    if not messages:
        raise ValueError("At least one user message is required.")

    trimmed_messages = trim_chat_messages_for_prompt(messages)

    parts: list[str] = []
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

    if len(trimmed_messages) > 1:
        parts.append("\nConversation so far:")
        for msg in trimmed_messages[:-1]:
            role = msg.get("role", "user").upper()
            parts.append(f"{role}: {msg.get('content', '')}")

    parts.append(f"\nTarget cloud platform: {platform_label}")
    parts.append(f"\nLatest user request:\n{trimmed_messages[-1].get('content', '')}")

    if validation_feedback:
        parts.append(f"\n---\nVALIDATION FEEDBACK (fix these issues in your next JSON output):\n{validation_feedback}")

    return "\n".join(parts)


def _build_llm(provider: str):
    """Instantiate the correct LangChain LLM for the requested provider."""
    provider = provider.lower().strip()

    if provider == "gemini":
        if not os.getenv("GOOGLE_API_KEY"):
            raise ValueError("GOOGLE_API_KEY environment variable is not set.")
        return ChatGoogleGenerativeAI(model=MODEL_IDS["gemini"], temperature=0)

    if provider == "claude":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set.")
        return ChatAnthropic(model=MODEL_IDS["claude"], temperature=0)

    # Default: OpenAI
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    return ChatOpenAI(model=MODEL_IDS["openai"], temperature=0)


def _extract_json_from_text(text: str) -> dict:
    if not text or not text.strip():
        raise ValueError("Cursor returned an empty response.")

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

    raise ValueError("Cursor response did not contain valid architecture JSON.")


def _cursor_model_id(model: str | None = None) -> str:
    model_id = (model or os.getenv("CURSOR_MODEL", "composer-2.5")).strip()
    return model_id or "composer-2.5"


def _generate_with_cursor_sdk(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
    logger: "StepLogger | None" = None,
) -> ArchitectureDiagram:
    """Generate architecture JSON via the official Cursor SDK (`cursor-sdk` / @cursor/sdk)."""
    from cursor_bridge_patch import apply_windows_bridge_patch
    from cursor_sdk import Agent, AgentOptions, CloudAgentOptions, LocalAgentOptions
    from cursor_sdk.errors import CursorAgentError

    api_key = os.getenv("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "CURSOR_API_KEY environment variable is not set. "
            "Create a key at https://cursor.com/dashboard (API Keys section)."
        )

    apply_windows_bridge_patch()

    model_id = _cursor_model_id(model)
    runtime = os.getenv("CURSOR_RUNTIME", "local").strip().lower()
    workspace = str(Path(__file__).resolve().parent)
    combined_prompt = (
        f"{system_prompt}\n\n"
        "---\n"
        "IMPORTANT: Respond with ONLY one valid JSON object matching the schema above. "
        "Do not use tools, edit files, or wrap the JSON in markdown.\n\n"
        f"{user_prompt}"
    )

    if runtime == "cloud":
        options = AgentOptions(
            api_key=api_key,
            model=model_id,
            name="drawio-architecture",
            cloud=CloudAgentOptions(repos=[]),
        )
    else:
        options = AgentOptions(
            api_key=api_key,
            model=model_id,
            name="drawio-architecture",
            local=LocalAgentOptions(cwd=workspace),
        )

    if logger:
        logger.log(
            "cursor",
            "Calling Cursor SDK (Agent.prompt)",
            details={"model": model_id, "runtime": runtime, "workspace": workspace},
        )

    try:
        result = Agent.prompt(combined_prompt, options)
    except CursorAgentError as error:
        raise ValueError(f"Cursor SDK error: {error.message}") from error

    if result.status != "finished":
        raise ValueError(
            f"Cursor agent ended with status={result.status}. "
            f"Result: {result.result or 'no result text'}"
        )

    raw_text = str(result.result or "")
    if logger:
        logger.log(
            "cursor",
            "Cursor SDK run finished",
            level="success",
            details={
                "response_chars": len(raw_text),
                "preview": raw_text[:240] + ("…" if len(raw_text) > 240 else ""),
            },
        )

    diagram = ArchitectureDiagram.model_validate(_extract_json_from_text(raw_text))
    if logger:
        logger.log(
            "cursor",
            "Parsed response into ArchitectureDiagram",
            level="success",
            details={"nodes": len(diagram.nodes), "edges": len(diagram.edges)},
        )
    return diagram


def generate_architecture_json(
    user_request: str,
    provider: str = "openai",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    current_architecture: ArchitectureDiagram | None = None,
    previous_architecture: ArchitectureDiagram | None = None,
    minimal_style: bool = False,
    custom_system_message: str = "",
    validation_feedback: str | None = None,
) -> ArchitectureDiagram:
    """
    Generate an architecture diagram JSON using the selected AI provider.

    Args:
        user_request: Natural-language description (used when messages is None).
        provider:     "openai" | "gemini" | "claude" | "cursor"
        platform:     "aws" | "gcp" | "azure" | "multi"
        messages:     Optional full chat history; last entry must be the latest user message.
        current_architecture: Previous diagram for iterative edits.

    Returns:
        A validated ArchitectureDiagram Pydantic model instance.
    """
    platform_key = (platform or "aws").strip().lower()
    platform_label = PLATFORM_LABELS.get(platform_key, PLATFORM_LABELS["aws"])
    provider_key = (provider or "openai").strip().lower()

    chat_messages = messages
    if chat_messages is None:
        chat_messages = [{"role": "user", "content": user_request}]

    if logger:
        logger.log(
            "prompt",
            "Prepared LLM request",
            details={
                "platform": platform_key,
                "platform_label": platform_label,
                "provider": provider_key,
                "turns": len(chat_messages),
                "has_existing_diagram": current_architecture is not None,
                "has_validation_feedback": bool(validation_feedback),
            },
        )

    system_prompt = get_system_prompt(
        model_id=MODEL_IDS.get(provider_key, MODEL_IDS["openai"]),
        minimal_style=minimal_style,
        provider=provider_key,
        custom_system_message=custom_system_message,
        is_edit_mode=current_architecture is not None or len(chat_messages) > 1,
        include_schema=True,
    )

    if logger:
        logger.log(
            "system_prompt",
            "Built system prompt (system_prompts.py)",
            details={
                "chars": len(system_prompt),
                "minimal_style": minimal_style,
                "edit_mode": current_architecture is not None or len(chat_messages) > 1,
                "has_custom_instructions": bool((custom_system_message or "").strip()),
            },
        )

    human_content = _build_chat_prompt(
        chat_messages,
        platform_label,
        current_architecture,
        previous_architecture,
        validation_feedback,
    )

    if provider_key == "cursor":
        if logger:
            logger.log(
                "llm",
                "Using Cursor SDK",
                details={"model": _cursor_model_id(MODEL_IDS["cursor"])},
            )
        return _generate_with_cursor_sdk(
            system_prompt=system_prompt,
            user_prompt=human_content,
            model=MODEL_IDS["cursor"],
            logger=logger,
        )

    langchain_messages: list[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_content),
    ]

    if logger:
        model_id = MODEL_IDS.get(provider_key, MODEL_IDS["openai"])
        logger.log(
            "llm",
            f"Initializing {provider_key} client",
            details={"model": model_id, "temperature": 0},
        )

    llm = _build_llm(provider)
    structured_llm = llm.with_structured_output(ArchitectureDiagram)

    if logger:
        logger.log("llm", "Calling LLM for structured architecture JSON")

    result = structured_llm.invoke(langchain_messages)

    if logger:
        containers = sum(1 for node in result.nodes if node.is_container)
        logger.log(
            "architecture",
            "Architecture JSON validated",
            level="success",
            details={
                "nodes": len(result.nodes),
                "containers": containers,
                "services": len(result.nodes) - containers,
                "edges": len(result.edges),
                "suggestions": len(result.suggestions),
                "node_ids": [node.id for node in result.nodes],
            },
        )

    return result


def generate_architecture_from_chat(
    messages: list[dict[str, str]],
    provider: str = "openai",
    platform: str = "aws",
    current_architecture: ArchitectureDiagram | None = None,
    previous_architecture: ArchitectureDiagram | None = None,
    logger: "StepLogger | None" = None,
    minimal_style: bool = False,
    custom_system_message: str = "",
    validation_feedback: str | None = None,
) -> tuple[ArchitectureDiagram, str]:
    """Run one chat turn and return the diagram plus a short assistant summary."""
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
    )
    summary = _summarize_diagram(diagram)
    if diagram.suggestions:
        summary += f" {len(diagram.suggestions)} suggestion(s) included."
    return diagram, summary