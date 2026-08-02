from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from app.adapters.inbound.web.schemas import ChatRequest, ParseUrlRequest, PromptRequest
from app.adapters.outbound.capture_store import list_captures, load_capture, save_capture
from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.history_store import (
    delete_session,
    get_version,
    list_sessions,
    list_versions,
    save_version,
)
from app.adapters.outbound.llm.concurrency import run_llm_sync
from app.adapters.outbound.utils.documents import MAX_ATTACHMENT_BYTES, extract_document
from app.adapters.outbound.utils.web import fetch_url_content
from app.core.logging import StepLogger
from app.core.services.agent import generate_mermaid_diagram
from app.core.services.intent import classify_intent
from app.core.services.xml_pipeline import _summarize_llm_usage, run_xml_pipeline
from app.adapters.outbound.llm import providers as providers_module

router = APIRouter()


class PipelineTimeoutError(RuntimeError):
    """Raised when a request exceeds the configured overall time budget (REQUEST_TIMEOUT_SECONDS)."""


async def _with_request_timeout(coro: Any) -> Any:
    """Bound a diagram-generation coroutine to request_timeout_seconds.

    A stuck/retrying LLM call can otherwise stall a request indefinitely — this
    cancels the *awaiting coroutine* on timeout so the user gets a clean 504
    instead of an infinite wait. It does not kill the underlying worker thread
    (Python threads can't be forcibly killed); see llm_client.call_llm's retry
    backoff for the actual source of long-running background threads.
    """
    timeout = get_config().request_timeout_seconds
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise PipelineTimeoutError(
            f"The request took longer than {timeout:g}s and was cancelled. "
            "The LLM provider (or a retry/backoff) may be taking unusually long — try again shortly."
        ) from exc


def _logger_from_request(request: Request) -> StepLogger:
    return StepLogger(request_id=getattr(request.state, "request_id", None))


def _sanitize_custom_msg(text: str) -> str:
    return (text or "")[:get_config().max_custom_message_len]


def _mermaid_response(result: Any, logger: StepLogger) -> dict[str, Any]:
    return {
        "status": "success",
        "diagram_type": "mermaid",
        "mermaid": result.mermaid,
        "suggestions": result.suggestions or [],
        "assistant_message": result.assistant_message or "Generated Mermaid diagram.",
        "xml": None,
        "renderer_used": None,
        "mcp_preview_url": None,
        "llm_usage": _summarize_llm_usage(logger),
        "logs": logger.to_list() if logger else [],
    }


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@router.get("/providers")
async def list_providers() -> dict:
    return {
        "providers": [
            {"key": spec.key, "label": spec.label or spec.key} for spec in providers_module.PROVIDERS.values()
        ],
        "default": providers_module.default_provider_key(),
    }


@router.get("/health")
async def health() -> dict:
    import os
    return {
        "status": "ok",
        "service": get_config().app_title,
        "version": os.environ.get("APP_VERSION", "1.0.0"),
    }


@router.get("/ready")
async def ready():
    """Readiness probe — returns 200 when the app can serve requests, 503 otherwise."""
    import os
    from pathlib import Path
    from fastapi.responses import JSONResponse

    issues: list[str] = []

    # At least one LLM key must be set
    llm_key_envs = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "OPENCODE_API_KEY"]
    if not any(os.environ.get(k) for k in llm_key_envs):
        issues.append("No LLM API key configured (set at least one of ANTHROPIC/OPENAI/GOOGLE/OPENROUTER/OPENCODE API key)")

    # Style-cache directory must be writable
    cache_path = get_config().drawio_style_cache_path
    if cache_path:
        cache_dir = Path(cache_path).parent
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            test_file = cache_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
        except OSError as exc:
            issues.append(f"Style cache directory not writable: {exc}")

    if issues:
        return JSONResponse(status_code=503, content={"status": "not ready", "issues": issues})
    return {"status": "ready"}


@router.post("/generate")
async def generate_diagram(body: PromptRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        intent = classify_intent(body.user_prompt, diagram_mode=body.diagram_mode, platform=body.platform)
        if intent == "simple":
            result = await _with_request_timeout(run_llm_sync(
                generate_mermaid_diagram, body.user_prompt, body.provider, logger, None,
            ))
            return _mermaid_response(result, logger)
        return await _with_request_timeout(run_xml_pipeline(
            user_prompt=body.user_prompt, messages=None,
            provider=body.provider, platform=body.platform,
            logger=logger,
            custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            diagram_renderer=body.diagram_renderer,
            three_d=body.three_d,
            cost_estimate=body.cost_estimate,
            well_architected=body.well_architected,
            compliance=body.compliance,
            compliance_frameworks=body.compliance_frameworks,
            libavoid=body.libavoid,
            image_replicate=body.image_replicate,
            animated_connectors=body.animated_connectors,
            include_suggestions=body.suggestions,
            diagram_views=body.diagram_views,
            adr_notes=body.adr_notes,
            raid_register=body.raid_register,
            interview_answers=body.interview_answers,
        ))
    except PipelineTimeoutError as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=504,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


async def _run_chat(body: ChatRequest, logger: StepLogger, *, on_progress=None) -> dict[str, Any]:
    platform = (body.platform or "aws").strip().lower()
    provider = (body.provider or "claude").strip().lower()

    if not body.messages:
        raise ValueError("messages must not be empty")
    if body.messages[-1].role != "user":
        raise ValueError("Last message must be from the user")

    messages = [m.model_dump() for m in body.messages]
    last_user = messages[-1]["content"] if messages else ""
    applied_suggestions = body.applied_suggestions or []

    intent = classify_intent(last_user, diagram_mode=body.diagram_mode, platform=platform)
    has_attachments = bool(body.attachment_documents) or bool(body.context_text)
    if intent == "simple" and has_attachments:
        # The mermaid/"simple" path never forwards attachments to the LLM — only
        # run_xml_pipeline does, so an attached file must not be routed away from it.
        intent = "cloud"
    if intent == "simple" and body.current_xml and len(body.current_xml) > 200:
        # A stale "simple" diagram_mode setting must never derail an in-progress
        # cloud-diagram edit (e.g. applying a WAF/Compliance suggestion) onto the
        # Mermaid path — that path returns xml=None and would replace the existing
        # cloud diagram with an unrelated flowchart.
        intent = "cloud"
    if on_progress:
        await on_progress("intent", f"Classified as {intent}", {"intent": intent})

    # Architecture interview — only when setting is on and answers not yet provided.
    # Skip when surgically editing an existing diagram (current_xml present).
    if (
        intent != "simple"
        and body.architecture_interview
        and not body.interview_answers
        and not (body.current_xml and len(body.current_xml) > 200)
    ):
        from app.adapters.outbound.diagram.architecture_docs import INTERVIEW_QUESTIONS
        questions = INTERVIEW_QUESTIONS
        lines = [
            "Before I draw the architecture, please answer these questions "
            "(reply in one message):",
            "",
        ]
        for i, q in enumerate(questions, start=1):
            lines.append(f"{i}. {q['prompt']}")
        msg = "\n".join(lines)
        logger.log("interview", "Architecture interview required", details={"questions": len(questions)})
        return {
            "status": "success",
            "interview_required": True,
            "interview_questions": questions,
            "assistant_message": msg,
            "xml": None,
            "mermaid": None,
            "diagram_type": None,
            "suggestions": [],
            "logs": logger.to_list(),
        }

    if intent == "simple":
        result = await _with_request_timeout(run_llm_sync(
            generate_mermaid_diagram, last_user, provider, logger, messages, applied_suggestions,
            include_suggestions=body.suggestions,
        ))
        response = _mermaid_response(result, logger)
    else:
        await _snapshot_pre_edit(body)
        response = await _with_request_timeout(run_xml_pipeline(
            user_prompt=None, messages=messages, provider=provider, platform=platform,
            logger=logger,
            custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            diagram_renderer=body.diagram_renderer,
            applied_suggestions=applied_suggestions,
            three_d=body.three_d,
            cost_estimate=body.cost_estimate,
            well_architected=body.well_architected,
            compliance=body.compliance,
            compliance_frameworks=body.compliance_frameworks,
            context_text=body.context_text,
            attachment_documents=[a.model_dump() for a in body.attachment_documents] if body.attachment_documents else None,
            current_xml=body.current_xml,
            libavoid=body.libavoid,
            image_replicate=body.image_replicate,
            animated_connectors=body.animated_connectors,
            include_suggestions=body.suggestions,
            diagram_views=body.diagram_views,
            adr_notes=body.adr_notes,
            raid_register=body.raid_register,
            interview_answers=body.interview_answers,
            on_progress=on_progress,
        ))

    if body.save_captures:
        await asyncio.to_thread(save_capture, body.model_dump(), response)
    await _record_history(body, response, last_user)
    return response


async def _snapshot_pre_edit(body: ChatRequest) -> None:
    """Persist live canvas XML before an AI mutation so users can restore."""
    if not body.session_id or not body.current_xml or len(body.current_xml) <= 200:
        return
    try:
        await asyncio.to_thread(
            save_version,
            session_id=body.session_id,
            xml=body.current_xml,
            prompt="(pre-edit snapshot)",
            platform=body.platform or "aws",
            provider=body.provider or "",
            meta={"kind": "pre_edit"},
        )
    except Exception:
        pass


async def _record_history(body: ChatRequest, response: dict[str, Any], prompt: str) -> None:
    """Append this generation to the session's version history.

    Best-effort by design — the store swallows its own errors and we ignore the
    result. A history write must never turn a delivered diagram into a failure.
    """
    if not body.session_id or not response.get("xml") or response.get("status") != "success":
        return
    await asyncio.to_thread(
        save_version,
        session_id=body.session_id,
        xml=response["xml"],
        prompt=prompt,
        title=(response.get("assistant_message") or "")[:120],
        platform=(body.platform or "").lower(),
        provider=(body.provider or "").lower(),
        generation_path=response.get("generation_path") or "",
        renderer_used=response.get("renderer_used") or "",
        meta={
            "diagram_views": body.diagram_views or [],
            "llm_usage": response.get("llm_usage"),
        },
    )


@router.post("/chat")
async def chat_diagram(body: ChatRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        return await _run_chat(body, logger)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc)})
    except PipelineTimeoutError as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=504,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )


@router.post("/chat/stream")
async def chat_diagram_stream(body: ChatRequest, request: Request):
    """SSE progress events, then a final ``result`` or ``error`` event."""
    logger = _logger_from_request(request)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def on_progress(step: str, message: str, details: dict[str, Any] | None = None) -> None:
        await queue.put({
            "type": "progress",
            "step": step,
            "message": message,
            "details": details or {},
            "logs": logger.to_list(),
        })

    async def runner() -> None:
        try:
            result = await _run_chat(body, logger, on_progress=on_progress)
            await queue.put({"type": "result", **result})
        except ValueError as exc:
            await queue.put({"type": "error", "status": "error", "detail": str(exc), "logs": logger.to_list()})
        except PipelineTimeoutError as exc:
            logger.log("error", str(exc), level="error")
            await queue.put({
                "type": "error",
                "status": "error",
                "detail": str(exc),
                "timeout": True,
                "logs": logger.to_list(),
            })
        except Exception as exc:
            logger.log("error", str(exc), level="error")
            await queue.put({
                "type": "error",
                "status": "error",
                "detail": str(exc),
                "logs": logger.to_list(),
            })
        finally:
            await queue.put(None)

    async def event_gen() -> AsyncIterator[str]:
        task = asyncio.create_task(runner())
        try:
            while True:
                if await request.is_disconnected():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if item is None:
                    break
                yield _sse(item)
        finally:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/captures")
async def get_captures():
    """Metadata (id/timestamp/prompt snippet) for every captured response, newest first."""
    return {"captures": await asyncio.to_thread(list_captures)}


@router.get("/captures/{capture_id}")
async def get_capture(capture_id: str):
    """Full saved request+response for one capture, for playback — no LLM/MCP call."""
    record = await asyncio.to_thread(load_capture, capture_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Capture not found")
    return record


@router.get("/history/sessions")
async def history_sessions(limit: int = 50):
    """Every session that has stored versions, most recently updated first."""
    return {"sessions": await asyncio.to_thread(list_sessions, limit)}


@router.get("/history/sessions/{session_id}")
async def history_session_versions(session_id: str, limit: int = 50):
    """Version metadata for one session, newest first. XML payloads are omitted."""
    versions = await asyncio.to_thread(list_versions, session_id, limit)
    return {"session_id": session_id, "versions": [v.summary() for v in versions]}


@router.get("/history/versions/{version_id}")
async def history_version(version_id: str):
    """One stored version including its full XML — used to restore a diagram."""
    record = await asyncio.to_thread(get_version, version_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return record.full()


@router.delete("/history/sessions/{session_id}")
async def history_delete_session(session_id: str):
    deleted = await asyncio.to_thread(delete_session, session_id)
    return {"session_id": session_id, "deleted": deleted}


@router.post("/download")
async def download_diagram(body: PromptRequest, request: Request):
    logger = _logger_from_request(request)
    try:
        result = await _with_request_timeout(run_xml_pipeline(
            user_prompt=body.user_prompt, messages=None,
            provider=body.provider, platform=body.platform,
            logger=logger,
            custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            diagram_renderer=body.diagram_renderer,
            three_d=body.three_d,
            cost_estimate=body.cost_estimate,
            well_architected=body.well_architected,
            compliance=body.compliance,
            compliance_frameworks=body.compliance_frameworks,
            libavoid=body.libavoid,
            image_replicate=body.image_replicate,
            animated_connectors=body.animated_connectors,
        ))
        xml_output = result.get("xml", "")
        return Response(
            content=xml_output,
            media_type="application/xml",
            headers={"Content-Disposition": "attachment; filename=architecture.drawio"},
        )
    except PipelineTimeoutError as exc:
        logger.log("error", str(exc), level="error")
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/attachments/extract")
async def extract_attachment(request: Request, file: UploadFile = File(...)):
    """Read an attached PDF/text/image/draw.io file and return text, vision
    payload, or validated diagram XML. Never raises for a bad file — one
    unreadable attachment must not break the rest of the batch or the chat flow."""
    logger = _logger_from_request(request)
    filename = file.filename or "attachment"

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ATTACHMENT_BYTES:
            logger.log("attachment", f"Rejected '{filename}': exceeds size limit", level="warning")
            return {
                "status": "error", "filename": filename,
                "detail": f"'{filename}' is too large — the limit is {MAX_ATTACHMENT_BYTES // 1_048_576} MB.",
            }
        chunks.append(chunk)
    data = b"".join(chunks)

    # extract_document does synchronous PDF/image parsing (pypdf) — run off the
    # event loop so a large attachment doesn't stall other in-flight requests.
    result = await asyncio.to_thread(extract_document, filename, data)
    if result.kind == "error":
        logger.log("attachment", f"Could not read '{filename}': {result.message}", level="warning")
        return {"status": "error", "filename": filename, "detail": result.message}

    logger.log("attachment", f"Read '{filename}' as {result.kind}", level="success", details={
        "kind": result.kind, "truncated": result.truncated,
    })
    if result.kind == "text":
        return {"status": "success", "kind": "text", "filename": filename, "content": result.content, "truncated": result.truncated}
    if result.kind == "diagram":
        return {
            "status": "success",
            "kind": "diagram",
            "filename": filename,
            "content": result.content,
            "mime": result.mime or "application/xml",
        }
    return {"status": "success", "kind": "vision", "filename": filename, "mime": result.mime, "data": result.data_b64}


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
