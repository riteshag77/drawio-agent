from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.adapters.inbound.web.schemas import ChatRequest, ParseUrlRequest, PromptRequest
from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.utils.web import fetch_url_content
from app.core.logging import StepLogger
from app.core.services.agent import generate_mermaid_diagram
from app.core.services.xml_pipeline import run_xml_pipeline

router = APIRouter()


def _logger_from_request(request: Request) -> StepLogger:
    return StepLogger(request_id=getattr(request.state, "request_id", None))


_CLOUD_KWS = frozenset({
    "aws", "amazon", "gcp", "google cloud", "azure", "microsoft azure",
    "vpc", "vnet", "subnet", "lambda", "ecs", "eks", "kubernetes", "k8s",
    "cloudfront", "route 53", "route53", "alb", "load balancer",
    "rds", "aurora", "dynamodb", "s3", "cloud architecture",
    "three tier", "3-tier", "microservices", "serverless",
    "api gateway", "cloud run", "cloud sql", "cloud watch",
})

_SIMPLE_KWS = frozenset({
    "flowchart", "flow chart", "sequence diagram", "sequence chart",
    "org chart", "organization chart", "organizational chart",
    "state diagram", "state machine", "simple diagram",
    "basic flow", "no cloud", "mermaid", "process flow", "workflow diagram",
})


def _classify_intent(prompt: str, *, diagram_mode: str = "auto", platform: str = "aws") -> str:
    mode = (diagram_mode or "auto").strip().lower()
    if mode in ("cloud", "simple"):
        return mode
    text = (prompt or "").lower()
    has_cloud = any(kw in text for kw in _CLOUD_KWS)
    has_simple = any(kw in text for kw in _SIMPLE_KWS)
    if has_simple and not has_cloud:
        return "simple"
    return "cloud"


def _sanitize_custom_msg(text: str) -> str:
    return (text or "")[:get_config().max_custom_message_len]


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
        intent = _classify_intent(body.user_prompt, diagram_mode=body.diagram_mode, platform=body.platform)
        if intent == "simple":
            result = await asyncio.to_thread(
                generate_mermaid_diagram, body.user_prompt, body.provider, logger, None,
            )
            return {
                "status": "success",
                "diagram_type": "mermaid",
                "mermaid": result.mermaid,
                "suggestions": result.suggestions or [],
                "assistant_message": result.assistant_message or "Generated Mermaid diagram.",
                "xml": None, "renderer_used": None, "mcp_preview_url": None,
                "logs": logger.to_list() if logger else [],
            }
        return await run_xml_pipeline(
            user_prompt=body.user_prompt, messages=None,
            provider=body.provider, platform=body.platform,
            logger=logger,
            custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            diagram_renderer=body.diagram_renderer,
            three_d=body.three_d,
            cost_estimate=body.cost_estimate,
            well_architected=body.well_architected,
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

    try:
        messages = [m.model_dump() for m in body.messages]
        last_user = messages[-1]["content"] if messages else ""
        applied_suggestions = body.applied_suggestions or []

        intent = _classify_intent(last_user, diagram_mode=body.diagram_mode, platform=platform)
        if intent == "simple":
            result = await asyncio.to_thread(
                generate_mermaid_diagram, last_user, provider, logger, messages, applied_suggestions,
            )
            return {
                "status": "success",
                "diagram_type": "mermaid",
                "mermaid": result.mermaid,
                "suggestions": result.suggestions or [],
                "assistant_message": result.assistant_message or "Generated Mermaid diagram.",
                "xml": None, "renderer_used": None, "mcp_preview_url": None,
                "logs": logger.to_list() if logger else [],
            }
        return await run_xml_pipeline(
            user_prompt=None, messages=messages, provider=provider, platform=platform,
            logger=logger,
            custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            diagram_renderer=body.diagram_renderer,
            applied_suggestions=applied_suggestions,
            three_d=body.three_d,
            cost_estimate=body.cost_estimate,
            well_architected=body.well_architected,
        )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/download")
async def download_diagram(body: PromptRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        result = await run_xml_pipeline(
            user_prompt=body.user_prompt, messages=None,
            provider=body.provider, platform=body.platform,
            logger=logger,
            custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            diagram_renderer=body.diagram_renderer,
            three_d=body.three_d,
            cost_estimate=body.cost_estimate,
            well_architected=body.well_architected,
        )
        xml_output = result.get("xml", "")
        return Response(
            content=xml_output,
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=architecture.drawio"},
        )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


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
