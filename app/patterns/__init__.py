"""Architecture pattern library — backward-compatible re-export."""

from app.adapters.outbound.patterns.file_repository import (  # noqa: F401
    ArchitecturePattern,
    FilePatternRepository,
    format_patterns_for_prompt,
    load_all_patterns,
)

# Backward-compatible aliases
ARCHITECTURE_PATTERNS = load_all_patterns()
