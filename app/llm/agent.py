"""Backward-compatible re-export — content moved to app.core.services.agent"""
from app.core.services.agent import (  # noqa: F401
    MODEL_IDS,
    PLATFORM_LABELS,
    _diagram_summary,
    _format_cursor_error,
    _message_text,
    _normalize_opencode_model,
    _platform_label,
    build_platform_conversion_block,
    generate_architecture_from_chat,
    generate_architecture_json,
    generate_edit_operations,
    generate_mermaid_diagram,
)
