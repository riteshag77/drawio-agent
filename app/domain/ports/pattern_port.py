"""Pattern repository port — abstract interface for loading architecture patterns."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.patterns import ArchitecturePattern


class PatternRepository(ABC):
    """Interface for loading and querying architecture patterns."""

    @abstractmethod
    def find_matching(self, prompt: str, platform: str = "aws", *, limit: int = 2, min_score: int = 1) -> list[ArchitecturePattern]:
        ...

    @abstractmethod
    def get_all(self) -> list[ArchitecturePattern]:
        ...

    @abstractmethod
    def get_by_id(self, pattern_id: str) -> ArchitecturePattern | None:
        ...
