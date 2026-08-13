"""Per-stage timing on StepLogger — durations, the summary, and failure paths."""

from __future__ import annotations

import asyncio
import time
import unittest

from app.core.logging import StepLogger


class StepSpanTests(unittest.TestCase):
    def test_span_logs_a_duration(self) -> None:
        logger = StepLogger(request_id="req-1")
        with logger.span("llm_call", "claude → NetworkIR", provider="claude"):
            time.sleep(0.02)

        entry = logger.entries[-1]
        self.assertEqual(entry.step, "llm_call")
        self.assertGreaterEqual(entry.details["duration_ms"], 15)
        self.assertEqual(entry.details["provider"], "claude")
        self.assertEqual(entry.request_id, "req-1")

    def test_async_span_records_the_same_way(self) -> None:
        logger = StepLogger()

        async def main():
            async with logger.span("mcp", "Resolving styles"):
                await asyncio.sleep(0.02)

        asyncio.run(main())
        self.assertGreaterEqual(logger.entries[-1].details["duration_ms"], 15)

    def test_span_records_duration_and_reraises_on_failure(self) -> None:
        logger = StepLogger()
        with self.assertRaises(RuntimeError):
            with logger.span("llm_call", "claude → NetworkIR"):
                raise RuntimeError("provider exploded")

        entry = logger.entries[-1]
        self.assertEqual(entry.level, "warning")
        self.assertIn("provider exploded", entry.details["error"])
        self.assertIn("duration_ms", entry.details)

    def test_cancellation_is_not_reported_as_a_stage_failure(self) -> None:
        """A request timeout cancels mid-stage; that is a timeout, not a stage error."""
        logger = StepLogger()

        async def main():
            with logger.span("llm_call", "claude → NetworkIR"):
                raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(main())
        self.assertEqual(logger.entries[-1].level, "info")

    def test_repeated_steps_accumulate(self) -> None:
        logger = StepLogger()
        for _ in range(3):
            with logger.span("llm_call", "call"):
                time.sleep(0.01)

        summary = logger.timings_summary()
        self.assertEqual(summary["llm_call"]["calls"], 3)
        self.assertGreaterEqual(summary["llm_call"]["total_ms"], 25)

    def test_summary_is_ordered_slowest_first(self) -> None:
        logger = StepLogger()
        with logger.span("fast", "quick"):
            pass
        with logger.span("slow", "sluggish"):
            time.sleep(0.03)

        self.assertEqual(list(logger.timings_summary())[0], "slow")

    def test_summary_entry_is_emitted_once(self) -> None:
        logger = StepLogger()
        with logger.span("llm_call", "call"):
            pass

        logger.log_timings_summary()
        logger.log_timings_summary()

        summaries = [e for e in logger.entries if e.step == "timings"]
        self.assertEqual(len(summaries), 1)
        self.assertIn("llm_call", summaries[0].details["stages"])

    def test_summary_is_skipped_when_nothing_was_timed(self) -> None:
        logger = StepLogger()
        logger.log_timings_summary()
        self.assertEqual([e for e in logger.entries if e.step == "timings"], [])
