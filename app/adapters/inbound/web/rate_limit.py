"""Per-client rate limiting for diagram-generation endpoints.

Separate from the ``MAX_CONCURRENT_LLM`` semaphore, which caps *total* in-flight
LLM calls process-wide. That protects the provider, but it is not a fairness
mechanism: one client looping requests can occupy every slot and starve
everyone else. This caps how many generations any single client may start per
minute.

A sliding-window counter in memory: no Redis, no new dependency. That is the
right trade for a single-process deployment; behind multiple workers each
process enforces its own share of the limit, which is documented rather than
hidden. Disabled by default (``RATE_LIMIT_PER_MINUTE=0``).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.adapters.outbound.config.env_config import get_config

_WINDOW_SECONDS = 60.0

#: Only generation endpoints are limited. Health/readiness probes, static assets
#: and history reads are cheap and must stay available even under throttling.
_LIMITED_PATHS = ("/chat", "/chat/stream", "/generate", "/download", "/analyze")

_hits: dict[str, deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _client_key(request: Request) -> str:
    """Identify the caller: API key if present, else client IP.

    Preferring the API key means several users behind one NAT are not lumped
    together, while an anonymous deployment still gets per-IP protection.
    ``X-Forwarded-For`` is honoured only when the app is explicitly told it is
    behind a proxy — trusting it unconditionally would let any client forge an
    identity and bypass the limit entirely.
    """
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return f"key:{api_key[:16]}"
    if get_config().get_bool("TRUST_PROXY_HEADERS", False):
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
    client = request.client
    return f"ip:{client.host if client else 'unknown'}"


def check_and_record(key: str, limit: int, *, now: float | None = None) -> tuple[bool, int]:
    """Record a hit for ``key``. Returns ``(allowed, retry_after_seconds)``."""
    now = time.monotonic() if now is None else now
    cutoff = now - _WINDOW_SECONDS
    with _lock:
        bucket = _hits[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit:
            retry_after = max(1, int(bucket[0] + _WINDOW_SECONDS - now) + 1)
            return False, retry_after
        bucket.append(now)
        # Opportunistic cleanup so idle clients don't accumulate forever.
        if len(_hits) > 4096:
            for stale_key in [k for k, v in _hits.items() if not v or v[-1] < cutoff]:
                del _hits[stale_key]
        return True, 0


def reset() -> None:
    """Test helper — clear all recorded hits."""
    with _lock:
        _hits.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        limit = get_config().rate_limit_per_minute
        if limit <= 0 or not request.url.path.startswith(_LIMITED_PATHS):
            return await call_next(request)

        allowed, retry_after = check_and_record(_client_key(request), limit)
        if not allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry_after)},
                content={
                    "status": "error",
                    "detail": (
                        f"Rate limit reached ({limit} diagram requests per minute). "
                        f"Try again in {retry_after}s."
                    ),
                },
            )
        return await call_next(request)
