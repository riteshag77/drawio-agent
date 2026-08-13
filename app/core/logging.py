"""Collects structured step logs for the generation pipeline."""

from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.adapters.outbound.config.env_config import get_config


@dataclass
class LogEntry:
    step: str
    message: str
    level: str = "info"
    details: dict[str, Any] | None = None
    request_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step,
            "message": self.message,
            "level": self.level,
            "timestamp": self.timestamp,
        }
        if self.details:
            payload["details"] = self.details
        if self.request_id:
            payload["request_id"] = self.request_id
        return payload

    def to_logfmt(self) -> str:
        parts = [
            f"step={self.step}",
            f"level={self.level}",
            f'msg="{self.message}"',
            f"ts={self.timestamp}",
        ]
        if self.request_id:
            parts.append(f"req_id={self.request_id}")
        if self.details:
            parts.append(f"details={self.details}")
        return " ".join(parts)


_config = get_config()  # also guarantees .env is loaded before the raw env reads below
_LOG_FORMAT: str = _config.log_format  # text | json


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        return json.dumps(payload, ensure_ascii=False)


# Root logger for server-side output (separate from per-request StepLogger instances).
_LOGGER: logging.Logger = logging.getLogger("drawio-agent")
_LOGGER.setLevel(logging.DEBUG)

# The console handler encodes through the active code page, which on Windows is
# cp1252 — so a log message containing an em-dash or arrow (several already do)
# raises UnicodeEncodeError inside logging and the line is lost, replaced by a
# "--- Logging error ---" traceback. Widen stdout once here rather than
# restricting every message to ASCII. The file handler is already UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
except (AttributeError, ValueError):
    pass  # stdout replaced (pytest capture) or already detached — logging still works

_console = logging.StreamHandler(sys.stdout)
if _LOG_FORMAT == "json":
    _console.setFormatter(_JsonFormatter())
else:
    _console.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
_LOGGER.addHandler(_console)

_log_dir = _config.log_dir
try:
    os.makedirs(_log_dir, exist_ok=True)
    # One file per calendar day, named at process start (drawio-agent-
    # YYYY-MM-DD.log) — deliberately NOT TimedRotatingFileHandler(when=
    # "midnight"), which rotates by renaming the active file at the moment a
    # log record crosses midnight. That rename silently stopped working
    # here: file logging went dead mid-session with no rotated
    # ".2026-08-04"-suffixed file ever appearing, on Windows, under
    # run.py's reload=True (which restarts the worker process — and Windows
    # can refuse to rename a file another process, or even the same
    # process's own still-open handle, has open). A plain FileHandler on an
    # already-dated filename needs no rename at all, so there's nothing to
    # fail; the tradeoff is a run that stays up across midnight keeps
    # appending to the previous day's file instead of rolling over
    # mid-process, which is a far smaller cost than losing all logging.
    _log_date = datetime.now().strftime("%Y-%m-%d")
    _file = logging.FileHandler(
        os.path.join(_log_dir, f"drawio-agent-{_log_date}.log"),
        encoding="utf-8",
    )
    _file.setLevel(logging.DEBUG)
    _file.setFormatter(
        _JsonFormatter() if _LOG_FORMAT == "json" else logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    _LOGGER.addHandler(_file)

    # Retention: prune files older than the window instead of relying on
    # TimedRotatingFileHandler's backupCount (which pruned on its own
    # rollover — now replaced by this per-start sweep).
    _cutoff = datetime.now() - timedelta(days=_config.log_retention_days)
    for _old_path in glob.glob(os.path.join(_log_dir, "drawio-agent-*.log")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(_old_path)) < _cutoff:
                os.remove(_old_path)
        except OSError:
            pass  # best-effort cleanup only
except OSError:
    pass  # non-fatal: file logging unavailable


def get_logger() -> logging.Logger:
    return _LOGGER


class _Span:
    """Times a pipeline stage and logs one entry carrying ``duration_ms``.

    Supports both ``with`` and ``async with`` so the same call reads the same
    either side of the sync/async boundary in the pipeline.
    """

    def __init__(
        self,
        logger: "StepLogger",
        step: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._logger = logger
        self._step = step
        self._message = message
        self._details = details or {}
        self._t0 = 0.0

    def __enter__(self) -> "_Span":
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        duration_ms = round((time.monotonic() - self._t0) * 1000)
        self._logger._record_timing(self._step, duration_ms)
        details = {**self._details, "duration_ms": duration_ms}
        if exc_type is None:
            self._logger.log(self._step, f"{self._message} ({duration_ms} ms)", details=details)
        else:
            # Cancellation is a normal timeout outcome, not a stage failure.
            level = "info" if exc_type is asyncio.CancelledError else "warning"
            details["error"] = str(exc_val)
            self._logger.log(
                self._step,
                f"{self._message} failed after {duration_ms} ms: {exc_val}",
                level=level,
                details=details,
            )
        return False  # never suppress

    async def __aenter__(self) -> "_Span":
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        return self.__exit__(exc_type, exc_val, exc_tb)


class StepLogger:
    """Append-only logger for pipeline steps returned to the UI."""

    def __init__(self, request_id: str | None = None) -> None:
        self.entries: list[LogEntry] = []
        self.request_id: str | None = request_id
        #: step -> [call count, cumulative ms]. Populated by ``span``.
        self.timings: dict[str, list[int]] = {}
        self._timings_logged = False

    def span(self, step: str, message: str, **details: Any) -> _Span:
        """Time a stage: ``with logger.span("llm", "Generating IR"): ...``.

        Entries already carry timestamps, but a caller reading a 90-second
        generation could only recover per-stage cost by diffing them by hand —
        and not at all for stages that overlap in the enrichment gather. This
        records the duration explicitly, on the entry and in the summary.
        """
        return _Span(self, step, message, details or None)

    def _record_timing(self, step: str, duration_ms: int) -> None:
        slot = self.timings.setdefault(step, [0, 0])
        slot[0] += 1
        slot[1] += duration_ms

    def timings_summary(self) -> dict[str, dict[str, int]]:
        """Per-step ``{calls, total_ms}``, slowest first."""
        return {
            step: {"calls": calls, "total_ms": total}
            for step, (calls, total) in sorted(
                self.timings.items(), key=lambda kv: kv[1][1], reverse=True
            )
        }

    def log_timings_summary(self) -> None:
        """Emit the breakdown as a final ``step="timings"`` entry.

        Goes out through the existing channels (response ``logs``, SSE, file
        log) rather than adding a field every call site would have to thread
        through.
        """
        if self._timings_logged:
            return
        summary = self.timings_summary()
        if not summary:
            return
        self._timings_logged = True
        total_ms = sum(item["total_ms"] for item in summary.values())
        self.log(
            "timings",
            f"Stage timings ({total_ms} ms measured across {len(summary)} stages)",
            details={"stages": summary, "measured_total_ms": total_ms},
        )

    def log(
        self,
        step: str,
        message: str,
        level: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        entry = LogEntry(
            step=step,
            message=message,
            level=level,
            details=details,
            request_id=self.request_id,
        )
        self.entries.append(entry)
        _LOGGER.log(
            getattr(logging, level.upper(), logging.INFO),
            "%s | %s",
            step,
            entry.to_logfmt(),
        )

    def to_list(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]
