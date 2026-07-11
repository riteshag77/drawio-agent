from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.adapters.inbound.web.schemas import ChatRequest, ParseUrlRequest, PromptRequest, ValidateDiagramRequest
from app.adapters.outbound.config.env_config import get_config
from app.core.logging import StepLogger
from app.adapters.outbound.diagram.draftsman import generate_xml
from app.core.services.agent import generate_architecture_json
from app.domain.entities import ArchitectureDiagram
from app.core.services.pipeline import (
    classify_diagram_intent,
    run_edit_pipeline,
    run_generation_pipeline,
    run_mermaid_pipeline,
    sanitize_custom_message,
)
from app.adapters.outbound.utils.web import fetch_url_content
from app.adapters.outbound.validation.visual import validate_diagram_image
from app.adapters.outbound.utils.chat import is_minimal_diagram

router = APIRouter()


def _logger_from_request(request: Request) -> StepLogger:
    return StepLogger(request_id=getattr(request.state, "request_id", None))


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": get_config().app_title,
        "version": "1.0.0",
    }


@router.post("/generate")
async def generate_diagram(body: PromptRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        intent = classify_diagram_intent(
            body.user_prompt,
            diagram_mode=body.diagram_mode,
            platform=body.platform,
        )
        if intent == "simple":
            return await run_mermaid_pipeline(
                user_prompt=body.user_prompt, messages=None,
                provider=body.provider, logger=logger,
            )
        return await run_generation_pipeline(
            user_prompt=body.user_prompt, messages=None,
            provider=body.provider, platform=body.platform,
            current_architecture=None, previous_architecture=None,
            logger=logger, minimal_style=body.minimal_style,
            custom_system_message=sanitize_custom_message(body.custom_system_message),
            context_text=body.context_text, image_data=body.image_data,
            diagram_renderer=body.diagram_renderer,
        )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/chat")
async def chat_diagram(body: ChatRequest, request: Request):
    logger = _logger_from_request(request)
    platform = (body.platform or "aws").strip().lower()
    provider = (body.provider or "openai").strip().lower()

    if not body.messages:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "messages must not be empty"})
    if body.messages[-1].role != "user":
        return JSONResponse(status_code=400, content={"status": "error", "detail": "Last message must be from the user"})

    current_architecture = None
    previous_architecture = None
    if body.current_architecture:
        try:
            current_architecture = ArchitectureDiagram.model_validate(body.current_architecture)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"status": "error", "detail": f"Invalid current_architecture: {exc}"})
    if body.previous_architecture:
        try:
            previous_architecture = ArchitectureDiagram.model_validate(body.previous_architecture)
        except Exception as exc:
            return JSONResponse(status_code=400, content={"status": "error", "detail": f"Invalid previous_architecture: {exc}"})

    try:
        messages = [m.model_dump() for m in body.messages]
        last_user = messages[-1]["content"] if messages else ""
        applied_suggestions = body.applied_suggestions or []

        use_xml_edit = bool(body.current_xml and body.current_xml.strip() and not is_minimal_diagram(body.current_xml))

        if body.platform_convert:
            return await run_generation_pipeline(
                user_prompt=None, messages=messages, provider=provider, platform=platform,
                current_architecture=current_architecture, previous_architecture=previous_architecture,
                logger=logger, minimal_style=body.minimal_style,
                custom_system_message=sanitize_custom_message(body.custom_system_message),
                validation_feedback=body.validation_feedback, image_data=body.image_data,
                context_text=body.context_text, platform_convert=True,
                previous_platform=body.previous_platform, current_xml=body.current_xml,
                validation_provider=body.validation_provider,
                applied_suggestions=applied_suggestions,
                diagram_renderer=body.diagram_renderer,
            )

        if use_xml_edit:
            return await run_edit_pipeline(
                messages=messages, provider=provider, platform=platform,
                current_xml=body.current_xml or "", previous_xml=body.previous_xml,
                logger=logger, custom_system_message=sanitize_custom_message(body.custom_system_message),
                validation_feedback=body.validation_feedback, image_data=body.image_data,
                context_text=body.context_text,
                validation_provider=body.validation_provider,
                applied_suggestions=applied_suggestions,
            )

        intent = classify_diagram_intent(last_user, diagram_mode=body.diagram_mode, platform=platform)
        if intent == "simple" and not use_xml_edit and current_architecture is None:
            return await run_mermaid_pipeline(
                user_prompt=None, messages=messages, provider=provider, logger=logger,
                applied_suggestions=applied_suggestions,
            )

        return await run_generation_pipeline(
            user_prompt=None, messages=messages, provider=provider, platform=platform,
            current_architecture=current_architecture, previous_architecture=previous_architecture,
            logger=logger, minimal_style=body.minimal_style,
            custom_system_message=sanitize_custom_message(body.custom_system_message),
            validation_feedback=body.validation_feedback, image_data=body.image_data,
            context_text=body.context_text,
            validation_provider=body.validation_provider,
            applied_suggestions=applied_suggestions,
            diagram_renderer=body.diagram_renderer,
        )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/validate-diagram")
async def validate_diagram(body: ValidateDiagramRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        result = await asyncio.to_thread(validate_diagram_image, body.image_data, logger, body.validation_provider)
        return {"status": "success", "validation": result.model_dump(), "logs": logger.to_list()}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc), "logs": logger.to_list()})
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc), "logs": logger.to_list()})


@router.post("/parse-url")
async def parse_url(body: ParseUrlRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        data = await fetch_url_content(body.url)
        logger.log("parse-url", f"Extracted {data['char_count']} chars", level="success")
        return {"status": "success", **data, "logs": logger.to_list()}
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc), "logs": logger.to_list()})


@router.post("/download")
async def download_diagram(body: PromptRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        architecture_data = generate_architecture_json(
            body.user_prompt, body.provider, body.platform,
        )
        xml_output = generate_xml(architecture_data, body.platform)
        return Response(
            content=xml_output,
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=architecture.drawio"},
        )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
