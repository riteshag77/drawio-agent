"""Application entry point: builds and configures the FastAPI app."""

from __future__ import annotations

import uuid

import app.core.ssl_trust  # noqa: F401 — trust the OS CA store before any HTTPS clients

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.adapters.inbound.web.routes import router
from app.adapters.outbound.config.env_config import get_config
from app.core.config import APP_TITLE, STATIC_DIR, init_config, _resolve_config

# Initialize configuration before any other imports.
init_config()
_resolve_config()

cfg = get_config()

logger = None  # lazy import to avoid circular dependency


def create_app() -> FastAPI:
    """Construct the FastAPI application with routes and static assets mounted."""
    application = FastAPI(title=cfg.app_title)

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
