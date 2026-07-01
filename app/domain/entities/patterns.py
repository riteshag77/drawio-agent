"""Architecture pattern domain entity."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ArchitecturePattern:
    id: str
    name: str
    summary: str
    keywords: tuple[str, ...]
    platforms: tuple[str, ...]
    flow: tuple[str, ...]
    services: dict[str, tuple[str, ...]]
    best_practices: tuple[str, ...] = field(default_factory=tuple)
