"""Concurrency, streaming, timeout, rate-limit and provider-failover behaviour.

These were the untested failure modes: everything the app does under load,
disconnection, or partial provider outage. Unit tests of pure functions cannot
reach them, so they are exercised here through the real semaphore, the real SSE
generator, and the real middleware stack.
"""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest.mock import patch

from langchain_core.exceptions import OutputParserException

from app.adapters.inbound.web import rate_limit
from app.adapters.outbound.config import env_config
from app.adapters.outbound.llm import concurrency
from app.adapters.outbound.llm.fallback import call_llm_with_fallback, resolve_chain, should_failover
from app.adapters.outbound.llm.providers import MissingApiKeyError
from app.core.logging import StepLogger


class _Env:
    """Temporarily set env vars and reset the config singleton."""

    def __init__(self, **values: str) -> None:
        self.values = values
        self._saved: dict[str, str | None] = {}

    def __enter__(self):
        for key, value in self.values.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        env_config._config_instance = None
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        env_config._config_instance = None
        return False


# ── Concurrency ────────────────────────────────────────────────────────


class LlmConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        concurrency._llm_semaphore = None
        concurrency._llm_sem_limit = None

    tearDown = setUp

    def test_semaphore_caps_simultaneous_calls(self) -> None:
        """No more than MAX_CONCURRENT_LLM calls may be in flight at once."""
        peak = 0
        active = 0
        lock = asyncio.Lock()

        def blocking_call() -> str:
            nonlocal peak, active
            active += 1
            peak = max(peak, active)
            time.sleep(0.02)
            active -= 1
            return "ok"

        async def main():
            with _Env(MAX_CONCURRENT_LLM="3"):
                await asyncio.gather(*(concurrency.run_llm_sync(blocking_call) for _ in range(12)))

        asyncio.run(main())
        self.assertLessEqual(peak, 3, f"peak concurrency {peak} exceeded the configured cap of 3")
        self.assertGreater(peak, 1, "calls were serialized — the semaphore is over-restricting")

    def test_all_queued_calls_still_complete(self) -> None:
        async def main():
            with _Env(MAX_CONCURRENT_LLM="2"):
                return await asyncio.gather(*(concurrency.run_llm_sync(lambda i=i: i) for i in range(20)))

        self.assertEqual(sorted(asyncio.run(main())), list(range(20)))

    def test_one_failing_call_does_not_leak_a_permit(self) -> None:
        """A raising call must release its slot, or the pool drains to a deadlock."""
        def boom():
            raise RuntimeError("provider exploded")

        async def main():
            with _Env(MAX_CONCURRENT_LLM="2"):
                for _ in range(6):
                    with self.assertRaises(RuntimeError):
                        await concurrency.run_llm_sync(boom)
                # Pool must still be usable afterwards.
                return await asyncio.wait_for(concurrency.run_llm_sync(lambda: "alive"), timeout=2)

        self.assertEqual(asyncio.run(main()), "alive")

    def test_semaphore_rebuilds_when_limit_changes(self) -> None:
        async def main():
            with _Env(MAX_CONCURRENT_LLM="2"):
                await concurrency.run_llm_sync(lambda: None)
                first = concurrency._llm_sem_limit
            with _Env(MAX_CONCURRENT_LLM="5"):
                await concurrency.run_llm_sync(lambda: None)
                return first, concurrency._llm_sem_limit

        first, second = asyncio.run(main())
        self.assertEqual((first, second), (2, 5))


# ── SSE streaming ──────────────────────────────────────────────────────


class SseStreamTests(unittest.TestCase):
    """Exercises routes.chat_diagram_stream's generator directly.

    Uses the real event generator rather than an HTTP client so a client
    disconnect can be simulated deterministically instead of raced.
    """

    def _request(self, disconnect_after: int = 10_000):
        calls = {"n": 0}

        class FakeRequest:
            state = type("S", (), {"request_id": "test"})()

            async def is_disconnected(_self) -> bool:
                calls["n"] += 1
                return calls["n"] > disconnect_after

        return FakeRequest(), calls

    def _body(self):
        from app.adapters.inbound.web.schemas import ChatRequest

        return ChatRequest(messages=[{"role": "user", "content": "draw a vpc"}])

    def test_progress_events_then_result(self) -> None:
        from app.adapters.inbound.web import routes

        async def fake_run_chat(body, logger, *, on_progress=None):
            await on_progress("llm", "Generating…", {})
            await on_progress("validate", "Validating…", {})
            return {"status": "success", "xml": "<mxfile/>", "logs": []}

        async def main():
            request, _ = self._request()
            with patch.object(routes, "_run_chat", fake_run_chat):
                response = await routes.chat_diagram_stream(self._body(), request)
                return [chunk async for chunk in response.body_iterator]

        chunks = asyncio.run(main())
        joined = "".join(chunks)
        self.assertIn('"type": "progress"', joined)
        self.assertIn('"type": "result"', joined)
        self.assertIn("Generating", joined)

    def test_pipeline_error_is_delivered_as_an_error_event(self) -> None:
        from app.adapters.inbound.web import routes

        async def failing(body, logger, *, on_progress=None):
            raise RuntimeError("pipeline blew up")

        async def main():
            request, _ = self._request()
            with patch.object(routes, "_run_chat", failing):
                response = await routes.chat_diagram_stream(self._body(), request)
                return "".join([c async for c in response.body_iterator])

        out = asyncio.run(main())
        self.assertIn('"type": "error"', out)
        self.assertIn("pipeline blew up", out)

    def test_timeout_error_is_flagged_for_the_client(self) -> None:
        from app.adapters.inbound.web import routes

        async def timing_out(body, logger, *, on_progress=None):
            raise routes.PipelineTimeoutError("took too long")

        async def main():
            request, _ = self._request()
            with patch.object(routes, "_run_chat", timing_out):
                response = await routes.chat_diagram_stream(self._body(), request)
                return "".join([c async for c in response.body_iterator])

        out = asyncio.run(main())
        self.assertIn('"timeout": true', out)

    def test_client_disconnect_cancels_the_in_flight_pipeline(self) -> None:
        """An abandoned request must not keep burning LLM budget in the background."""
        from app.adapters.inbound.web import routes

        cancelled = {"yes": False}

        async def long_running(body, logger, *, on_progress=None):
            # Emit once so the generator yields and the task is genuinely
            # mid-flight when the disconnect is noticed.
            await on_progress("llm", "Generating…", {})
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled["yes"] = True
                raise
            return {"status": "success", "xml": "<mxfile/>"}

        async def main():
            request, _ = self._request(disconnect_after=1)
            with patch.object(routes, "_run_chat", long_running):
                response = await routes.chat_diagram_stream(self._body(), request)
                return [c async for c in response.body_iterator]

        chunks = asyncio.run(main())
        joined = "".join(chunks)
        self.assertIn('"type": "progress"', joined)
        self.assertNotIn('"type": "result"', joined, "streamed a result after the client left")
        self.assertTrue(cancelled["yes"], "in-flight pipeline task was not cancelled on disconnect")

    def test_disconnect_before_the_task_starts_does_not_hang(self) -> None:
        """Disconnecting on the very first poll must close the stream cleanly."""
        from app.adapters.inbound.web import routes

        async def never_runs(body, logger, *, on_progress=None):
            await asyncio.sleep(30)
            return {"status": "success"}

        async def main():
            request, _ = self._request(disconnect_after=0)
            with patch.object(routes, "_run_chat", never_runs):
                response = await routes.chat_diagram_stream(self._body(), request)
                return [c async for c in response.body_iterator]

        chunks = asyncio.run(asyncio.wait_for(main(), timeout=5))
        self.assertEqual(chunks, [], "no events should be emitted after an immediate disconnect")


# ── Rate limiting ──────────────────────────────────────────────────────


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        rate_limit.reset()

    tearDown = setUp

    def test_allows_up_to_the_limit_then_rejects(self) -> None:
        for i in range(5):
            allowed, _ = rate_limit.check_and_record("client-a", 5)
            self.assertTrue(allowed, f"request {i + 1} should have been allowed")
        allowed, retry_after = rate_limit.check_and_record("client-a", 5)
        self.assertFalse(allowed)
        self.assertGreater(retry_after, 0)

    def test_clients_are_isolated_from_each_other(self) -> None:
        for _ in range(5):
            rate_limit.check_and_record("noisy", 5)
        self.assertFalse(rate_limit.check_and_record("noisy", 5)[0])
        self.assertTrue(rate_limit.check_and_record("quiet", 5)[0])

    def test_window_slides_so_old_hits_expire(self) -> None:
        now = 1000.0
        for _ in range(3):
            rate_limit.check_and_record("c", 3, now=now)
        self.assertFalse(rate_limit.check_and_record("c", 3, now=now + 1)[0])
        # Past the 60s window, the bucket has drained.
        self.assertTrue(rate_limit.check_and_record("c", 3, now=now + 61)[0])

    def test_middleware_is_inert_when_limit_is_zero(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(rate_limit.RateLimitMiddleware)

        @app.post("/chat")
        async def chat():
            return {"ok": True}

        with _Env(RATE_LIMIT_PER_MINUTE="0"):
            client = TestClient(app)
            for _ in range(30):
                self.assertEqual(client.post("/chat").status_code, 200)

    def test_middleware_returns_429_with_retry_after(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.add_middleware(rate_limit.RateLimitMiddleware)

        @app.post("/chat")
        async def chat():
            return {"ok": True}

        @app.get("/health")
        async def health():
            return {"ok": True}

        with _Env(RATE_LIMIT_PER_MINUTE="3"):
            client = TestClient(app)
            for _ in range(3):
                self.assertEqual(client.post("/chat").status_code, 200)
            blocked = client.post("/chat")
            self.assertEqual(blocked.status_code, 429)
            self.assertIn("Retry-After", blocked.headers)
            # Health probes must stay reachable while generation is throttled.
            self.assertEqual(client.get("/health").status_code, 200)


# ── Provider failover ──────────────────────────────────────────────────


class ProviderFailoverTests(unittest.TestCase):
    def test_chain_is_just_the_primary_by_default(self) -> None:
        with _Env(PROVIDER_FALLBACK_ORDER=""):
            self.assertEqual(resolve_chain("claude"), ["claude"])

    def test_chain_includes_configured_fallbacks(self) -> None:
        with _Env(PROVIDER_FALLBACK_ORDER="gemini,openai"):
            self.assertEqual(resolve_chain("claude"), ["claude", "gemini", "openai"])

    def test_chain_drops_duplicates_and_unknown_providers(self) -> None:
        with _Env(PROVIDER_FALLBACK_ORDER="claude,not_a_real_provider,gemini"):
            self.assertEqual(resolve_chain("claude"), ["claude", "gemini"])

    def test_schema_failures_do_not_trigger_failover(self) -> None:
        """Every provider would fail the same schema — retrying elsewhere is waste."""
        self.assertFalse(should_failover(OutputParserException("bad json")))

    def test_quota_and_transient_errors_trigger_failover(self) -> None:
        self.assertTrue(should_failover(ValueError("429 quota exceeded")))
        self.assertTrue(should_failover(RuntimeError("model is overloaded")))
        self.assertTrue(should_failover(RuntimeError("503 service unavailable")))
        self.assertTrue(should_failover(MissingApiKeyError("OPENAI_API_KEY is not set")))

    def test_falls_over_to_the_next_provider_on_quota_error(self) -> None:
        calls: list[str] = []

        def fake_call_llm(system, human, provider, output_type, logger=None, attachments=None):
            calls.append(provider)
            if provider == "claude":
                raise ValueError("429 quota exceeded")
            return "diagram-from-gemini"

        logger = StepLogger()
        with _Env(PROVIDER_FALLBACK_ORDER="gemini"), \
                patch("app.adapters.outbound.llm.fallback.call_llm", fake_call_llm):
            result = call_llm_with_fallback("sys", "human", "claude", str, logger, None)

        self.assertEqual(result, "diagram-from-gemini")
        self.assertEqual(calls, ["claude", "gemini"])
        self.assertTrue(any(e.get("step") == "llm_fallback" for e in logger.to_list()))

    def test_non_failover_error_propagates_immediately(self) -> None:
        calls: list[str] = []

        def fake_call_llm(system, human, provider, output_type, logger=None, attachments=None):
            calls.append(provider)
            raise OutputParserException("schema mismatch")

        with _Env(PROVIDER_FALLBACK_ORDER="gemini"), \
                patch("app.adapters.outbound.llm.fallback.call_llm", fake_call_llm):
            with self.assertRaises(OutputParserException):
                call_llm_with_fallback("sys", "human", "claude", str, None, None)

        self.assertEqual(calls, ["claude"], "should not have tried the fallback provider")

    def test_last_provider_error_propagates(self) -> None:
        def always_quota(system, human, provider, output_type, logger=None, attachments=None):
            raise ValueError("429 quota exceeded")

        with _Env(PROVIDER_FALLBACK_ORDER="gemini"), \
                patch("app.adapters.outbound.llm.fallback.call_llm", always_quota):
            with self.assertRaises(ValueError):
                call_llm_with_fallback("sys", "human", "claude", str, None, None)


if __name__ == "__main__":
    unittest.main()
