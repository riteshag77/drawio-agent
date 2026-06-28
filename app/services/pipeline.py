from __future__ import annotations

import asyncio

from app.core.config import MAX_CUSTOM_MESSAGE_LEN, MAX_STRUCTURAL_RETRIES, MAX_VLM_RETRIES
from app.core.logging import StepLogger
from app.diagram.draftsman import generate_xml
from app.diagram.xml_tools import apply_diagram_operations, validate_and_fix_xml, wrap_with_mxfile
from app.llm.agent import (
    PLATFORM_LABELS,
    generate_architecture_from_chat,
    generate_architecture_json,
    generate_edit_operations,
    generate_mermaid_diagram,
)
from app.models import ArchitectureDiagram, ValidationResult
from app.utils.chat import is_minimal_architecture, is_minimal_diagram
from app.validation.structural import format_validation_feedback, validate_architecture_structure
from app.validation.visual import validate_diagram_image


def sanitize_custom_message(text: str) -> str:
    return (text or "")[:MAX_CUSTOM_MESSAGE_LEN]


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
                    validation=None, logger=None):
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
        "validation": validation or {"structural": None, "vlm": None, "retries": 0, "feedback": None},
        "logs": logger.to_list() if logger else [],
    }


def _run_vlm_retry(user_prompt_or_none, messages, provider, platform, architecture_data,
                   current_architecture, logger, custom_msg, feedback, platform_convert,
                   previous_platform, current_xml, image_data):
    """Run VLM validation retry loop. Returns (architecture_data, xml_output, vlm_validation)."""
    vlm_validation = None
    for vlm_attempt in range(MAX_VLM_RETRIES + 1):
        vlm_validation = _validate_sync(image_data, logger)
        if vlm_validation.valid or vlm_attempt >= MAX_VLM_RETRIES:
            break
        feedback = format_validation_feedback(vlm_validation)
        if messages:
            architecture_data, _ = generate_architecture_from_chat(
                messages, provider, platform, architecture_data, current_architecture,
                logger, False, custom_msg, feedback, platform_convert, previous_platform, current_xml,
            )
        else:
            architecture_data = generate_architecture_json(
                user_prompt_or_none or "", provider, platform, logger, None,
                architecture_data, current_architecture, False, custom_msg, feedback,
            )
        xml_output = generate_xml(architecture_data, platform, logger)
        check = validate_and_fix_xml(xml_output)
        xml_output = check.fixed if check.fixed else xml_output
    return architecture_data, xml_output, vlm_validation


def _validate_sync(image_data, logger):
    return validate_diagram_image(image_data, logger)


def _retry_loop_sync(user_prompt_or_none, messages, provider, platform, current_architecture,
                     previous_architecture, logger, minimal_style, custom_msg, feedback,
                     platform_convert, previous_platform, current_xml):
    for attempt in range(MAX_STRUCTURAL_RETRIES + 1):
        if messages:
            architecture_data, assistant_message = generate_architecture_from_chat(
                messages, provider, platform, current_architecture, previous_architecture,
                logger, minimal_style, custom_msg, feedback,
                platform_convert, previous_platform, current_xml,
            )
        else:
            architecture_data = generate_architecture_json(
                user_prompt_or_none or "", provider, platform, logger, None,
                current_architecture, previous_architecture, minimal_style, custom_msg, feedback,
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

        if structural_validation.valid or attempt >= MAX_STRUCTURAL_RETRIES:
            return architecture_data, assistant_message, structural_validation
        feedback = format_validation_feedback(structural_validation)
    return architecture_data, assistant_message, structural_validation


async def run_mermaid_pipeline(*, user_prompt, messages, provider, logger):
    provider = (provider or "openai").strip().lower()
    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")

    logger.log("request", "Mermaid request", details={"provider": provider})

    result = await asyncio.to_thread(generate_mermaid_diagram, human_prompt, provider, logger, messages)

    logger.log("complete", "Mermaid pipeline finished", level="success", details={"chars": len(result.mermaid)})

    return _build_response(
        diagram_type="mermaid",
        mermaid=result.mermaid,
        suggestions=result.suggestions,
        assistant_message=result.assistant_message or "Generated Mermaid diagram.",
        logger=logger,
    )


async def run_edit_pipeline(*, messages, provider, platform, current_xml, previous_xml=None,
                             logger, custom_system_message="", validation_feedback=None,
                             image_data=None, context_text=None):
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

    for attempt in range(MAX_STRUCTURAL_RETRIES + 1):
        retry_count = attempt
        edit_response = await asyncio.to_thread(
            generate_edit_operations, messages, provider, xml_output, previous_xml,
            logger, custom_msg, feedback, context_text,
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
                if attempt >= MAX_STRUCTURAL_RETRIES:
                    xml_output = apply_result.result
                    break
                continue
            xml_output = apply_result.result
        else:
            logger.log("validation", "Edit response had no operations", level="warn")
            break

        check = validate_and_fix_xml(xml_output)
        xml_output = check.fixed if check.fixed else xml_output
        if check.valid or attempt >= MAX_STRUCTURAL_RETRIES:
            break
        feedback = f"XML validation failed: {check.error}"

    if image_data and not is_minimal_diagram(xml_output):
        for vlm_attempt in range(MAX_VLM_RETRIES + 1):
            vlm_validation = await asyncio.to_thread(validate_diagram_image, image_data, logger)
            if vlm_validation.valid or vlm_attempt >= MAX_VLM_RETRIES:
                break
            feedback = format_validation_feedback(vlm_validation)
            edit_response = await asyncio.to_thread(
                generate_edit_operations, messages, provider, xml_output, previous_xml,
                logger, custom_msg, feedback, context_text,
            )
            if edit_response.operations:
                xml_output = apply_diagram_operations(xml_output, edit_response.operations).result
            assistant_message = edit_response.assistant_message or assistant_message

    validation_summary = format_validation_feedback(vlm_validation) if vlm_validation and not vlm_validation.valid else None
    logger.log("complete", "XML edit finished", level="success", details={"xml_chars": len(xml_output)})

    return _build_response(
        xml=xml_output, suggestions=suggestions, assistant_message=assistant_message,
        edit_mode=True, validation={"structural": None, "vlm": vlm_validation.model_dump() if vlm_validation else None,
                                     "retries": retry_count, "feedback": validation_summary},
        logger=logger,
    )


async def run_generation_pipeline(*, user_prompt=None, messages=None, provider="openai", platform="aws",
                                   current_architecture=None, previous_architecture=None, logger=None,
                                   minimal_style=False, custom_system_message="",
                                   validation_feedback=None, image_data=None, context_text=None,
                                   platform_convert=False, previous_platform=None, current_xml=None):
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()

    logger.log("request", "Generation request", details={
        "platform": platform, "provider": provider, "chat_turns": len(messages) if messages else 1,
        "platform_convert": platform_convert,
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
        platform_convert, previous_platform, current_xml,
    )

    logger.log("layout", "Running auto-layout and XML builder")
    xml_output = await asyncio.to_thread(generate_xml, architecture_data, platform, logger)

    final_check = validate_and_fix_xml(xml_output)
    xml_output = final_check.fixed if final_check.fixed else xml_output

    if image_data and not is_minimal_architecture(architecture_data):
        architecture_data, xml_output, vlm_validation = await asyncio.to_thread(
            _run_vlm_retry, human_prompt, messages, provider, platform, architecture_data,
            current_architecture, logger, custom_msg, feedback, platform_convert,
            previous_platform, current_xml, image_data,
        )

    validation_summary = None
    if structural_validation and not structural_validation.valid:
        validation_summary = format_validation_feedback(structural_validation)
    if vlm_validation and not vlm_validation.valid:
        validation_summary = format_validation_feedback(vlm_validation)

    logger.log("complete", "Pipeline finished", level="success",
               details={"xml_chars": len(xml_output), "retries": 0})

    return _build_response(
        xml=xml_output,
        architecture=architecture_data.model_dump(),
        flow_steps=[s.model_dump() for s in architecture_data.flow_steps],
        pattern_id=architecture_data.pattern_id,
        title=architecture_data.title,
        suggestions=architecture_data.suggestions,
        assistant_message=assistant_message,
        validation={
            "structural": structural_validation.model_dump() if structural_validation else None,
            "vlm": vlm_validation.model_dump() if vlm_validation else None,
            "retries": 0,
            "feedback": validation_summary,
        },
        logger=logger,
    )
