"""
System prompt builder — loads prompts from external .txt files.

Composes role, layout/edge/label rules (next-ai), output contract, architecture schema catalogue,
and expert architect principles.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

EXTENDED_PROMPT_MODEL_PATTERNS = (
    "claude-opus-4-5",
    "claude-haiku-4-5",
)

MAX_CUSTOM_SYSTEM_MESSAGE_LEN = 5000

ARCHITECTURE_SCHEMA_FILENAME = "schema.txt"

# Kept as a Python constant because prompt_builders.py imports it for human-prompt assembly.
XML_EDIT_VALIDATION_FEEDBACK = """
When VALIDATION FEEDBACK is provided, fix visual issues with minimal operations:
- Move overlapping nodes apart (update geometry x/y).
- Delete redundant edges or shorten labels.
- Do NOT regenerate the entire diagram unless replace mode is truly necessary.
"""


CONFIG_DIR = Path(__file__).resolve().parents[5] / "config"


@lru_cache(maxsize=12)
def _load_prompt(filename: str) -> str:
    path = CONFIG_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _load_architecture_schema() -> str:
    schema_path = CONFIG_DIR / ARCHITECTURE_SCHEMA_FILENAME
    if not schema_path.is_file():
        raise FileNotFoundError(f"Architecture schema not found: {schema_path}")
    return schema_path.read_text(encoding="utf-8")


def _resolve_model_name(model_id: str | None, provider: str | None) -> str:
    if model_id:
        return model_id
    if provider:
        return provider
    return "AI"


def _uses_extended_prompt(model_id: str | None) -> bool:
    if not model_id:
        return False
    lowered = model_id.lower()
    return any(pattern in lowered for pattern in EXTENDED_PROMPT_MODEL_PATTERNS)


def _assemble_prompt(
    *,
    model_id: str | None = None,
    provider: str | None = None,
    minimal_style: bool = False,
    custom_system_message: str = "",
    is_edit_mode: bool = False,
    include_schema: bool = True,
    patterns_block: str = "",
    use_extended: bool = False,



) -> str:
    model_name = _resolve_model_name(model_id, provider)
    prompt = _load_prompt("architect_default.txt")

    if minimal_style:
        prompt = _load_prompt("minimal_style.txt") + "\n\n" + prompt
    else:
        prompt += "\n\n" + _load_prompt("style_instructions.txt")

    if use_extended:
        prompt += "\n\n" + _load_prompt("extended_additions.txt")

    prompt += "\n\n" + _load_prompt("principles.txt")

    block = (patterns_block or "").strip()
    if block:
        prompt += f"\n\n---\n{block}"

    if include_schema:
        schema = _load_architecture_schema()
        prompt += f"\n\n---\n## Architecture Schema & Platform Catalogue\n\n{schema}"

    if is_edit_mode:
        prompt += "\n\n" + _load_prompt("chat_edit_instructions.txt")

    custom = (custom_system_message or "").strip()
    if custom:
        custom = custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]
        prompt += f"\n\n## Custom Instructions\n{custom}"

    return prompt.replace("{{MODEL_NAME}}", model_name)


def get_system_prompt(
    model_id: str | None = None,
    minimal_style: bool = False,
    *,
    provider: str | None = None,
    custom_system_message: str = "",
    is_edit_mode: bool = False,
    include_schema: bool = True,
    patterns_block: str = "",
) -> str:
    return _assemble_prompt(
        model_id=model_id,
        provider=provider,
        minimal_style=minimal_style,
        custom_system_message=custom_system_message,
        is_edit_mode=is_edit_mode,
        include_schema=include_schema,
        patterns_block=patterns_block,
        use_extended=_uses_extended_prompt(model_id),
    )


def get_edit_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    custom_system_message: str = "",
) -> str:
    model_name = _resolve_model_name(model_id, provider)
    prompt = _load_prompt("xml_edit_prompt.txt").replace("{{MODEL_NAME}}", model_name)
    custom = (custom_system_message or "").strip()
    if custom:
        custom = custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]
        prompt += f"\n\n## Custom Instructions\n{custom}"
    return prompt
