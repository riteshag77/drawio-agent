"""A3: ASGI middleware — pure, composable, no hand-rolled ordering issues.

Replaces mixed ASGI/BaseHTTPMiddleware with a pure ASGI middleware
that adds request-id and timing to all responses.
"""

from __future__ import annotations

import time
import uuid
from typing import Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestTracingMiddleware:
    """Pure ASGI middleware for request ID tracking and response timing.
    
    Adds X-Request-ID and X-Response-Time-Ms headers to every response.
    Uses request_id from X-Request-ID header or generates a new one.
    
    Should be the first middleware in the stack so all downstream code
    can access request_id via scope["request_id"].
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # Pass through non-HTTP (websocket, lifespan, etc.)
            await self.app(scope, receive, send)
            return

        # Extract or generate request ID
        headers_dict = {name.lower(): value for name, value in scope.get("headers", [])}
        request_id = headers_dict.get(b"x-request-id", b"").decode() or uuid.uuid4().hex[:12]

        # Store in scope for downstream access
        scope["request_id"] = request_id
        scope["request_start_time"] = time.monotonic()

        # Wrap send to add headers to response
        async def send_with_timing(message: Message) -> None:
            if message["type"] == "http.response.start":
                # Add timing and request ID to response headers
                headers = list(message.get("headers", []))
                elapsed_ms = round(
                    (time.monotonic() - scope["request_start_time"]) * 1000
                )
                headers.append((b"x-request-id", request_id.encode()))
                headers.append((b"x-response-time-ms", str(elapsed_ms).encode()))
                message["headers"] = headers

            await send(message)

        await self.app(scope, receive, send_with_timing)


class ContextVarsMiddleware:
    """ASGI middleware to set contextvars for logging correlation.
    
    Sets context variables (request_id, user_id, etc.) so they're available
    to all async code in the request's context.
    """

    def __init__(self, app: ASGIApp):
        self.app = app
        try:
            from contextvars import ContextVar
            self.request_id_var = ContextVar("request_id")
        except ImportError:
            self.request_id_var = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self.request_id_var:
            self.request_id_var.set(scope.get("request_id", "unknown"))

        await self.app(scope, receive, send)
