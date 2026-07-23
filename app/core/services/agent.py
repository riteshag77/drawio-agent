"""Public LLM orchestration API — XML + Mermaid diagram generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.outbound.llm.prompt_builders import build_chat_prompt
from app.adapters.outbound.llm.prompts.simple import MERMAID_SYSTEM_PROMPT
from app.adapters.outbound.llm.prompts.system import get_xml_gen_system_prompt_marked
from app.adapters.outbound.llm.providers import get_provider, normalize_provider_key
from app.adapters.outbound.llm.response_parse import extract_mermaid
from app.core.ports.llm import LlmPort, get_llm
from app.domain.entities import MermaidDiagram, XmlDiagram

if TYPE_CHECKING:
    from app.adapters.outbound.diagram.xml_diagnostics import XmlDiagnostics
    from app.core.logging import StepLogger


def generate_xml_diagram(
    user_request: str,
    provider: str = "claude",
    platform: str = "aws",
    logger: "StepLogger | None" = None,
    messages: list[dict[str, str]] | None = None,
    custom_system_message: str = "",
    applied_suggestions: list[str] | None = None,
    three_d: bool = False,
    context_text: str | None = None,
    attachment_documents: list[dict] | None = None,
    current_xml: str | None = None,
    *,
    llm: LlmPort | None = None,
) -> XmlDiagram:
    """Generate draw.io XML directly (MCP-accelerated path — no intermediate JSON).

    ``current_xml``, when provided, is the live diagram exported straight from
    the draw.io iframe — the real ground truth for an edit turn (ids, parents,
    coordinates, the existing Overview Panel text), as opposed to inferring
    prior state from prose conversation history alone.
    """
    provider_key = normalize_provider_key(provider)
    spec = get_provider(provider_key)
    chat_messages = messages or [{"role": "user", "content": user_request}]
    hint = user_request or (chat_messages[-1]["content"] if chat_messages else "")
    system_prompt = get_xml_gen_system_prompt_marked(
        model_id=spec.default_model,
        provider=provider_key,
        platform=platform,
        custom_system_message=custom_system_message,
        three_d=three_d,
        user_hint=hint,
    )
    human_parts: list[str] = []
    if current_xml:
        human_parts.append(
            "## Current diagram (edit this exact XML — this is Edit mode)\n"
            "```xml\n" + current_xml + "\n```\n"
            "Preserve every id/parent/coordinate not relevant to the requested change, "
            "per Create vs Edit Modes. Rewrite the Overview Panel's full description to "
            "match the diagram after your edit, per the Overview Panel section."
        )
    if len(chat_messages) > 1:
        human_parts.append("Conversation so far:")
        for msg in chat_messages[:-1]:
            human_parts.append(f"{msg['role'].upper()}: {msg['content']}")
    human_parts.append(f"Platform: {platform.upper()}")
    if context_text:
        human_parts.append(f"Document context:\n{context_text}")
    human_parts.append(f"Request:\n{chat_messages[-1]['content']}")
    human_content = "\n".join(human_parts)

    if logger:
        logger.log("prompt", "Prepared XML generation request", details={"provider": provider_key, "platform": platform})

    _llm = llm or get_llm()
    try:
        result = _llm(
            system_prompt, human_content, provider_key, XmlDiagram, logger,
            attachment_documents,
        )
    except Exception as exc:
        if logger:
            logger.log("error", f"XML generation LLM call failed: {exc}", level="error")
        raise

    if not result.assistant_message:
        result.assistant_message = "Generated diagram."
    if result.suggestions is None:
        result.suggestions = []

    if logger:
        logger.log("xml_diagram", "Draw.io XML generated", level="success", details={
            "xml_chars": len(result.xml),
            "xml_preview": (result.xml[:500] + "...") if len(result.xml) > 500 else result.xml,
        })
    return result


def repair_xml_diagram(
    previous_xml: str,
    diagnostics: XmlDiagnostics,
    provider: str = "claude",
    platform: str = "aws",
    logger: StepLogger | None = None,
    user_hint: str = "",
) -> XmlDiagram:
    """One bounded corrective LLM pass — fix only the listed structural problems.

    Reuses the full layout system prompt (the model still needs every rule to
    avoid introducing new violations) plus the previous XML and the concrete
    diagnostic strings from ``xml_diagnostics.diagnose_xml``. Never raises —
    on failure this falls back to the (already deterministically-repaired)
    input XML, since the caller already has a usable diagram without it.

    ``user_hint`` is the original user request; passing it keeps the assembled
    system prompt identical to generation's (same ``_filter_catalog`` result),
    so the large static prefix is reused rather than re-billed as a distinct,
    unfiltered — and therefore larger — prompt.
    """
    provider_key = normalize_provider_key(provider)
    spec = get_provider(provider_key)
    system_prompt = get_xml_gen_system_prompt_marked(
        model_id=spec.default_model, provider=provider_key, platform=platform, user_hint=user_hint,
    )
    problems = "\n".join(f"- {p}" for p in (diagnostics.errors + diagnostics.warnings))
    human_content = (
        "The following draw.io XML has specific structural problems. Fix ONLY these "
        "problems and return the complete corrected XML, preserving every other cell, "
        "id, position, and style exactly as-is.\n\n"
        f"Problems:\n{problems}\n\n"
        f"XML:\n{previous_xml}"
    )
    if logger:
        logger.log("repair", "Requesting bounded repair pass", details={
            "problem_count": len(diagnostics.errors) + len(diagnostics.warnings),
        })
    _llm = get_llm()
    try:
        result = _llm(system_prompt, human_content, provider_key, XmlDiagram, logger, None)
    except Exception as exc:
        if logger:
            logger.log("repair", f"Repair pass failed, keeping prior result: {exc}", level="warning")
        return XmlDiagram(xml=previous_xml, assistant_message="Generated diagram.")
    if not result.xml:
        return XmlDiagram(xml=previous_xml, assistant_message="Generated diagram.")
    if logger:
        logger.log("repair", "Repair pass produced corrected XML", level="success", details={
            "xml_chars": len(result.xml),
        })
    return result


def generate_mermaid_diagram(
    user_request: str,
    provider: str = "claude",
    logger: StepLogger | None = None,
    messages: list[dict[str, str]] | None = None,
    applied_suggestions: list[str] | None = None,
) -> MermaidDiagram:
    provider_key = normalize_provider_key(provider)
    chat_messages = messages or [{"role": "user", "content": user_request}]

    if logger:
        logger.log(
            "prompt",
            "Prepared Mermaid request",
            details={"provider": provider_key, "turns": len(chat_messages)},
        )

    human_content = build_chat_prompt(
        chat_messages, "Generic (Mermaid)", None, None, None, applied_suggestions=applied_suggestions,
    )
    parsed = get_llm()(MERMAID_SYSTEM_PROMPT, human_content, provider_key, MermaidDiagram, logger, None)
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
