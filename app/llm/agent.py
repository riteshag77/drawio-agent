import app.core.ssl_trust  # noqa: F401 — trust OS CA store before LangChain HTTP clients

import json
import os
import re
from typing import TYPE_CHECKING, Any

from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from app.core.config import BASE_DIR
from app.utils.chat import replace_historical_tool_inputs, trim_chat_messages_for_prompt
from app.models import ArchitectureDiagram, EditResponse, MermaidDiagram
from dotenv import load_dotenv
from app.patterns.library import find_matching_patterns, format_patterns_for_prompt
from app.llm.prompts.simple import MERMAID_SYSTEM_PROMPT
from app.llm.prompts.system import get_edit_system_prompt, get_system_prompt, XML_EDIT_VALIDATION_FEEDBACK

if TYPE_CHECKING:
    from app.core.logging import StepLogger

load_dotenv()


class _ProviderConfig:
    __slots__ = ("klass", "model", "env_key", "is_sdk")
    def __init__(self, klass=None, model="", env_key="", is_sdk=False):
        self.klass = klass
        self.model = model
        self.env_key = env_key
        self.is_sdk = is_sdk


_LLM_PROVIDERS: dict[str, _ProviderConfig] = {
    "openai":  _ProviderConfig(ChatOpenAI,  "gpt-4o",              "OPENAI_API_KEY"),
    "gemini":  _ProviderConfig(ChatGoogleGenerativeAI, "gemini-2.5-flash", "GOOGLE_API_KEY"),
    "claude":  _ProviderConfig(ChatAnthropic, "claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
    "cursor":  _ProviderConfig(is_sdk=True, model=os.getenv("CURSOR_MODEL", "composer-2.5"), env_key="CURSOR_API_KEY"),
}

MODEL_IDS = {k: v.model for k, v in _LLM_PROVIDERS.items()}

PLATFORM_LABELS = {
    "aws":   "Amazon Web Services (AWS)",
    "gcp":   "Google Cloud Platform (GCP)",
    "azure": "Microsoft Azure",
    "multi": "Multi-Cloud / Hybrid",
}


def _extract_json(text: str) -> dict:
    if not text or not text.strip():
        raise ValueError("Empty response from model.")
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
    raise ValueError("Response did not contain valid JSON.")


def _call_cursor_sdk(system_prompt: str, user_prompt: str, output_type: type, model_id: str, logger=None) -> Any:
    from app.core.cursor_bridge import apply_windows_bridge_patch
    from cursor_sdk import Agent, AgentOptions, CloudAgentOptions, LocalAgentOptions
    from cursor_sdk.errors import CursorAgentError

    api_key = os.getenv("CURSOR_API_KEY", "").strip()
    if not api_key:
        raise ValueError("CURSOR_API_KEY environment variable is not set.")

    apply_windows_bridge_patch()

    runtime = os.getenv("CURSOR_RUNTIME", "local").strip().lower()
    combined = f"{system_prompt}\n\n---\nRespond with ONLY valid JSON.\n\n{user_prompt}"

    if runtime == "cloud":
        options = AgentOptions(api_key=api_key, model=model_id, name="drawio-agent",
                               cloud=CloudAgentOptions(repos=[]))
    else:
        options = AgentOptions(api_key=api_key, model=model_id, name="drawio-agent",
                               local=LocalAgentOptions(cwd=str(BASE_DIR)))

    if logger:
        logger.log("cursor", f"Calling Cursor SDK ({model_id}, {runtime})")

    try:
        result = Agent.prompt(combined, options)
    except CursorAgentError as error:
        raise ValueError(f"Cursor SDK error: {error.message}") from error

    if result.status != "finished":
        raise ValueError(f"Cursor agent ended with status={result.status}. Result: {result.result or 'no result text'}")

    raw_text = str(result.result or "")
    if logger:
        logger.log("cursor", "Cursor SDK finished", level="success",
                   details={"chars": len(raw_text)})

    return output_type.model_validate(_extract_json(raw_text))


def _call_llm(system_prompt: str, human_content: str, provider: str, output_type: type, logger=None) -> Any:
    provider_key = (provider or "openai").strip().lower()
    config = _LLM_PROVIDERS.get(provider_key)
    if not config:
        raise ValueError(f"Unsupported provider: {provider_key}")

    if config.is_sdk:
        return _call_cursor_sdk(system_prompt, human_content, output_type, config.model, logger)

    if config.env_key and not os.getenv(config.env_key):
        raise ValueError(f"{config.env_key} environment variable is not set.")

    if logger:
        logger.log("llm", f"Calling {provider_key} ({config.model})",
                   details={"model": config.model, "temperature": 0, "output_type": output_type.__name__})

    llm = config.klass(model=config.model, temperature=0)
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]
    return llm.with_structured_output(output_type).invoke(messages)


def build_platform_conversion_block(previous_platform: str, target_platform: str, *, current_xml: str | None = None) -> str:
    prev = (previous_platform or "aws").strip().lower()
    target = (target_platform or "aws").strip().lower()
    prev_label = PLATFORM_LABELS.get(prev, prev.upper())
    target_label = PLATFORM_LABELS.get(target, target.upper())

    lines = [
        "PLATFORM CONVERSION (authoritative):",
        f"Convert the current architecture from {prev_label} to {target_label}.",
        "- Preserve topology: same node ids, container hierarchy, step_number order, and edge pairs.",
        f"- Replace every shape_type with the equivalent {target} key from the Platform Shape Catalogue.",
    ]
    if target == "multi":
        lines.append("- Multi-cloud target: use mixed providers only where the design is explicitly hybrid; "
                      "otherwise map each tier to the closest single-provider equivalent.")
    else:
        lines.append(f"- Never mix aws_*, gcp_*, azure_* shape keys — use {target}_* keys only.")
    lines.extend(["- Update edge colors: AWS #ED7100, GCP #4285F4, Azure #0078D4.",
                  "- Return the FULL updated ArchitectureDiagram JSON."])
    if current_xml and len(current_xml.strip()) > 300:
        lines.append(f"\nCurrent draw.io XML (infer topology if architecture JSON is missing):\n"
                      f'"""xml\n{current_xml[:12000]}\n"""')
    return "\n".join(lines)


def _build_chat_prompt(messages: list[dict[str, str]], platform_label: str,
                       current_architecture=None, previous_architecture=None,
                       validation_feedback=None, *, platform_convert=False,
                       previous_platform=None, target_platform="aws", current_xml=None) -> str:
    if not messages:
        raise ValueError("At least one user message is required.")
    trimmed_messages = replace_historical_tool_inputs(trim_chat_messages_for_prompt(messages))

    parts = []
    if platform_convert:
        parts.append(build_platform_conversion_block(previous_platform or "aws", target_platform,
                      current_xml=current_xml if current_architecture is None else None))
    if previous_architecture is not None:
        parts.append(f"Previous architecture JSON (before the user's last message — for comparison only):\n"
                      f"{previous_architecture.model_dump_json()}")
    if current_architecture is not None:
        parts.append(f"Current architecture JSON (AUTHORITATIVE — modify this; return the full updated object):\n"
                      f"{current_architecture.model_dump_json()}")
    if len(trimmed_messages) > 1:
        parts.append("\nConversation so far:")
        for msg in trimmed_messages[:-1]:
            parts.append(f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}")
    parts.append(f"\nTarget cloud platform: {platform_label}")
    parts.append(f"\nLatest user request:\n{trimmed_messages[-1].get('content', '')}")
    if validation_feedback:
        parts.append(f"\n---\nVALIDATION FEEDBACK (fix these issues in your next JSON output):\n{validation_feedback}")
    return "\n".join(parts)


def _build_edit_prompt(messages: list[dict[str, str]], current_xml: str,
                       previous_xml: str | None = None, validation_feedback: str | None = None,
                       context_text: str | None = None) -> str:
    if not messages:
        raise ValueError("At least one user message is required.")
    trimmed = replace_historical_tool_inputs(trim_chat_messages_for_prompt(messages))
    parts = []
    if previous_xml:
        parts.append(f"Previous diagram XML (before the user's last message — for comparison only):\n"
                      f'"""xml\n{previous_xml}\n"""')
    parts.append(f"Current diagram XML (AUTHORITATIVE — apply operations to this; copy ids/styles from here):\n"
                  f'"""xml\n{current_xml}\n"""')
    if context_text:
        parts.append(f"\nAdditional context from uploaded file or URL:\n{context_text}")
    if len(trimmed) > 1:
        parts.append("\nConversation so far:")
        for msg in trimmed[:-1]:
            parts.append(f"{msg.get('role', 'user').upper()}: {msg.get('content', '')}")
    parts.append(f"\nLatest user request:\n{trimmed[-1].get('content', '')}")
    if validation_feedback:
        parts.append(f"\n---\n{XML_EDIT_VALIDATION_FEEDBACK}\n{validation_feedback}")
    return "\n".join(parts)


def generate_architecture_json(user_request: str, provider: str = "openai",
                                platform: str = "aws", logger=None,
                                messages: list[dict[str, str]] | None = None,
                                current_architecture=None, previous_architecture=None,
                                minimal_style: bool = False,
                                custom_system_message: str = "",
                                validation_feedback: str | None = None,
                                platform_convert: bool = False,
                                previous_platform: str | None = None,
                                current_xml: str | None = None) -> ArchitectureDiagram:
    platform_key = (platform or "aws").strip().lower()
    platform_label = PLATFORM_LABELS.get(platform_key, PLATFORM_LABELS["aws"])
    provider_key = (provider or "openai").strip().lower()

    chat_messages = messages or [{"role": "user", "content": user_request}]
    is_edit = bool(current_architecture or len(chat_messages) > 1 or platform_convert)

    patterns_block = ""
    if not is_edit:
        match_text = user_request or (chat_messages[-1]["content"] if chat_messages else "")
        matched = find_matching_patterns(match_text, platform_key)
        if matched:
            patterns_block = format_patterns_for_prompt(matched, platform_key)

    if logger:
        logger.log("prompt", "Prepared LLM request", details={
            "platform": platform_key, "provider": provider_key, "turns": len(chat_messages),
            "has_existing_diagram": current_architecture is not None,
            "has_validation_feedback": bool(validation_feedback),
            "platform_convert": platform_convert,
        })

    system_prompt = get_system_prompt(
        model_id=_LLM_PROVIDERS.get(provider_key, _LLM_PROVIDERS["openai"]).model,
        minimal_style=minimal_style, provider=provider_key,
        custom_system_message=custom_system_message, is_edit_mode=is_edit,
        include_schema=True, patterns_block=patterns_block,
    )

    human_content = _build_chat_prompt(chat_messages, platform_label, current_architecture,
                                        previous_architecture, validation_feedback,
                                        platform_convert=platform_convert,
                                        previous_platform=previous_platform,
                                        target_platform=platform_key, current_xml=current_xml)

    result = _call_llm(system_prompt, human_content, provider_key, ArchitectureDiagram, logger)

    if logger:
        containers = sum(1 for n in result.nodes if n.is_container)
        logger.log("architecture", "Architecture JSON validated", level="success",
                   details={"nodes": len(result.nodes), "containers": containers,
                            "edges": len(result.edges)})
    return result


def generate_architecture_from_chat(messages: list[dict[str, str]], provider: str = "openai",
                                     platform: str = "aws", current_architecture=None,
                                     previous_architecture=None, logger=None,
                                     minimal_style: bool = False,
                                     custom_system_message: str = "",
                                     validation_feedback: str | None = None,
                                     platform_convert: bool = False,
                                     previous_platform: str | None = None,
                                     current_xml: str | None = None) -> tuple[ArchitectureDiagram, str]:
    if not messages or messages[-1].get("role") != "user":
        raise ValueError("Chat must end with a user message.")
    diagram = generate_architecture_json(
        user_request=messages[-1]["content"], provider=provider, platform=platform,
        logger=logger, messages=messages, current_architecture=current_architecture,
        previous_architecture=previous_architecture, minimal_style=minimal_style,
        custom_system_message=custom_system_message, validation_feedback=validation_feedback,
        platform_convert=platform_convert, previous_platform=previous_platform, current_xml=current_xml,
    )
    if platform_convert:
        summary = f"Converted diagram to {PLATFORM_LABELS.get(platform.lower(), platform)}: {len(diagram.nodes)} nodes, {len(diagram.edges)} edges."
    else:
        containers = sum(1 for n in diagram.nodes if n.is_container)
        summary = f"Diagram updated: {len(diagram.nodes)} nodes ({containers} containers), {len(diagram.edges)} edges."
    if diagram.suggestions:
        summary += f" {len(diagram.suggestions)} suggestion(s) included."
    return diagram, summary


def generate_edit_operations(messages: list[dict[str, str]], provider: str = "openai",
                              current_xml: str = "", previous_xml: str | None = None,
                              logger=None, custom_system_message: str = "",
                              validation_feedback: str | None = None,
                              context_text: str | None = None) -> EditResponse:
    provider_key = (provider or "openai").strip().lower()
    system_prompt = get_edit_system_prompt(
        model_id=_LLM_PROVIDERS.get(provider_key, _LLM_PROVIDERS["openai"]).model,
        provider=provider_key, custom_system_message=custom_system_message,
    )
    human_content = _build_edit_prompt(messages, current_xml, previous_xml, validation_feedback, context_text)

    if logger:
        logger.log("prompt", "Prepared XML edit request", details={"provider": provider_key, "xml_chars": len(current_xml)})

    return _call_llm(system_prompt, human_content, provider_key, EditResponse, logger)


def _extract_mermaid_from_text(text: str) -> str:
    if not text or not text.strip():
        raise ValueError("Empty Mermaid response from model.")
    stripped = text.strip()
    fenced = re.search(r"```(?:mermaid)?\s*([\s\S]*?)```", stripped)
    return fenced.group(1).strip() if fenced else stripped


def generate_mermaid_diagram(user_request: str, provider: str = "openai",
                              logger=None,
                              messages: list[dict[str, str]] | None = None) -> MermaidDiagram:
    provider_key = (provider or "openai").strip().lower()
    chat_messages = messages or [{"role": "user", "content": user_request}]
    human_content = _build_chat_prompt(chat_messages, "Generic (Mermaid)", None, None, None)

    if logger:
        logger.log("prompt", "Prepared Mermaid LLM request", details={"provider": provider_key, "turns": len(chat_messages)})

    parsed = _call_llm(MERMAID_SYSTEM_PROMPT, human_content, provider_key, MermaidDiagram, logger)
    parsed.mermaid = _extract_mermaid_from_text(parsed.mermaid)
    if not parsed.assistant_message:
        parsed.assistant_message = "Generated Mermaid diagram."

    if logger:
        logger.log("mermaid", "Mermaid diagram generated", level="success",
                   details={"chars": len(parsed.mermaid), "lines": parsed.mermaid.count(chr(10)) + 1})
    return parsed
