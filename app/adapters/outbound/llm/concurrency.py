"""Process-wide LLM concurrency gate for blocking provider calls.

The gate is a **dedicated, bounded thread pool**, not a semaphore around
``asyncio.to_thread``. That distinction is the whole point of this module, and
it is worth spelling out because the obvious implementation is subtly wrong.

An ``asyncio.Semaphore`` guards the *coroutine*, not the *thread*. Python
cannot kill a running thread, so when a request times out
(``routes._with_request_timeout``) the coroutine is cancelled, ``async with``
releases the permit — and the provider call keeps running, uncounted. The next
caller takes the freed permit and starts a second real call. Under repeated
timeouts the cap is not a cap at all: measured at ``MAX_CONCURRENT_LLM=2``,
four calls ran simultaneously. Those orphans keep burning provider quota and,
because ``to_thread`` uses the shared default executor, they crowd out the ~40
store/document calls that also run there.

Bounding a private pool instead fixes both. A worker stays occupied until the
call genuinely finishes, so the cap holds under cancellation; work beyond the
cap queues (backpressure) rather than escaping it; and LLM calls can no longer
starve the default executor, because they never touch it.

Cancelling before the work is dequeued also cancels it outright — a request
that times out while queued never reaches the provider at all, which the
``to_thread`` design could not express.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import TypeVar

from app.adapters.outbound.config.env_config import get_config

logger = logging.getLogger("drawio-agent")

T = TypeVar("T")

_llm_pool: ThreadPoolExecutor | None = None
_llm_pool_limit: int | None = None
_pool_lock = Lock()


def _get_pool() -> ThreadPoolExecutor:
    """The LLM pool, rebuilt if ``MAX_CONCURRENT_LLM`` changed since last call."""
    global _llm_pool, _llm_pool_limit
    limit = max(1, int(get_config().max_concurrent_llm))
    with _pool_lock:
        if _llm_pool is None or _llm_pool_limit != limit:
            old = _llm_pool
            _llm_pool = ThreadPoolExecutor(max_workers=limit, thread_name_prefix="llm")
            _llm_pool_limit = limit
            if old is not None:
                # Don't wait: in-flight calls (including orphans from a timed-out
                # request) finish on the old pool and it is collected after.
                old.shutdown(wait=False)
        return _llm_pool


async def run_llm_sync(fn, /, *args, **kwargs) -> T:
    """Run a sync LLM callable on the LLM pool, capped by MAX_CONCURRENT_LLM."""
    future = _get_pool().submit(fn, *args, **kwargs)
    try:
        return await asyncio.wrap_future(future)
    except asyncio.CancelledError:
        # Returns True only if the call had not started yet — in that case the
        # provider is never contacted. If it is already running we cannot stop
        # it, but it holds its worker slot until it finishes, so it stays
        # inside the cap instead of escaping it.
        if future.cancel():
            logger.debug("LLM call cancelled before dispatch — provider never contacted")
        raise
