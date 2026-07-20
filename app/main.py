"""Application entry point: builds and configures the FastAPI app."""

from __future__ import annotations

import logging
import os
import secrets
import uuid

import app.core.ssl_trust  # noqa: F401 — trust the OS CA store before any HTTPS clients

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.adapters.inbound.web.routes import router
from app.adapters.outbound.config.env_config import get_config
from app.core.config import STATIC_DIR, init_config, _resolve_config

# Initialize configuration before any other imports.
init_config()
_resolve_config()

cfg = get_config()

if cfg.drawio_style_cache_reset:
    from app.adapters.outbound.diagram.style_cache import clear_style_cache

    clear_style_cache()

if cfg.provider_default == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
    logging.getLogger("drawio-agent").warning(
        "Default provider is 'claude' but ANTHROPIC_API_KEY is not set — "
        "requests that don't specify a provider will fail. Set ANTHROPIC_API_KEY "
        "in .env, or pass provider=\"openai\" (etc.) per request."
    )

logger = None  # lazy import to avoid circular dependency


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Optional API key gate when API_KEY is configured."""

    async def dispatch(self, request: Request, call_next):
        api_key = get_config().api_key
        if not api_key:
            return await call_next(request)
        path = request.url.path
        if path == "/" or path == "/health" or path.startswith("/static"):
            return await call_next(request)
        provided = request.headers.get("X-API-Key") or ""
        auth = request.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            provided = provided or auth[7:].strip()
        if not provided or not secrets.compare_digest(provided, api_key):
            return JSONResponse(status_code=401, content={"status": "error", "detail": "Unauthorized"})
        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/attachments/extract":
            from app.adapters.outbound.utils.documents import MAX_ATTACHMENT_BYTES

            # Multipart framing adds overhead beyond the raw file bytes; give it headroom.
            limit = MAX_ATTACHMENT_BYTES + 65_536
        else:
            limit = get_config().max_request_body_bytes
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > limit:
            return JSONResponse(
                status_code=413,
                content={"status": "error", "detail": f"Request body exceeds {limit} bytes"},
            )
        return await call_next(request)


def create_app() -> FastAPI:
    """Construct the FastAPI application with routes and static assets mounted."""
    application = FastAPI(title=cfg.app_title)

    origins = cfg.cors_origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(BodySizeLimitMiddleware)
    application.add_middleware(ApiKeyMiddleware)

    @application.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(router)
    return application


app = create_app()
