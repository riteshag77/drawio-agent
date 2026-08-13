"""Startup warnings and orderly shutdown.

Both behaviours are invisible in normal use and only matter at the edges — a
misconfigured deploy, or a stop mid-request — so they are asserted here rather
than trusted.
"""

from __future__ import annotations

import os
import time
import unittest

from app.adapters.outbound.config import env_config
from app.adapters.outbound.llm import concurrency


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


class LlmPoolShutdownTests(unittest.TestCase):
    def tearDown(self) -> None:
        concurrency.shutdown_llm_pool(wait=False)

    def test_shutdown_clears_the_pool(self) -> None:
        pool = concurrency._get_pool()
        self.assertIsNotNone(pool)
        concurrency.shutdown_llm_pool(wait=True)
        self.assertIsNone(concurrency._llm_pool)

    def test_pool_rebuilds_after_shutdown(self) -> None:
        """A late call must not submit to a dead executor."""
        first = concurrency._get_pool()
        concurrency.shutdown_llm_pool(wait=True)
        second = concurrency._get_pool()
        self.assertIsNot(first, second)

    def test_shutdown_is_safe_when_no_pool_exists(self) -> None:
        concurrency.shutdown_llm_pool(wait=True)
        concurrency.shutdown_llm_pool(wait=True)  # must not raise

    def test_in_flight_work_completes_when_waiting(self) -> None:
        """wait=True must let a call that is already running finish.

        Submitted straight to the pool rather than through run_llm_sync: the
        point is that shutdown blocks on work nobody is awaiting, which is
        exactly the case a server stop creates.
        """
        done = []

        def slow() -> None:
            time.sleep(0.2)
            done.append(1)

        concurrency._get_pool().submit(slow)
        self.assertEqual(done, [], "probe should still be running before shutdown")

        concurrency.shutdown_llm_pool(wait=True)
        self.assertEqual(done, [1], "wait=True must let an in-flight call finish")


class ExposedDefaultsWarningTests(unittest.TestCase):
    """The warning fires only when the app is actually reachable off-host."""

    def _warn(self):
        from app.main import _warn_on_exposed_defaults
        return _warn_on_exposed_defaults

    def test_loopback_is_silent(self) -> None:
        with _Env(HOST="127.0.0.1", CORS_ORIGINS="*", API_KEY="", RATE_LIMIT_PER_MINUTE="0"):
            with self.assertNoLogs("drawio-agent", level="WARNING"):
                self._warn()()

    def test_public_bind_with_defaults_warns(self) -> None:
        with _Env(HOST="0.0.0.0", CORS_ORIGINS="*", API_KEY="", RATE_LIMIT_PER_MINUTE="0"):
            with self.assertLogs("drawio-agent", level="WARNING") as captured:
                self._warn()()
        joined = " ".join(captured.output)
        self.assertIn("CORS allows any origin", joined)
        self.assertIn("no API_KEY set", joined)
        self.assertIn("rate limiting disabled", joined)

    def test_well_known_admin_password_is_called_out(self) -> None:
        with _Env(HOST="0.0.0.0", ADMIN_PASSWORD="admin123", API_KEY="k",
                  CORS_ORIGINS="https://example.com", RATE_LIMIT_PER_MINUTE="60"):
            with self.assertLogs("drawio-agent", level="WARNING") as captured:
                self._warn()()
        self.assertIn("ADMIN_PASSWORD is a well-known default", " ".join(captured.output))

    def test_hardened_public_config_is_silent(self) -> None:
        with _Env(HOST="0.0.0.0", CORS_ORIGINS="https://example.com", API_KEY="secret",
                  RATE_LIMIT_PER_MINUTE="60", ADMIN_PASSWORD="a-real-password"):
            with self.assertNoLogs("drawio-agent", level="WARNING"):
                self._warn()()
