"""Application entry point: builds and configures the FastAPI app."""

from __future__ import annotations

import app.core.ssl_trust  # noqa: F401 — trust the OS CA store before any HTTPS clients

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import APP_TITLE, STATIC_DIR


def create_app() -> FastAPI:
    """Construct the FastAPI application with routes and static assets mounted."""
    application = FastAPI(title=APP_TITLE)
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(router)
    return application


app = create_app()
