"""Collects structured step logs for the generation pipeline."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any


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


# Root logger for server-side output (separate from per-request StepLogger instances).
_LOGGER: logging.Logger = logging.getLogger("drawio-agent")
_LOGGER.setLevel(logging.DEBUG)

_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
)
_LOGGER.addHandler(_console)

_log_dir = os.environ.get("DRAWIO_AGENT_LOG_DIR", "logs")
try:
    os.makedirs(_log_dir, exist_ok=True)
    _file = RotatingFileHandler(
        os.path.join(_log_dir, "drawio-agent.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    _file.setLevel(logging.DEBUG)
    _file.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    _LOGGER.addHandler(_file)
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
