import ssl_config  # noqa: F401 — trust OS CA store before any HTTPS clients

import os
import asyncio
import uvicorn
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, Response, JSONResponse
from pydantic import BaseModel

from agent import (
    generate_architecture_from_chat,
    generate_architecture_json,
    generate_edit_operations,
    MODEL_IDS,
    PLATFORM_LABELS,
)
from cached_responses import find_cached_response
from chat_helpers import is_minimal_architecture, is_minimal_diagram
from diagram_validator import format_validation_feedback, validate_architecture_structure
from draftsman import generate_xml
from models import ArchitectureDiagram
from pipeline_log import StepLogger
from url_utils import fetch_url_content
from validation_models import ValidationResult
from vlm_validator import validate_diagram_image
from xml_utils import (
    apply_diagram_operations,
    validate_and_fix_xml,
    wrap_with_mxfile,
)

# Force load the .env file immediately
load_dotenv()

MAX_STRUCTURAL_RETRIES = int(os.getenv("MAX_VALIDATION_RETRIES", "1"))
MAX_VLM_RETRIES = int(os.getenv("MAX_VLM_RETRIES", "2"))

app = FastAPI(title="Draw.io AI Architect")

# Mount static files and automatically redirect the root URL to the UI
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return RedirectResponse(url="/static/index.html")


class PromptRequest(BaseModel):
    user_prompt: str
    provider: str = "openai"
    platform: str = "aws"
    minimal_style: bool = False
    custom_system_message: str = ""
    context_text: str | None = None
    image_data: str | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    provider: str = "openai"
    platform: str = "aws"
    current_architecture: dict | None = None
    previous_architecture: dict | None = None
    current_xml: str | None = None
    previous_xml: str | None = None
    minimal_style: bool = False
    custom_system_message: str = ""
    validation_feedback: str | None = None
    image_data: str | None = None
    context_text: str | None = None
    has_image_input: bool = False


class ValidateDiagramRequest(BaseModel):
    image_data: str
    session_id: str | None = None


class ParseUrlRequest(BaseModel):
    url: str


def _sanitize_custom_message(text: str) -> str:
    return (text or "")[:5000]


def _platform_page_name(platform: str) -> str:
    labels = {
        "aws": "Amazon Web Services",
        "gcp": "Google Cloud Platform",
        "azure": "Microsoft Azure",
        "multi": "Multi-Cloud Architecture",
    }
    return labels.get(platform, "Architecture")


async def _try_cached_first_turn(
    *,
    prompt_text: str,
    has_image: bool,
    platform: str,
    logger: StepLogger,
) -> dict | None:
    cached = find_cached_response(prompt_text, has_image)
    if not cached:
        return None
    logger.log("cache", "Serving cached example diagram", level="success")
    xml_output = wrap_with_mxfile(cached.xml, _platform_page_name(platform))
    return {
        "status": "success",
        "suggestions": [],
        "xml": xml_output,
        "architecture": None,
        "assistant_message": "Loaded cached example diagram instantly.",
        "edit_mode": False,
        "validation": {"structural": None, "vlm": None, "retries": 0, "feedback": None},
        "logs": logger.to_list(),
    }


async def _run_edit_pipeline(
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
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()
    custom_msg = _sanitize_custom_message(custom_system_message)
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
            xml_output = wrap_with_mxfile(edit_response.xml, _platform_page_name(platform))
        elif edit_response.operations:
            apply_result = apply_diagram_operations(xml_output, edit_response.operations)
            if apply_result.errors:
                err_text = "\n".join(
                    f"- {e.type} on {e.cell_id}: {e.message}" for e in apply_result.errors
                )
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

        xml_check = validate_and_fix_xml(xml_output)
        if xml_check.fixed:
            xml_output = xml_check.fixed
        if xml_check.valid or attempt >= MAX_STRUCTURAL_RETRIES:
            break
        feedback = f"XML validation failed: {xml_check.error}"
        logger.log("validation", "XML validation failed — retrying edit", level="warn")

    if image_data and not is_minimal_diagram(xml_output):
        for vlm_attempt in range(MAX_VLM_RETRIES + 1):
            vlm_validation = await asyncio.to_thread(
                validate_diagram_image,
                image_data,
                logger,
            )
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

    logger.log("complete", "XML edit pipeline finished", level="success", details={"xml_chars": len(xml_output)})

    return {
        "status": "success",
        "suggestions": suggestions,
        "xml": xml_output,
        "architecture": None,
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


async def _run_pipeline(
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
) -> dict:
    platform = (platform or "aws").strip().lower()
    provider = (provider or "openai").strip().lower()

    logger.log(
        "request",
        "Generation request received",
        details={
            "platform": platform,
            "provider": provider,
            "model": MODEL_IDS.get(provider, MODEL_IDS["openai"]),
            "platform_label": PLATFORM_LABELS.get(platform, PLATFORM_LABELS["aws"]),
            "chat_turns": len(messages) if messages else 1,
            "minimal_style": minimal_style,
            "custom_instructions": bool(_sanitize_custom_message(custom_system_message)),
        },
    )

    human_prompt = user_prompt or (messages[-1]["content"] if messages else "")
    if context_text:
        human_prompt = f"{human_prompt}\n\n--- Context ---\n{context_text[:8000]}"

    if current_architecture is None and (not messages or len(messages) <= 1):
        cached_result = await _try_cached_first_turn(
            prompt_text=human_prompt,
            has_image=bool(image_data),
            platform=platform,
            logger=logger,
        )
        if cached_result:
            return cached_result

    custom_msg = _sanitize_custom_message(custom_system_message)
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
    xml_output = await asyncio.to_thread(
        generate_xml,
        architecture_data,
        platform,
        logger,
    )

    final_xml_check = validate_and_fix_xml(xml_output)
    if final_xml_check.fixes:
        logger.log("xml", "Auto-fixed final diagram XML", details={"fixes": final_xml_check.fixes})
    if final_xml_check.fixed:
        xml_output = final_xml_check.fixed
    if not final_xml_check.valid:
        logger.log(
            "xml",
            f"Final XML validation warning: {final_xml_check.error}",
            level="warn",
        )

    # VLM validation when client sends rendered PNG
    if image_data and not is_minimal_architecture(architecture_data):
        for vlm_attempt in range(MAX_VLM_RETRIES + 1):
            vlm_validation = await asyncio.to_thread(
                validate_diagram_image,
                image_data,
                logger,
            )
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
            graph_xml = await asyncio.to_thread(generate_xml, architecture_data, platform, logger)
            xml_output = graph_xml
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
        "suggestions": architecture_data.suggestions,
        "xml": xml_output,
        "architecture": architecture_data.model_dump(),
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


@app.post("/generate")
async def generate_diagram(request: PromptRequest):
    logger = StepLogger()
    try:
        cached = await _try_cached_first_turn(
            prompt_text=request.user_prompt,
            has_image=bool(request.image_data),
            platform=request.platform,
            logger=logger,
        )
        if cached:
            return cached
        result = await _run_pipeline(
            user_prompt=request.user_prompt,
            messages=None,
            provider=request.provider,
            platform=request.platform,
            current_architecture=None,
            previous_architecture=None,
            logger=logger,
            minimal_style=request.minimal_style,
            custom_system_message=_sanitize_custom_message(request.custom_system_message),
            context_text=request.context_text,
            image_data=request.image_data,
        )
        return result
    except Exception as e:
        logger.log("error", str(e), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e), "logs": logger.to_list()},
        )


@app.post("/chat")
async def chat_diagram(request: ChatRequest):
    logger = StepLogger()
    platform = (request.platform or "aws").strip().lower()
    provider = (request.provider or "openai").strip().lower()

    if not request.messages:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "messages must not be empty"},
        )
    if request.messages[-1].role != "user":
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Last message must be from the user"},
        )

    current_architecture = None
    previous_architecture = None
    if request.current_architecture:
        try:
            current_architecture = ArchitectureDiagram.model_validate(request.current_architecture)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": f"Invalid current_architecture: {e}"},
            )
    if request.previous_architecture:
        try:
            previous_architecture = ArchitectureDiagram.model_validate(request.previous_architecture)
        except Exception as e:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": f"Invalid previous_architecture: {e}"},
            )

    try:
        messages = [m.model_dump() for m in request.messages]
        last_user = messages[-1]["content"] if messages else ""

        use_xml_edit = bool(
            request.current_xml
            and request.current_xml.strip()
            and not is_minimal_diagram(request.current_xml)
        )

        if use_xml_edit:
            result = await _run_edit_pipeline(
                messages=messages,
                provider=provider,
                platform=platform,
                current_xml=request.current_xml or "",
                previous_xml=request.previous_xml,
                logger=logger,
                custom_system_message=_sanitize_custom_message(request.custom_system_message),
                validation_feedback=request.validation_feedback,
                image_data=request.image_data,
                context_text=request.context_text,
            )
            return result

        if is_minimal_diagram(request.current_xml) and len(messages) == 1:
            cached = await _try_cached_first_turn(
                prompt_text=last_user,
                has_image=request.has_image_input or bool(request.image_data),
                platform=platform,
                logger=logger,
            )
            if cached:
                return cached

        result = await _run_pipeline(
            user_prompt=None,
            messages=messages,
            provider=provider,
            platform=platform,
            current_architecture=current_architecture,
            previous_architecture=previous_architecture,
            logger=logger,
            minimal_style=request.minimal_style,
            custom_system_message=_sanitize_custom_message(request.custom_system_message),
            validation_feedback=request.validation_feedback,
            image_data=request.image_data,
            context_text=request.context_text,
        )
        return result
    except Exception as e:
        logger.log("error", str(e), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e), "logs": logger.to_list()},
        )


@app.post("/validate-diagram")
async def validate_diagram(request: ValidateDiagramRequest):
    """VLM visual validation of a rendered diagram PNG — port of next-ai-draw-io /api/validate-diagram."""
    logger = StepLogger()
    try:
        result = await asyncio.to_thread(
            validate_diagram_image,
            request.image_data,
            logger,
        )
        return {
            "status": "success",
            "validation": result.model_dump(),
            "logs": logger.to_list(),
        }
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": str(e), "logs": logger.to_list()},
        )
    except Exception as e:
        logger.log("error", str(e), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(e), "logs": logger.to_list()},
        )


@app.post("/parse-url")
async def parse_url(request: ParseUrlRequest):
    """Extract text content from a URL for diagram context."""
    logger = StepLogger()
    try:
        data = await fetch_url_content(request.url)
        logger.log("parse-url", f"Extracted {data['char_count']} chars", level="success")
        return {"status": "success", **data, "logs": logger.to_list()}
    except Exception as e:
        logger.log("error", str(e), level="error")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": str(e), "logs": logger.to_list()},
        )


@app.post("/download")
async def download_diagram(request: PromptRequest):
    """Generate and return the diagram as a downloadable .drawio file."""
    try:
        architecture_data = generate_architecture_json(request.user_prompt, request.provider, request.platform)
        xml_output = generate_xml(architecture_data, request.platform)
        return Response(
            content=xml_output,
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=architecture.drawio"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
