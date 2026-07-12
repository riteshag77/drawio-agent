"""System prompt builder — loads config/*.txt prompt files."""

from __future__ import annotations

import os
from functools import lru_cache

from app.adapters.outbound.config.env_config import EnvConfigAdapter

MAX_CUSTOM_SYSTEM_MESSAGE_LEN = 5000

CONFIG_DIR = EnvConfigAdapter.base_dir() / "config"
REFERENCES_DIR = CONFIG_DIR / "references"


@lru_cache(maxsize=24)
def _load_prompt(filename: str) -> str:
    path = CONFIG_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=24)
def _load_reference(filename: str) -> str:
    path = REFERENCES_DIR / filename
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _resolve_model_name(model_id: str | None, provider: str | None) -> str:
    if model_id:
        return model_id
    if provider:
        return provider
    return "AI"


ESTIMATE_PROMPT_TOKENS = lambda text: max(1, len(text) // 4)  # noqa: E731


def _get_reference_files(three_d: bool) -> list[str]:
    if three_d:
        return ["aws-icons-3d.txt", "aws-icons-allied-telesis.txt"]

    return [
        "aws-icons-common.txt",
        "aws-icons-compute.txt",
        "aws-icons-database.txt",
        "aws-icons-storage.txt",
        "aws-icons-networking.txt",
        "aws-icons-integration.txt",
        "aws-icons-security.txt",
        "aws-icons-analytics-ml.txt",
        "aws-icons-iot-migration-devtools.txt",
    ]


def get_xml_gen_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    three_d: bool = False,
) -> str:
    """System prompt for XML generation (LLM produces draw.io XML directly)."""
    model_name = _resolve_model_name(model_id, provider)
    base_prompt = "xml_gen_prompt_3d.txt" if three_d else "xml_gen_prompt.txt"
    parts: list[str] = [_load_prompt(base_prompt)]
    parts.append(_load_prompt("style_instructions.txt"))
    if platform == "aws":
        for ref in _get_reference_files(three_d):
            content = _load_reference(ref)
            if content:
                parts.append(content)
    custom = (custom_system_message or "").strip()
    if custom:
        parts.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")
    return "\n\n".join(parts).replace("{{MODEL_NAME}}", model_name)
