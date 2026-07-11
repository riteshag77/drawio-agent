"""System prompt builder — lean topology-only prompts from config/*.txt."""

from __future__ import annotations

from functools import lru_cache

from app.adapters.outbound.config.env_config import EnvConfigAdapter

MAX_CUSTOM_SYSTEM_MESSAGE_LEN = 5000

# Kept for prompt_builders.py (XML edit validation feedback).
XML_EDIT_VALIDATION_FEEDBACK = """
When VALIDATION FEEDBACK is provided, fix visual issues with minimal operations:
- Move overlapping nodes apart (update geometry x/y).
- Delete redundant edges or shorten labels.
- Do NOT regenerate the entire diagram unless replace mode is truly necessary.
"""

CONFIG_DIR = EnvConfigAdapter.base_dir() / "config"

_CATALOGUE_BY_PLATFORM = {
    "aws": "catalogue_aws.txt",
    "gcp": "catalogue_gcp.txt",
    "azure": "catalogue_azure.txt",
    "multi": "catalogue_aws.txt",
}

_TOPOLOGY_BY_PLATFORM = {
    "aws": "topology_aws_reference.txt",
    "gcp": "topology_generic.txt",
    "azure": "topology_generic.txt",
    "multi": "topology_aws_reference.txt",
}


@lru_cache(maxsize=24)
def _load_prompt(filename: str) -> str:
    path = CONFIG_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def _resolve_model_name(model_id: str | None, provider: str | None) -> str:
    if model_id:
        return model_id
    if provider:
        return provider
    return "AI"


def _catalogue_file(platform: str) -> str:
    return _CATALOGUE_BY_PLATFORM.get((platform or "aws").lower(), "catalogue_aws.txt")


def _topology_file(platform: str) -> str:
    return _TOPOLOGY_BY_PLATFORM.get((platform or "aws").lower(), "topology_aws_reference.txt")


def estimate_prompt_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) for budget tests."""
    return max(1, len(text) // 4)


def _assemble_prompt(
    *,
    model_id: str | None = None,
    provider: str | None = None,
    platform: str = "aws",
    minimal_style: bool = False,
    custom_system_message: str = "",
    is_edit_mode: bool = False,
    include_schema: bool = True,
    patterns_block: str = "",
) -> str:
    model_name = _resolve_model_name(model_id, provider)
    parts: list[str] = [_load_prompt("architect_default.txt")]

    if minimal_style:
        parts.insert(0, _load_prompt("minimal_style.txt"))
    else:
        parts.append(_load_prompt("style_instructions.txt"))

    parts.append(_load_prompt("contract.txt"))

    if include_schema:
        parts.append(_load_prompt(_catalogue_file(platform)))
        parts.append(_load_prompt(_topology_file(platform)))
    else:
        # Edit turns: catalogue only so new nodes use valid shape keys.
        parts.append(_load_prompt(_catalogue_file(platform)))

    block = (patterns_block or "").strip()
    if block:
        parts.append(f"---\n{block}")

    if is_edit_mode:
        parts.append(_load_prompt("chat_edit_instructions.txt"))

    custom = (custom_system_message or "").strip()
    if custom:
        parts.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")

    return "\n\n".join(parts).replace("{{MODEL_NAME}}", model_name)


def get_system_prompt(
    model_id: str | None = None,
    minimal_style: bool = False,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    is_edit_mode: bool = False,
    include_schema: bool = True,
    patterns_block: str = "",
) -> str:
    return _assemble_prompt(
        model_id=model_id,
        provider=provider,
        platform=platform,
        minimal_style=minimal_style,
        custom_system_message=custom_system_message,
        is_edit_mode=is_edit_mode,
        include_schema=include_schema,
        patterns_block=patterns_block,
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
        prompt += f"\n\n## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}"
    return prompt
