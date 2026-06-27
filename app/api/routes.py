"""HTTP route handlers for diagram generation, editing, and validation."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.api.schemas import (
    ChatRequest,
    ParseUrlRequest,
    PromptRequest,
    ValidateDiagramRequest,
)
from app.core.logging import StepLogger
from app.diagram.draftsman import generate_xml
from app.llm.agent import generate_architecture_json
from app.models import ArchitectureDiagram
from app.services.pipeline import (
    classify_diagram_intent,
    run_edit_pipeline,
    run_generation_pipeline,
    run_mermaid_pipeline,
    sanitize_custom_message,
    try_cached_first_turn,
)
from app.utils.chat import is_minimal_diagram
from app.utils.web import fetch_url_content
from app.validation.visual import validate_diagram_image

router = APIRouter()


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@router.post("/generate")
async def generate_diagram(request: PromptRequest):
    logger = StepLogger()
    try:
        cached = await try_cached_first_turn(
            prompt_text=request.user_prompt,
            has_image=bool(request.image_data),
            platform=request.platform,
            logger=logger,
        )
        if cached:
            return cached
        intent = classify_diagram_intent(
            request.user_prompt,
            diagram_mode=request.diagram_mode,
            platform=request.platform,
        )
        if intent == "simple":
            return await run_mermaid_pipeline(
                user_prompt=request.user_prompt,
                messages=None,
                provider=request.provider,
                logger=logger,
            )
        return await run_generation_pipeline(
            user_prompt=request.user_prompt,
            messages=None,
            provider=request.provider,
            platform=request.platform,
            current_architecture=None,
            previous_architecture=None,
            logger=logger,
            minimal_style=request.minimal_style,
            custom_system_message=sanitize_custom_message(request.custom_system_message),
            context_text=request.context_text,
            image_data=request.image_data,
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the client as JSON
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/chat")
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
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": f"Invalid current_architecture: {exc}"},
            )
    if request.previous_architecture:
        try:
            previous_architecture = ArchitectureDiagram.model_validate(request.previous_architecture)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                status_code=400,
                content={"status": "error", "detail": f"Invalid previous_architecture: {exc}"},
            )

    try:
        messages = [m.model_dump() for m in request.messages]
        last_user = messages[-1]["content"] if messages else ""

        use_xml_edit = bool(
            request.current_xml
            and request.current_xml.strip()
            and not is_minimal_diagram(request.current_xml)
        )

        if request.platform_convert:
            return await run_generation_pipeline(
                user_prompt=None,
                messages=messages,
                provider=provider,
                platform=platform,
                current_architecture=current_architecture,
                previous_architecture=previous_architecture,
                logger=logger,
                minimal_style=request.minimal_style,
                custom_system_message=sanitize_custom_message(request.custom_system_message),
                validation_feedback=request.validation_feedback,
                image_data=request.image_data,
                context_text=request.context_text,
                platform_convert=True,
                previous_platform=request.previous_platform,
                current_xml=request.current_xml,
            )

        if use_xml_edit:
            return await run_edit_pipeline(
                messages=messages,
                provider=provider,
                platform=platform,
                current_xml=request.current_xml or "",
                previous_xml=request.previous_xml,
                logger=logger,
                custom_system_message=sanitize_custom_message(request.custom_system_message),
                validation_feedback=request.validation_feedback,
                image_data=request.image_data,
                context_text=request.context_text,
            )

        if is_minimal_diagram(request.current_xml) and len(messages) == 1:
            cached = await try_cached_first_turn(
                prompt_text=last_user,
                has_image=request.has_image_input or bool(request.image_data),
                platform=platform,
                logger=logger,
            )
            if cached:
                return cached

        intent = classify_diagram_intent(
            last_user,
            diagram_mode=request.diagram_mode,
            platform=platform,
        )
        if intent == "simple" and not use_xml_edit and current_architecture is None:
            return await run_mermaid_pipeline(
                user_prompt=None,
                messages=messages,
                provider=provider,
                logger=logger,
            )

        return await run_generation_pipeline(
            user_prompt=None,
            messages=messages,
            provider=provider,
            platform=platform,
            current_architecture=current_architecture,
            previous_architecture=previous_architecture,
            logger=logger,
            minimal_style=request.minimal_style,
            custom_system_message=sanitize_custom_message(request.custom_system_message),
            validation_feedback=request.validation_feedback,
            image_data=request.image_data,
            context_text=request.context_text,
        )
    except Exception as exc:  # noqa: BLE001
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/validate-diagram")
async def validate_diagram(request: ValidateDiagramRequest):
    """VLM visual validation of a rendered diagram PNG."""
    logger = StepLogger()
    try:
        result = await asyncio.to_thread(validate_diagram_image, request.image_data, logger)
        return {
            "status": "success",
            "validation": result.model_dump(),
            "logs": logger.to_list(),
        }
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )
    except Exception as exc:  # noqa: BLE001
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/parse-url")
async def parse_url(request: ParseUrlRequest):
    """Extract readable text content from a URL for diagram context."""
    logger = StepLogger()
    try:
        data = await fetch_url_content(request.url)
        logger.log("parse-url", f"Extracted {data['char_count']} chars", level="success")
        return {"status": "success", **data, "logs": logger.to_list()}
    except Exception as exc:  # noqa: BLE001
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/download")
async def download_diagram(request: PromptRequest):
    """Generate and return the diagram as a downloadable .drawio file."""
    try:
        architecture_data = generate_architecture_json(
            request.user_prompt, request.provider, request.platform
        )
        xml_output = generate_xml(architecture_data, request.platform)
        return Response(
            content=xml_output,
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=architecture.drawio"},
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc
