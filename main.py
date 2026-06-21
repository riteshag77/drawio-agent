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
    MODEL_IDS,
    PLATFORM_LABELS,
)
from chat_helpers import is_minimal_architecture
from diagram_validator import format_validation_feedback, validate_architecture_structure
from draftsman import generate_xml
from models import ArchitectureDiagram
from pipeline_log import StepLogger
from validation_models import ValidationResult
from vlm_validator import validate_diagram_image
from xml_utils import validate_and_fix_xml

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


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    provider: str = "openai"
    platform: str = "aws"
    current_architecture: dict | None = None
    previous_architecture: dict | None = None
    minimal_style: bool = False
    custom_system_message: str = ""
    validation_feedback: str | None = None
    image_data: str | None = None


class ValidateDiagramRequest(BaseModel):
    image_data: str
    session_id: str | None = None


def _sanitize_custom_message(text: str) -> str:
    return (text or "")[:5000]


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
