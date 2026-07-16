"""Process-wide LLM concurrency gate for blocking provider calls."""

from __future__ import annotations

import asyncio
from typing import TypeVar

from app.adapters.outbound.config.env_config import get_config

T = TypeVar("T")

_llm_semaphore: asyncio.Semaphore | None = None
_llm_sem_limit: int | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore, _llm_sem_limit
    limit = max(1, int(get_config().max_concurrent_llm))
    if _llm_semaphore is None or _llm_sem_limit != limit:
        _llm_semaphore = asyncio.Semaphore(limit)
        _llm_sem_limit = limit
    return _llm_semaphore


async def run_llm_sync(fn, /, *args, **kwargs) -> T:
    """Run a sync LLM callable in a thread, capped by MAX_CONCURRENT_LLM."""
    async with _get_semaphore():
        return await asyncio.to_thread(fn, *args, **kwargs)
