"""Collects structured step logs for the generation pipeline."""

from __future__ import annotations

import glob
import json
import logging
import os
import sys
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


class StepLogger:
    """Append-only logger for pipeline steps returned to the UI."""

    def __init__(self, request_id: str | None = None) -> None:
        self.entries: list[LogEntry] = []
        self.request_id: str | None = request_id

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
