"""Port interfaces — contracts between domain and adapters."""

from app.domain.ports.config_port import ConfigPort
from app.domain.ports.pattern_port import PatternRepository

__all__ = [
    "ConfigPort",
    "PatternRepository",
]
