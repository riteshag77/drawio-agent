from __future__ import annotations

import asyncio
import time

from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.mcp_renderer import render_architecture_xml
from app.adapters.outbound.diagram.xml_tools import (
    apply_diagram_operations,
    choose_fixed_xml,
    validate_and_fix_xml,
    wrap_with_mxfile,
)
from app.adapters.outbound.llm.prompt_builders import PLATFORM_LABELS
from app.adapters.outbound.utils.chat import filter_applied_suggestions, is_minimal_architecture, is_minimal_diagram
from app.adapters.outbound.validation.structural import format_validation_feedback, validate_architecture_structure
from app.adapters.outbound.validation.visual import validate_diagram_image
from app.core.services.agent import (
    generate_architecture_from_chat,
    generate_architecture_json,
    generate_edit_operations,
    generate_mermaid_diagram,
)
from app.domain.entities import ValidationResult


class StructuralValidationError(Exception):
    """Raised when architecture remains structurally invalid after retries."""

    def __init__(self, validation: ValidationResult, feedback: str):
        self.validation = validation
        self.feedback = feedback
        super().__init__(feedback or "Architecture failed structural validation")


def sanitize_custom_message(text: str) -> str:
    return (text or "")[:get_config().max_custom_message_len]


def classify_diagram_intent(prompt: str, *, diagram_mode: str = "auto", platform: str = "aws") -> str:
    mode = (diagram_mode or "auto").strip().lower()
    if mode in ("cloud", "simple"):
        return mode
    text = (prompt or "").lower()
    has_cloud = any(kw in text for kw in (
        "aws", "amazon", "gcp", "google cloud", "azure", "microsoft azure",
        "vpc", "vnet", "subnet", "lambda", "ecs", "eks", "kubernetes", "k8s",
        "cloudfront", "route 53", "route53", "alb", "load balancer",
        "rds", "aurora", "dynamodb", "s3", "cloud architecture",
        "three tier", "3-tier", "microservices", "serverless",
        "api gateway", "cloud run", "cloud sql", "cloud watch",
    ))
    has_simple = any(kw in text for kw in (
        "flowchart", "flow chart", "sequence diagram", "sequence chart",
        "org chart", "organization chart", "organizational chart",
        "state diagram", "state machine", "simple diagram",
        "basic flow", "no cloud", "mermaid", "process flow", "workflow diagram",
    ))
    if has_simple and not has_cloud:
        return "simple"
    if platform in ("aws", "gcp", "azure", "multi") and has_cloud:
        return "cloud"
    return "cloud"


def _build_response(status="success", diagram_type="cloud", *, xml=None, mermaid=None,
                    architecture=None, flow_steps=None, pattern_id=None, title=None,
                    suggestions=None, assistant_message="", edit_mode=False,
                    validation=None, logger=None, renderer_used=None, mcp_preview_url=None):
    return {
        "status": status,
        "diagram_type": diagram_type,
        "suggestions": suggestions or [],
        "xml": xml,
        "mermaid": mermaid,
        "architecture": architecture,
        "flow_steps": flow_steps or [],
        "pattern_id": pattern_id,
        "title": title,
        "assistant_message": assistant_message,
        "edit_mode": edit_mode,
        "renderer_used": renderer_used,
        "mcp_preview_url": mcp_preview_url,
        "validation": validation or {"structural": None, "vlm": None, "retries": 0, "feedback": None},
        "logs": logger.to_list() if logger else [],
    }


def _run_vlm_retry(user_prompt_or_none, messages, provider, platform, architecture_data,
                   current_architecture, logger, custom_msg, feedback, platform_convert,
                   previous_platform, current_xml, image_data, validation_provider="auto",
                   applied_suggestions=None):
    """Validate once with the provided screenshot (legacy helper for tests).

    Without headless re-render we cannot refresh ``image_data`` after regen, so
    looping would re-score the same stale PNG. Prefer inline validate in
    ``run_generation_pipeline`` (no second XML/MCP pass).
    """
    _ = (
        user_prompt_or_none, messages, provider, platform, current_architecture,
        custom_msg, feedback, platform_convert, previous_platform, current_xml,
        applied_suggestions,
    )
    vlm_validation = _validate_sync(image_data, logger, validation_provider)
    if logger and vlm_validation and not vlm_validation.skipped and not vlm_validation.valid:
        logger.log(
            "validation",
            "VLM issues reported; skipping server-side regen loop (stale screenshot)",
            level="warn",
            details={"issues": len(vlm_validation.issues)},
        )
    # Keep existing XML path out of this helper — caller already has MCP-rendered XML.
    return architecture_data, None, vlm_validation


def _validate_sync(image_data, logger, validation_provider="auto"):
    return validate_diagram_image(image_data, logger, validation_provider)


def _backoff_delay(attempt: int) -> None:
    config = get_config()
    delay = min(config.backoff_base_seconds * (2 ** attempt), config.backoff_max_seconds)
    time.sleep(delay)


def _retry_loop_sync(user_prompt_or_none, messages, provider, platform, current_architecture,
                     previous_architecture, logger, minimal_style, custom_msg, feedback,
                     platform_convert, previous_platform, current_xml, applied_suggestions=None):
    config = get_config()
    architecture_data = None
    assistant_message = ""
    structural_validation = None
    for attempt in range(config.max_structural_retries + 1):
        if attempt > 0:
            _backoff_delay(attempt - 1)

        if messages:
            architecture_data, assistant_message = generate_architecture_from_chat(
                messages, provider, platform, current_architecture, previous_architecture,
                logger, minimal_style, custom_msg, feedback,
                platform_convert, previous_platform, current_xml, applied_suggestions,
            )
        else:
            architecture_data = generate_architecture_json(
                user_prompt_or_none or "", provider, platform, logger, None,
                current_architecture, previous_architecture, minimal_style, custom_msg, feedback,
                platform_convert=platform_convert,
                previous_platform=previous_platform,
                current_xml=current_xml,
                applied_suggestions=applied_suggestions,
            )
            assistant_message = (
                f"Generated diagram with {len(architecture_data.nodes)} nodes "
                f"and {len(architecture_data.edges)} edges."
            )

        structural_validation = validate_architecture_structure(architecture_data)
        if logger:
            logger.log("validation", "Structural validation", level="success" if structural_validation.valid else "warn",
                       details={"valid": structural_validation.valid, "issues": len(structural_validation.issues),
                                "attempt": attempt + 1})

        if structural_validation.valid:
            return architecture_data, assistant_message, structural_validation
        feedback = format_validation_feedback(structural_validation)

    assert architecture_data is not None and structural_validation is not None
    raise StructuralValidationError(
        structural_validation,
        format_validation_feedback(structural_validation),
    )


async def run_mermaid_pipeline(*, user_prompt, messages, provider, logger, applied_suggestions=None):
    provider = (provider or "openai").strip().lower()
    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")

    logger.log("request", "Mermaid request", details={"provider": provider})

    result = await asyncio.to_thread(
        generate_mermaid_diagram, human_prompt, provider, logger, messages, applied_suggestions,
    )

    logger.log("complete", "Mermaid pipeline finished", level="success", details={"chars": len(result.mermaid)})

    return _build_response(
        diagram_type="mermaid",
        mermaid=result.mermaid,
        suggestions=filter_applied_suggestions(result.suggestions, applied_suggestions),
        assistant_message=result.assistant_message or "Generated Mermaid diagram.",
        logger=logger,
    )


async def run_edit_pipeline(*, messages, provider, platform, current_xml, previous_xml=None,
                              logger, custom_system_message="", validation_feedback=None,
                              image_data=None, context_text=None, validation_provider="auto",
                              applied_suggestions=None):
    config = get_config()
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()
    custom_msg = sanitize_custom_message(custom_system_message)
    feedback = (validation_feedback or "").strip() or None
    vlm_validation = None
    retry_count = 0
    xml_output = current_xml
    assistant_message = "Diagram updated."
    suggestions = []

    logger.log("request", "XML edit request", details={"provider": provider, "xml_chars": len(current_xml)})

    for attempt in range(config.max_structural_retries + 1):
        if attempt > 0:
            await asyncio.sleep(min(config.backoff_base_seconds * (2 ** (attempt - 1)), config.backoff_max_seconds))
        retry_count = attempt
        edit_response = await asyncio.to_thread(
            generate_edit_operations, messages, provider, xml_output, previous_xml,
            logger, custom_msg, feedback, context_text, applied_suggestions,
        )
        assistant_message = edit_response.assistant_message or assistant_message
        suggestions = edit_response.suggestions or []

        if edit_response.mode == "replace" and edit_response.xml:
            xml_output = wrap_with_mxfile(edit_response.xml, PLATFORM_LABELS.get(platform, "Architecture"))
        elif edit_response.operations:
            apply_result = apply_diagram_operations(xml_output, edit_response.operations)
            if apply_result.errors:
                err_text = "\n".join(f"- {e.type} on {e.cell_id}: {e.message}" for e in apply_result.errors)
                feedback = f"Operation errors:\n{err_text}\nPlease fix and retry."
                logger.log("validation", "Edit operation errors", level="warn", details={"errors": len(apply_result.errors)})
                if attempt >= config.max_structural_retries:
                    xml_output = apply_result.result
                    break
                continue
            xml_output = apply_result.result
        else:
            logger.log("validation", "Edit response had no operations", level="warn")
            break

        check = validate_and_fix_xml(xml_output)
        xml_output = choose_fixed_xml(xml_output, check)
        if check.valid or attempt >= config.max_structural_retries:
            break
        feedback = f"XML validation failed: {check.error}"

    if image_data and not is_minimal_diagram(xml_output):
        # Fail-fast: same screenshot cannot validate post-edit XML without headless re-render
        vlm_validation = await asyncio.to_thread(validate_diagram_image, image_data, logger, validation_provider)
        if logger and vlm_validation and not vlm_validation.skipped and not vlm_validation.valid:
            logger.log(
                "validation",
                "VLM issues on edit; skipping server regen loop (stale screenshot)",
                level="warn",
                details={"issues": len(vlm_validation.issues)},
            )

    validation_summary = format_validation_feedback(vlm_validation) if vlm_validation and not vlm_validation.valid and not vlm_validation.skipped else None
    logger.log("complete", "XML edit finished", level="success", details={"xml_chars": len(xml_output)})

    return _build_response(
        xml=xml_output,
        suggestions=filter_applied_suggestions(suggestions, applied_suggestions),
        assistant_message=assistant_message,
        edit_mode=True, validation={"structural": None, "vlm": vlm_validation.model_dump() if vlm_validation else None,
                                     "retries": retry_count, "feedback": validation_summary},
        logger=logger,
    )


async def run_generation_pipeline(*, user_prompt=None, messages=None, provider="openai", platform="aws",
                                    current_architecture=None, previous_architecture=None, logger=None,
                                    minimal_style=False, custom_system_message="",
                                    validation_feedback=None, image_data=None, context_text=None,
                                    platform_convert=False, previous_platform=None, current_xml=None,
                                    validation_provider="auto", applied_suggestions=None,
                                    diagram_renderer=None):
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()

    logger.log("request", "Generation request", details={
        "platform": platform, "provider": provider, "chat_turns": len(messages) if messages else 1,
        "platform_convert": platform_convert,
        "diagram_renderer": diagram_renderer or get_config().diagram_renderer,
    })

    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")
    if context_text:
        human_prompt = f"{human_prompt}\n\n--- Context ---\n{context_text[:8000]}"

    custom_msg = sanitize_custom_message(custom_system_message)
    feedback = (validation_feedback or "").strip() or None
    architecture_data = None
    structural_validation = None
    vlm_validation = None

    architecture_data, assistant_message, structural_validation = await asyncio.to_thread(
        _retry_loop_sync, human_prompt, messages, provider, platform, current_architecture,
        previous_architecture, logger, minimal_style, custom_msg, feedback,
        platform_convert, previous_platform, current_xml, applied_suggestions,
    )

    logger.log("layout", "Running auto-layout and XML builder")
    xml_output, renderer_used, mcp_preview_url, architecture_data = await render_architecture_xml(
        architecture_data, platform, logger, diagram_renderer=diagram_renderer,
    )

    final_check = validate_and_fix_xml(xml_output)
    xml_output = choose_fixed_xml(xml_output, final_check)

    if image_data and not is_minimal_architecture(architecture_data):
        # Validate only — do not rebuild XML or re-hit MCP (same architecture, stale PNG).
        vlm_validation = await asyncio.to_thread(
            validate_diagram_image, image_data, logger, validation_provider,
        )
        if logger and vlm_validation and not vlm_validation.skipped and not vlm_validation.valid:
            logger.log(
                "validation",
                "VLM issues reported; skipping server-side regen loop (stale screenshot)",
                level="warn",
                details={"issues": len(vlm_validation.issues)},
            )

    validation_summary = None
    if structural_validation and not structural_validation.valid:
        validation_summary = format_validation_feedback(structural_validation)
    if vlm_validation and not vlm_validation.skipped and not vlm_validation.valid:
        validation_summary = format_validation_feedback(vlm_validation)

    logger.log("complete", "Pipeline finished", level="success",
               details={"xml_chars": len(xml_output), "retries": 0, "renderer_used": renderer_used})

    return _build_response(
        xml=xml_output,
        architecture=architecture_data.model_dump(),
        flow_steps=[s.model_dump() for s in architecture_data.flow_steps],
        pattern_id=architecture_data.pattern_id,
        title=architecture_data.title,
        suggestions=filter_applied_suggestions(architecture_data.suggestions, applied_suggestions),
        assistant_message=assistant_message,
        renderer_used=renderer_used,
        mcp_preview_url=mcp_preview_url,
        validation={
            "structural": structural_validation.model_dump() if structural_validation else None,
            "vlm": vlm_validation.model_dump() if vlm_validation else None,
            "retries": 0,
            "feedback": validation_summary,
        },
        logger=logger,
    )
