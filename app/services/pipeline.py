"""End-to-end generation and edit pipelines.

These orchestrate the LLM agents, the deterministic layout engine, and the
validation layers into the response payloads consumed by the API routes. All
blocking work is dispatched to worker threads so the FastAPI event loop stays
responsive.
"""

from __future__ import annotations

import asyncio

from app.core.config import (
    MAX_CUSTOM_MESSAGE_LEN,
    MAX_STRUCTURAL_RETRIES,
    MAX_VLM_RETRIES,
)
from app.core.logging import StepLogger
from app.diagram.draftsman import generate_xml
from app.diagram.xml_tools import (
    apply_diagram_operations,
    validate_and_fix_xml,
    wrap_with_mxfile,
)
from app.llm.agent import (
    MODEL_IDS,
    PLATFORM_LABELS,
    generate_architecture_from_chat,
    generate_architecture_json,
    generate_edit_operations,
    generate_mermaid_diagram,
)
from app.models import ArchitectureDiagram, ValidationResult
from app.patterns.cache import find_cached_response
from app.utils.chat import is_minimal_architecture, is_minimal_diagram
from app.validation.structural import (
    format_validation_feedback,
    validate_architecture_structure,
)
from app.validation.visual import validate_diagram_image

_CLOUD_KEYWORDS = (
    "aws", "amazon", "gcp", "google cloud", "azure", "microsoft azure",
    "vpc", "vnet", "subnet", "lambda", "ecs", "eks", "kubernetes", "k8s",
    "cloudfront", "route 53", "route53", "alb", "load balancer",
    "rds", "aurora", "dynamodb", "s3", "cloud architecture",
    "three tier", "3-tier", "microservices", "serverless",
    "api gateway", "cloud run", "cloud sql", "cloud watch",
)

_SIMPLE_KEYWORDS = (
    "flowchart", "flow chart", "sequence diagram", "sequence chart",
    "org chart", "organization chart", "organizational chart",
    "state diagram", "state machine", "simple diagram",
    "basic flow", "no cloud", "mermaid", "process flow",
    "workflow diagram",
)

_PLATFORM_PAGE_NAMES = {
    "aws": "Amazon Web Services",
    "gcp": "Google Cloud Platform",
    "azure": "Microsoft Azure",
    "multi": "Multi-Cloud Architecture",
}


def sanitize_custom_message(text: str) -> str:
    """Clamp user-supplied custom instructions to a safe length."""
    return (text or "")[:MAX_CUSTOM_MESSAGE_LEN]


def platform_page_name(platform: str) -> str:
    """Human-readable page name used for the mxfile diagram tab."""
    return _PLATFORM_PAGE_NAMES.get(platform, "Architecture")


def classify_diagram_intent(
    prompt: str,
    *,
    diagram_mode: str = "auto",
    platform: str = "aws",
) -> str:
    """Return ``cloud`` or ``simple`` for pipeline routing."""
    mode = (diagram_mode or "auto").strip().lower()
    if mode in ("cloud", "simple"):
        return mode

    text = (prompt or "").lower()
    has_cloud = any(kw in text for kw in _CLOUD_KEYWORDS)
    has_simple = any(kw in text for kw in _SIMPLE_KEYWORDS)
    if has_simple and not has_cloud:
        return "simple"
    if platform in ("aws", "gcp", "azure", "multi") and has_cloud:
        return "cloud"
    return "cloud"


async def run_mermaid_pipeline(
    *,
    user_prompt: str | None,
    messages: list[dict[str, str]] | None,
    provider: str,
    logger: StepLogger,
) -> dict:
    """Generate a Mermaid diagram for simple flowcharts / sequence diagrams."""
    provider = (provider or "openai").strip().lower()
    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")

    logger.log(
        "request",
        "Mermaid simple-diagram request received",
        details={"provider": provider, "model": MODEL_IDS.get(provider, MODEL_IDS["openai"])},
    )

    result = await asyncio.to_thread(
        generate_mermaid_diagram,
        human_prompt,
        provider,
        logger,
        messages,
    )

    assistant_message = result.assistant_message or "Generated Mermaid diagram."

    logger.log(
        "complete",
        "Mermaid pipeline finished",
        level="success",
        details={"mermaid_chars": len(result.mermaid)},
    )

    return {
        "status": "success",
        "diagram_type": "mermaid",
        "mermaid": result.mermaid,
        "suggestions": result.suggestions,
        "xml": None,
        "architecture": None,
        "flow_steps": [],
        "pattern_id": None,
        "title": None,
        "assistant_message": assistant_message,
        "edit_mode": False,
        "validation": {"structural": None, "vlm": None, "retries": 0, "feedback": None},
        "logs": logger.to_list(),
    }


async def try_cached_first_turn(
    *,
    prompt_text: str,
    has_image: bool,
    platform: str,
    logger: StepLogger,
) -> dict | None:
    """Return an instant cached diagram response when the prompt matches, else None."""
    cached = find_cached_response(prompt_text, has_image)
    if not cached:
        return None
    logger.log("cache", "Serving cached example diagram", level="success")
    xml_output = wrap_with_mxfile(cached.xml, platform_page_name(platform))
    return {
        "status": "success",
        "diagram_type": "cloud",
        "suggestions": [],
        "xml": xml_output,
        "architecture": None,
        "flow_steps": [],
        "pattern_id": None,
        "title": None,
        "assistant_message": "Loaded cached example diagram instantly.",
        "edit_mode": False,
        "validation": {"structural": None, "vlm": None, "retries": 0, "feedback": None},
        "logs": logger.to_list(),
    }


async def run_edit_pipeline(
    *,
    messages: list[dict[str, str]],
    provider: str,
    platform: str,
    current_xml: str,
    previous_xml: str | None,
    logger: StepLogger,
    custom_system_message: str = "",
    validation_feedback: str | None = None,
    image_data: str | None = None,
    context_text: str | None = None,
) -> dict:
    """Incrementally edit an existing diagram via ID-based XML operations."""
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()
    custom_msg = sanitize_custom_message(custom_system_message)
    feedback = (validation_feedback or "").strip() or None
    vlm_validation: ValidationResult | None = None
    retry_count = 0

    logger.log(
        "request",
        "XML edit request received",
        details={"provider": provider, "xml_chars": len(current_xml), "platform": platform},
    )

    xml_output = current_xml
    assistant_message = "Diagram updated."
    suggestions: list[str] = []

    for attempt in range(MAX_STRUCTURAL_RETRIES + 1):
        retry_count = attempt
        edit_response = await asyncio.to_thread(
            generate_edit_operations,
            messages,
            provider,
            xml_output,
            previous_xml,
            logger,
            custom_msg,
            feedback,
            context_text,
        )
        assistant_message = edit_response.assistant_message or assistant_message
        suggestions = edit_response.suggestions or []

        if edit_response.mode == "replace" and edit_response.xml:
            xml_output = wrap_with_mxfile(edit_response.xml, platform_page_name(platform))
        elif edit_response.operations:
            apply_result = apply_diagram_operations(xml_output, edit_response.operations)
            if apply_result.errors:
                err_text = "\n".join(
                    f"- {e.type} on {e.cell_id}: {e.message}" for e in apply_result.errors
                )
                feedback = f"Operation errors:\n{err_text}\nPlease fix and retry."
                logger.log(
                    "validation",
                    "Edit operation errors",
                    level="warn",
                    details={"errors": len(apply_result.errors)},
                )
                if attempt >= MAX_STRUCTURAL_RETRIES:
                    xml_output = apply_result.result
                    break
                continue
            xml_output = apply_result.result
        else:
            logger.log("validation", "Edit response had no operations", level="warn")
            break

        xml_check = validate_and_fix_xml(xml_output)
        if xml_check.fixed:
            xml_output = xml_check.fixed
        if xml_check.valid or attempt >= MAX_STRUCTURAL_RETRIES:
            break
        feedback = f"XML validation failed: {xml_check.error}"
        logger.log("validation", "XML validation failed — retrying edit", level="warn")

    if image_data and not is_minimal_diagram(xml_output):
        for vlm_attempt in range(MAX_VLM_RETRIES + 1):
            vlm_validation = await asyncio.to_thread(validate_diagram_image, image_data, logger)
            if vlm_validation.valid or vlm_attempt >= MAX_VLM_RETRIES:
                break
            feedback = format_validation_feedback(vlm_validation)
            edit_response = await asyncio.to_thread(
                generate_edit_operations,
                messages,
                provider,
                xml_output,
                previous_xml,
                logger,
                custom_msg,
                feedback,
                context_text,
            )
            if edit_response.operations:
                apply_result = apply_diagram_operations(xml_output, edit_response.operations)
                xml_output = apply_result.result
            assistant_message = edit_response.assistant_message or assistant_message

    validation_summary = None
    if vlm_validation and not vlm_validation.valid:
        validation_summary = format_validation_feedback(vlm_validation)

    logger.log(
        "complete",
        "XML edit pipeline finished",
        level="success",
        details={"xml_chars": len(xml_output)},
    )

    return {
        "status": "success",
        "diagram_type": "cloud",
        "suggestions": suggestions,
        "xml": xml_output,
        "architecture": None,
        "flow_steps": [],
        "pattern_id": None,
        "title": None,
        "assistant_message": assistant_message,
        "edit_mode": True,
        "validation": {
            "structural": None,
            "vlm": vlm_validation.model_dump() if vlm_validation else None,
            "retries": retry_count,
            "feedback": validation_summary,
        },
        "logs": logger.to_list(),
    }


async def run_generation_pipeline(
    *,
    user_prompt: str | None,
    messages: list[dict[str, str]] | None,
    provider: str,
    platform: str,
    current_architecture: ArchitectureDiagram | None,
    previous_architecture: ArchitectureDiagram | None,
    logger: StepLogger,
    minimal_style: bool = False,
    custom_system_message: str = "",
    validation_feedback: str | None = None,
    image_data: str | None = None,
    context_text: str | None = None,
    platform_convert: bool = False,
    previous_platform: str | None = None,
    current_xml: str | None = None,
) -> dict:
    """Generate (or chat-edit) an architecture as JSON, then render it to XML."""
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()

    logger.log(
        "request",
        "Generation request received" if not platform_convert else "Platform conversion request received",
        details={
            "platform": platform,
            "provider": provider,
            "model": MODEL_IDS.get(provider, MODEL_IDS["openai"]),
            "platform_label": PLATFORM_LABELS.get(platform, PLATFORM_LABELS["aws"]),
            "chat_turns": len(messages) if messages else 1,
            "minimal_style": minimal_style,
            "custom_instructions": bool(sanitize_custom_message(custom_system_message)),
            "platform_convert": platform_convert,
            "previous_platform": previous_platform,
        },
    )

    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")
    if context_text:
        human_prompt = f"{human_prompt}\n\n--- Context ---\n{context_text[:8000]}"

    if not platform_convert and current_architecture is None and (not messages or len(messages) <= 1):
        cached_result = await try_cached_first_turn(
            prompt_text=human_prompt,
            has_image=bool(image_data),
            platform=platform,
            logger=logger,
        )
        if cached_result:
            return cached_result

    custom_msg = sanitize_custom_message(custom_system_message)
    feedback = (validation_feedback or "").strip() or None
    architecture_data: ArchitectureDiagram | None = None
    assistant_message = ""
    structural_validation: ValidationResult | None = None
    vlm_validation: ValidationResult | None = None
    retry_count = 0

    for attempt in range(MAX_STRUCTURAL_RETRIES + 1):
        retry_count = attempt
        if messages:
            architecture_data, assistant_message = await asyncio.to_thread(
                generate_architecture_from_chat,
                messages,
                provider,
                platform,
                current_architecture,
                previous_architecture,
                logger,
                minimal_style,
                custom_msg,
                feedback,
                platform_convert,
                previous_platform,
                current_xml,
            )
        else:
            architecture_data = await asyncio.to_thread(
                generate_architecture_json,
                user_prompt or "",
                provider,
                platform,
                logger,
                None,
                current_architecture,
                previous_architecture,
                minimal_style,
                custom_msg,
                feedback,
            )
            assistant_message = (
                f"Generated diagram with {len(architecture_data.nodes)} nodes "
                f"and {len(architecture_data.edges)} edges."
            )

        structural_validation = validate_architecture_structure(architecture_data)
        logger.log(
            "validation",
            "Structural JSON validation",
            level="success" if structural_validation.valid else "warn",
            details={
                "valid": structural_validation.valid,
                "issues": len(structural_validation.issues),
                "attempt": attempt + 1,
            },
        )

        if structural_validation.valid or attempt >= MAX_STRUCTURAL_RETRIES:
            break

        feedback = format_validation_feedback(structural_validation)
        logger.log(
            "validation",
            "Retrying generation with validation feedback",
            details={"attempt": attempt + 2, "max_retries": MAX_STRUCTURAL_RETRIES},
        )

    assert architecture_data is not None

    logger.log("layout", "Running auto-layout and XML builder")
    xml_output = await asyncio.to_thread(generate_xml, architecture_data, platform, logger)

    final_xml_check = validate_and_fix_xml(xml_output)
    if final_xml_check.fixes:
        logger.log("xml", "Auto-fixed final diagram XML", details={"fixes": final_xml_check.fixes})
    if final_xml_check.fixed:
        xml_output = final_xml_check.fixed
    if not final_xml_check.valid:
        logger.log("xml", f"Final XML validation warning: {final_xml_check.error}", level="warn")

    if image_data and not is_minimal_architecture(architecture_data):
        for vlm_attempt in range(MAX_VLM_RETRIES + 1):
            vlm_validation = await asyncio.to_thread(validate_diagram_image, image_data, logger)
            if vlm_validation.valid or vlm_attempt >= MAX_VLM_RETRIES:
                break
            feedback = format_validation_feedback(vlm_validation)
            logger.log(
                "validation",
                "VLM failed — retrying JSON generation",
                details={"vlm_attempt": vlm_attempt + 2},
            )
            if messages:
                architecture_data, assistant_message = await asyncio.to_thread(
                    generate_architecture_from_chat,
                    messages,
                    provider,
                    platform,
                    architecture_data,
                    current_architecture,
                    logger,
                    minimal_style,
                    custom_msg,
                    feedback,
                    platform_convert,
                    previous_platform,
                    current_xml,
                )
            else:
                architecture_data = await asyncio.to_thread(
                    generate_architecture_json,
                    user_prompt or "",
                    provider,
                    platform,
                    logger,
                    None,
                    architecture_data,
                    previous_architecture,
                    minimal_style,
                    custom_msg,
                    feedback,
                )
            xml_output = await asyncio.to_thread(generate_xml, architecture_data, platform, logger)
            final_vlm_check = validate_and_fix_xml(xml_output)
            if final_vlm_check.fixed:
                xml_output = final_vlm_check.fixed

    validation_summary = None
    if structural_validation and not structural_validation.valid:
        validation_summary = format_validation_feedback(structural_validation)
    if vlm_validation and not vlm_validation.valid:
        validation_summary = format_validation_feedback(vlm_validation)

    logger.log(
        "complete",
        "Pipeline finished successfully",
        level="success",
        details={
            "xml_chars": len(xml_output),
            "retries": retry_count,
            "structural_valid": structural_validation.valid if structural_validation else True,
        },
    )

    return {
        "status": "success",
        "diagram_type": "cloud",
        "suggestions": architecture_data.suggestions,
        "xml": xml_output,
        "architecture": architecture_data.model_dump(),
        "flow_steps": [s.model_dump() for s in architecture_data.flow_steps],
        "pattern_id": architecture_data.pattern_id,
        "title": architecture_data.title,
        "assistant_message": assistant_message,
        "edit_mode": False,
        "validation": {
            "structural": structural_validation.model_dump() if structural_validation else None,
            "vlm": vlm_validation.model_dump() if vlm_validation else None,
            "retries": retry_count,
            "feedback": validation_summary,
        },
        "logs": logger.to_list(),
    }
