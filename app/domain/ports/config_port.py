"""Configuration port — abstract interface for reading application config."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ConfigPort(ABC):
    """Abstract interface for application configuration."""

    @abstractmethod
    def get(self, key: str, default: Any = None) -> Any:
        ...

    @abstractmethod
    def get_int(self, key: str, default: int = 0) -> int:
        ...

    @abstractmethod
    def get_float(self, key: str, default: float = 0.0) -> float:
        ...

    @abstractmethod
    def get_bool(self, key: str, default: bool = False) -> bool:
        ...

    @abstractmethod
    def get_list(self, key: str, default: list[str] | None = None, sep: str = ",") -> list[str]:
        ...
