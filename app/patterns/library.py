"""Backward-compatible re-export — content moved to app.adapters.outbound.patterns.file_repository."""
from app.adapters.outbound.patterns.file_repository import (  # noqa: F401
    ArchitecturePattern,
    FilePatternRepository,
    load_all_patterns,
)

ARCHITECTURE_PATTERNS = load_all_patterns()
