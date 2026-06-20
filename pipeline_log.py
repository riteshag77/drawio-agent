"""Collects structured step logs for the generation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class LogEntry:
    step: str
    message: str
    level: str = "info"
    details: dict[str, Any] | None = None
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
        return payload


class StepLogger:
    """Append-only logger for pipeline steps returned to the UI."""

    def __init__(self) -> None:
        self.entries: list[LogEntry] = []

    def log(
        self,
        step: str,
        message: str,
        level: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.entries.append(LogEntry(step=step, message=message, level=level, details=details))

    def to_list(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]
