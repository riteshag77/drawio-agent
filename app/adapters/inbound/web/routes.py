from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from app.adapters.inbound.web.schemas import (
    AnalyzeRequest,
    ChatRequest,
    CustomProviderConfig,
    ParseUrlRequest,
    PromptRequest,
    TemplateCreate,
    TemplateUpdate,
    ValidateVisualRequest,
)
from app.adapters.outbound.capture_store import list_captures, load_capture, save_capture
from app.adapters.outbound.compliance import assess_compliance
from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.cost_estimator import estimate_monthly_cost
from app.adapters.outbound.well_architected import assess_well_architected
from app.adapters.outbound.history_store import (
    delete_session,
    get_version,
    list_sessions,
    list_versions,
    save_version,
)
from app.adapters.outbound.llm.providers import default_provider_key
from app.adapters.outbound.template_store import (
    bump_run_count,
    create_template,
    delete_template,
    list_templates,
    update_template,
)
from app.adapters.outbound.utils.documents import MAX_ATTACHMENT_BYTES, extract_document
from app.adapters.outbound.utils.web import fetch_url_content
from app.core.logging import StepLogger
from app.core.services.agent import generate_followup_suggestions_for_xml, run_visual_qa_llm
from app.core.services.intent import classify_intent, wants_three_d
from app.core.services.xml_pipeline import run_simple_pipeline, run_xml_pipeline
from app.adapters.outbound.llm import providers as providers_module

router = APIRouter()


class PipelineTimeoutError(RuntimeError):
    """Raised when a request exceeds the configured overall time budget (REQUEST_TIMEOUT_SECONDS)."""


async def _with_request_timeout(coro: Any) -> Any:
    """Bound a diagram-generation coroutine to request_timeout_seconds.

    A stuck/retrying LLM call can otherwise stall a request indefinitely — this
    cancels the *awaiting coroutine* on timeout so the user gets a clean 504
    instead of an infinite wait. It still does not kill an already-running
    worker thread (Python threads can't be forcibly killed), but that orphan no
    longer escapes the concurrency cap: llm/concurrency.py runs LLM work on a
    bounded private pool, so the orphan holds its slot until it finishes and a
    call cancelled before dispatch is withdrawn without reaching the provider.
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


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/static/index.html")


@router.get("/providers")
async def list_providers() -> dict:
    return {
        "providers": [
            {
                "key": spec.key,
                "label": spec.label or spec.key,
                "models": list(spec.models),
            }
            for spec in providers_module.PROVIDERS.values()
        ],
        "default": providers_module.default_provider_key(),
    }


@router.post("/providers/validate")
async def validate_custom_provider(body: CustomProviderConfig) -> dict:
    """"Test connection" for a custom (BYOK) provider — never persisted, just probed."""
    import time

    try:
        spec = providers_module._spec_from_custom_payload(body.model_dump())
        providers_module.require_api_key(spec)
        client = providers_module.build_langchain_client(spec, timeout=15)
        start = time.monotonic()
        await asyncio.to_thread(client.invoke, "Reply with exactly: OK")
        latency_ms = int((time.monotonic() - start) * 1000)
        return {"valid": True, "latency_ms": latency_ms}
    except providers_module.MissingApiKeyError as exc:
        return {"valid": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as a validation result, not a 500
        return {"valid": False, "error": str(exc)[:300]}


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
            return await _with_request_timeout(run_simple_pipeline(
                user_prompt=body.user_prompt, provider=body.provider, logger=logger,
                custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            ))
        return await _with_request_timeout(run_xml_pipeline(
            user_prompt=body.user_prompt, messages=None,
            provider=body.provider, platform=body.platform,
            logger=logger,
            custom_system_message=_sanitize_custom_msg(body.custom_system_message),
            diagram_renderer=body.diagram_renderer,
            three_d=body.three_d or wants_three_d(body.user_prompt),
            cost_estimate=body.cost_estimate,
            well_architected=body.well_architected,
            compliance=body.compliance,
            governance=body.governance,
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


#: One follow-up, never a third — a user who has answered twice has earned
#: their diagram (see config/xml_gen/interview.txt).
_MAX_INTERVIEW_ROUNDS = 2


def _interview_rounds(answers: dict[str, str] | None) -> int:
    """How many rounds have already been answered.

    Counted from the answers themselves rather than held server-side: the chat
    endpoint is stateless per request, and a session store for a two-round form
    would be the only stateful thing in the request path.
    """
    if not answers:
        return 0
    raw = str(answers.get("_rounds", "")).strip()
    if raw.isdigit():
        return int(raw)
    # Answers present without a marker: the first round was answered by a
    # client that predates the marker.
    return 1


async def _run_chat(body: ChatRequest, logger: StepLogger, *, on_progress=None) -> dict[str, Any]:
    platform = (body.platform or "aws").strip().lower()
    # A custom_provider payload always wins over `provider` — the pipeline routes
    # every LLM call in this request through it via the context manager below.
    provider = (
        providers_module.CUSTOM_PROVIDER_KEY if body.custom_provider
        else (body.provider or "claude").strip().lower()
    )
    custom_payload = body.custom_provider.model_dump() if body.custom_provider else None

    with providers_module.using_custom_provider(custom_payload), \
            providers_module.using_model_override(body.model):
        if not body.messages:
            raise ValueError("messages must not be empty")
        if body.messages[-1].role != "user":
            raise ValueError("Last message must be from the user")

        messages = [m.model_dump() for m in body.messages]
        last_user = messages[-1]["content"] if messages else ""
        applied_suggestions = body.applied_suggestions or []

        intent = classify_intent(
            last_user, diagram_mode=body.diagram_mode, platform=platform,
            current_diagram_type=body.current_diagram_type or "",
        )
        three_d = body.three_d or wants_three_d(last_user)
        has_attachments = bool(body.attachment_documents) or bool(body.context_text)
        if intent == "simple" and has_attachments:
            # The "simple" path never forwards attachments to the LLM — only
            # run_xml_pipeline does, so an attached file must not be routed away from it.
            intent = "cloud"
        if intent == "simple" and three_d:
            # 3D/isometric rendering only exists on the cloud/XML pipeline
            # (prompt_3d.txt) — the simple path has no concept of it.
            intent = "cloud"
        if intent == "simple" and (body.cost_estimate or body.well_architected or body.compliance):
            # Cost/Well-Architected/Compliance are cloud-only insights — run_simple_pipeline
            # never populates them. Routing to the simple path here would silently return
            # a response with none of the requested data (worse: on the very first
            # message, before any current_xml exists for a later surgical edit to fix),
            # making the toggle look broken/reset on the frontend for no reason.
            intent = "cloud"
        if (
            intent == "simple"
            and body.current_xml
            and len(body.current_xml) > 200
            and body.current_diagram_type != "simple"
        ):
            # A stale "simple" diagram_mode setting must never derail an in-progress
            # cloud-diagram edit (e.g. applying a WAF/Compliance suggestion) onto the
            # simple path, which would replace the cloud architecture with an unrelated
            # flowchart. Scoped to a *cloud* canvas: when the diagram on screen is
            # itself a simple one, staying on the simple path is the whole point —
            # that path regenerates its IR (see agent._simple_revision_block), which
            # is the only way to edit a chart correctly, since a bar's height is
            # derived from its value rather than stored alongside it.
            intent = "cloud"
        if on_progress:
            await on_progress("intent", f"Classified as {intent}", {"intent": intent})

        # Architecture interview — only when setting is on and answers not yet provided.
        # Skip when surgically editing an existing diagram (current_xml present).
        # A second round is allowed exactly once — see interview.txt. `rounds`
        # is carried on the answers themselves so the count survives the
        # round-trip without a server session.
        interview_rounds = _interview_rounds(body.interview_answers)
        if (
            intent != "simple"
            and body.architecture_interview
            and interview_rounds < _MAX_INTERVIEW_ROUNDS
            and not (body.current_xml and len(body.current_xml) > 200)
        ):
            from app.core.services.xml_pipeline import plan_interview

            questions = await asyncio.to_thread(
                plan_interview,
                human_prompt=body.message or "",
                platform_key=(body.platform or "aws"),
                views=body.diagram_views or ["logical"],
                provider_key=provider,
                logger=logger,
                prior_answers=body.interview_answers or None,
            )
            # No questions is a real answer: the brief already says enough, or
            # the follow-up round found nothing new. Fall through and draw.
            if questions:
                lines = [
                    "Before I draw this, a few things that would change the diagram "
                    "(reply in one message — say 'all fine' to take every default):",
                    "",
                ]
                for i, q in enumerate(questions, start=1):
                    lines.append(f"{i}. {q['prompt']}")
                msg = "\n".join(lines)
                logger.log(
                    "interview", "Architecture interview required",
                    details={"questions": len(questions), "round": interview_rounds + 1},
                )
                return {
                    "status": "success",
                    "interview_required": True,
                    "interview_questions": questions,
                    # Echoed back so the next request can carry both the round
                    # count and the answers already given — without this a
                    # second round silently discarded the first round's answers.
                    "interview_answers_so_far": dict(body.interview_answers or {}),
                    "assistant_message": msg,
                    "xml": None,
                    "diagram_type": None,
                    "suggestions": [],
                    "logs": logger.to_list(),
                }

        if intent == "simple":
            if body.current_diagram_type == "simple" and body.current_xml:
                # Same pre-edit snapshot the cloud edit path takes, so a
                # regenerate that goes somewhere the user didn't want is still
                # one "restore previous version" away.
                await _snapshot_pre_edit(body)
            response = await _with_request_timeout(run_simple_pipeline(
                messages=messages, provider=provider, logger=logger,
                custom_system_message=_sanitize_custom_msg(body.custom_system_message),
                applied_suggestions=applied_suggestions,
                include_suggestions=body.suggestions,
                current_xml=body.current_xml if body.current_diagram_type == "simple" else None,
                on_progress=on_progress,
            ))
        else:
            await _snapshot_pre_edit(body)
            response = await _with_request_timeout(run_xml_pipeline(
                user_prompt=None, messages=messages, provider=provider, platform=platform,
                logger=logger,
                custom_system_message=_sanitize_custom_msg(body.custom_system_message),
                diagram_renderer=body.diagram_renderer,
                applied_suggestions=applied_suggestions,
                three_d=three_d,
                cost_estimate=body.cost_estimate,
                well_architected=body.well_architected,
                compliance=body.compliance,
                governance=body.governance,
                compliance_frameworks=body.compliance_frameworks,
                context_text=body.context_text,
                attachment_documents=[a.model_dump() for a in body.attachment_documents] if body.attachment_documents else None,
                current_xml=body.current_xml,
                libavoid=body.libavoid,
                image_replicate=body.image_replicate,
                image_mode=body.image_mode,
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

    Every path now stores draw.io XML in the ``xml`` column. ``meta.diagram_type``
    survives as a display label only (history badges it), no longer as a signal
    about which client-side loader to use — that distinction existed for the
    Mermaid path, which stored Mermaid source in the same column and needed a
    different import call. Rows written before that path was removed still carry
    ``diagram_type: "mermaid"`` and Mermaid text in ``xml``; they cannot be
    restored, and ``insights-ui.js`` says so rather than feeding the text to
    draw.io as if it were XML.
    """
    if not body.session_id or response.get("status") != "success":
        return
    payload = response.get("xml")
    if not payload:
        return
    # Snapshot the full transcript as of this turn (the request already carries
    # every prior message; the reply itself only exists in `response`) so a
    # session can later be reopened with its conversation intact, not just its
    # diagram — restoring a version today only restores the XML.
    transcript = [m.model_dump() for m in body.messages]
    transcript.append({"role": "assistant", "content": response.get("assistant_message") or ""})
    await asyncio.to_thread(
        save_version,
        session_id=body.session_id,
        xml=payload,
        prompt=prompt,
        title=(response.get("assistant_message") or "")[:120],
        platform=(body.platform or "").lower(),
        provider=(body.provider or "").lower(),
        generation_path=response.get("generation_path") or "",
        renderer_used=response.get("renderer_used") or "",
        meta={
            "diagram_views": body.diagram_views or [],
            "llm_usage": response.get("llm_usage"),
            "diagram_type": response.get("diagram_type") or "cloud",
        },
        messages=transcript,
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


@router.post("/analyze")
async def analyze_diagram(body: AnalyzeRequest, request: Request):
    """Re-run cost/Well-Architected/compliance/suggestions against an existing diagram.

    Split out from ``/chat`` so turning any of these toggles on after a
    diagram already exists shows results immediately instead of only taking
    effect on the *next* chat turn.
    """
    logger = _logger_from_request(request)
    xml = (body.xml or "").strip()
    if not xml:
        return JSONResponse(status_code=400, content={"status": "error", "detail": "xml is required"})

    cost_data = None
    waf_data = None
    compliance_data = None
    suggestions_data = None
    try:
        if body.cost_estimate:
            cost_data = await asyncio.to_thread(estimate_monthly_cost, xml, body.platform)
        if body.well_architected:
            waf_data = await asyncio.to_thread(assess_well_architected, xml, body.platform)
        if body.compliance:
            compliance_data = await asyncio.to_thread(
                assess_compliance,
                xml=xml,
                platform=body.platform,
                frameworks=body.compliance_frameworks,
                provider=body.provider,
                logger=logger,
            )
        if body.suggestions:
            suggestions_data = await asyncio.to_thread(
                generate_followup_suggestions_for_xml,
                xml,
                view=body.view,
                user_request=body.human_prompt,
                provider=body.provider,
                applied_suggestions=body.applied_suggestions,
                logger=logger,
            )
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "detail": str(exc), "logs": logger.to_list()},
        )

    return {
        "status": "success",
        "cost_estimate": cost_data,
        "well_architected": waf_data,
        "compliance": compliance_data,
        "suggestions": suggestions_data,
        "logs": logger.to_list(),
    }


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


@router.get("/templates")
async def templates_list(q: str = ""):
    templates = await asyncio.to_thread(list_templates, q)
    return {"templates": [t.to_dict() for t in templates]}


@router.post("/templates")
async def templates_create(body: TemplateCreate):
    template = await asyncio.to_thread(
        create_template, title=body.title, description=body.description, prompt=body.prompt
    )
    if template is None:
        raise HTTPException(status_code=400, detail="Could not save template")
    return template.to_dict()


@router.patch("/templates/{template_id}")
async def templates_update(template_id: str, body: TemplateUpdate):
    template = await asyncio.to_thread(
        update_template,
        template_id,
        title=body.title,
        description=body.description,
        prompt=body.prompt,
        pinned=body.pinned,
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template.to_dict()


@router.delete("/templates/{template_id}")
async def templates_delete(template_id: str):
    deleted = await asyncio.to_thread(delete_template, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"id": template_id, "deleted": True}


@router.post("/templates/{template_id}/use")
async def templates_use(template_id: str):
    template = await asyncio.to_thread(bump_run_count, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template.to_dict()


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
            three_d=body.three_d or wants_three_d(body.user_prompt),
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
        if data.get("kind") == "image":
            logger.log("parse-url", f"Fetched image: {data['filename']}", level="success")
        else:
            logger.log("parse-url", f"Extracted {data['char_count']} chars", level="success")
        return {"status": "success", **data, "logs": logger.to_list()}
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(status_code=400, content={"status": "error", "detail": str(exc), "logs": logger.to_list()})


@router.post("/diagram/validate-visual")
async def validate_visual(body: ValidateVisualRequest, request: Request):
    """Real screenshot-based vision QA — opt-in, client-triggered after a diagram
    loads. Distinct from the always-on ``DIAGRAM_VLM_QA`` geometry linter.

    Same provider/model/BYOK resolution as ``_run_chat`` — the Validation
    Provider picker offers the exact same catalog as the chat composer's, so
    it must be able to route to any of those choices, not just a plain
    server-provider key.
    """
    logger = _logger_from_request(request)
    provider = (
        providers_module.CUSTOM_PROVIDER_KEY if body.custom_provider
        else (body.provider or default_provider_key()).strip().lower()
    )
    custom_payload = body.custom_provider.model_dump() if body.custom_provider else None
    try:
        with providers_module.using_custom_provider(custom_payload), \
                providers_module.using_model_override(body.model):
            result = await asyncio.to_thread(run_visual_qa_llm, body.image_base64, body.xml, provider, logger)
        return {"status": "success", **result.model_dump(), "logs": logger.to_list()}
    except Exception as exc:
        logger.log("error", str(exc), level="error")
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(exc), "logs": logger.to_list()})
