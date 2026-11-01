"""Application entry point: builds and configures the FastAPI app."""

from __future__ import annotations

import logging
import os
import secrets
import time
import uuid

import app.core.ssl_trust  # noqa: F401 — trust the OS CA store before any HTTPS clients

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from contextlib import asynccontextmanager

from app.adapters.inbound.web.admin_routes import router as admin_router
from app.adapters.inbound.web.rate_limit import RateLimitMiddleware
from app.adapters.inbound.web.routes import router
from app.adapters.outbound.config.env_config import get_config
from app.adapters.outbound.diagram.elk_bridge import shutdown_elk_sidecar
from app.adapters.outbound.llm import providers as providers_module
from app.adapters.outbound.llm.concurrency import shutdown_llm_pool
from app.core.config import STATIC_DIR, init_config

# Initialize configuration before any other imports.
init_config()

cfg = get_config()

if cfg.drawio_style_cache_reset:
    from app.adapters.outbound.diagram.style_cache import clear_style_cache

    clear_style_cache()

_default_provider_key = providers_module.default_provider_key()
_default_provider_spec = providers_module.get_provider(_default_provider_key)
if _default_provider_spec.api_key_env and not os.environ.get(_default_provider_spec.api_key_env):
    logging.getLogger("drawio-agent").warning(
        "Default provider is '%s' but %s is not set — requests that don't specify "
        "a provider will fail. Set %s in .env, or pass a different provider per request.",
        _default_provider_key,
        _default_provider_spec.api_key_env,
        _default_provider_spec.api_key_env,
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


def _warn_on_exposed_defaults() -> None:
    """Warn when the app is reachable off-host with development defaults.

    Every one of these is the right default for local work and the wrong one
    for anything reachable: CORS ``*``, no API key, no rate limit, and the
    stock admin password. The mechanisms all exist — they are simply off — so
    the failure mode is silent rather than loud. Say it once at startup.
    """
    config = get_config()
    host = (config.host or "").strip()
    if host in ("127.0.0.1", "localhost", ""):
        return  # loopback only: development defaults are correct here

    exposed = []
    if "*" in config.cors_origins:
        exposed.append("CORS allows any origin")
    if not config.api_key:
        exposed.append("no API_KEY set (endpoints are unauthenticated)")
    if config.rate_limit_per_minute == 0:
        exposed.append("rate limiting disabled")
    if config.admin_password in ("admin", "admin123", "password", "changeme"):
        exposed.append("ADMIN_PASSWORD is a well-known default")

    if exposed:
        logging.getLogger("drawio-agent").warning(
            "Bound to %s with development defaults — %s. "
            "See docs/ENV_AND_MCP.md for the production checklist.",
            host,
            "; ".join(exposed),
        )


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Start-up warnings, and an orderly drain of the LLM pool on shutdown."""
    _warn_on_exposed_defaults()
    yield
    # LLM calls run on a private bounded pool (llm/concurrency.py). Without
    # this, a stop mid-request abandons in-flight provider calls instead of
    # letting them finish; wait=True lets the current calls complete while the
    # server has already stopped accepting new work.
    shutdown_llm_pool(wait=True)
    # Then the ELK sidecar, after the pool — layouts run on pool threads, so
    # stopping it first would strand in-flight work on the slow fallback path.
    shutdown_elk_sidecar()


def create_app() -> FastAPI:
    """Construct the FastAPI application with routes and static assets mounted."""
    application = FastAPI(title=cfg.app_title, lifespan=_lifespan)

    origins = cfg.cors_origins
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(BodySizeLimitMiddleware)
    # Rate limit sits inside the API-key gate so an unauthorized flood is
    # rejected on the cheaper check first and never consumes a client's quota.
    application.add_middleware(RateLimitMiddleware)
    application.add_middleware(ApiKeyMiddleware)

    @application.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        request.state.request_id = request_id
        t0 = time.monotonic()
        response = await call_next(request)
        duration_ms = round((time.monotonic() - t0) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)
        path = request.url.path
        if path in ("/", "/static/index.html"):
            # index.html itself carries no cache-busting query string (unlike
            # its ?v=-tagged <script>/<link> tags) — without this, a browser
            # can keep serving a stale shell (missing new UI entirely) even
            # after every asset it references has been updated server-side.
            response.headers["Cache-Control"] = "no-cache"
        if not path.endswith((".css", ".js")):
            from app.core.logging import get_logger
            get_logger().info(
                "request method=%s path=%s status=%s duration_ms=%s req_id=%s",
                request.method,
                path,
                response.status_code,
                duration_ms,
                request_id,
            )
        return response

    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(router)
    application.include_router(admin_router)
    return application


app = create_app()
