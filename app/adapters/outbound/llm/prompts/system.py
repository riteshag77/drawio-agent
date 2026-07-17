"""System prompt builder — CORE + platform packs (token-efficient)."""

from __future__ import annotations

import re
from functools import lru_cache

from app.adapters.outbound.config.env_config import EnvConfigAdapter

MAX_CUSTOM_SYSTEM_MESSAGE_LEN = 5000
# Soft budget for assembled system prompt (~tokens ≈ chars/4).
# Nesting + no-edge + label rules need headroom; still below legacy ~4.9k AWS monolith.
MAX_SYSTEM_PROMPT_TOKENS = 4600

CONFIG_DIR = EnvConfigAdapter.base_dir() / "config"
REFERENCES_DIR = CONFIG_DIR / "references"

_ICON_PLATFORMS = frozenset({"aws", "gcp", "azure", "multi"})


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


def _get_reference_files(platform: str, three_d: bool) -> list[str]:
    if platform == "multi":
        return ["multi-icons-top.txt"]
    if platform == "gcp":
        return ["gcp-icons-common.txt"]
    if platform == "azure":
        return ["azure-icons-common.txt"]
    if three_d:
        return ["aws-icons-3d.txt", "aws-icons-allied-telesis.txt"]
    return ["aws_stencils.txt"]


def _filter_catalog(catalog: str, user_hint: str, *, max_lines: int = 48) -> str:
    """Keep header lines + catalog lines that match tokens in the user request."""
    if not catalog.strip():
        return ""
    hint = (user_hint or "").lower()
    lines = catalog.splitlines()
    if not hint or len(lines) <= max_lines:
        return catalog

    tokens = {t for t in re.findall(r"[a-z0-9_]{3,}", hint) if t not in {
        "the", "and", "with", "for", "from", "that", "this", "into", "using",
        "draw", "diagram", "architecture", "cloud", "please", "create", "show",
    }}
    header: list[str] = []
    body: list[str] = []
    in_header = True
    for line in lines:
        stripped = line.strip()
        if in_header and (not stripped or stripped.startswith("━") or stripped.startswith("Service")
                          or stripped.startswith("Product") or stripped.startswith("Standalone")
                          or stripped.startswith("##") or stripped.startswith("BROKEN")):
            header.append(line)
            continue
        in_header = False
        low = line.lower()
        if not tokens or any(tok in low for tok in tokens):
            body.append(line)

    if not body:
        # Fallback: keep compact catalog as-is (already small for aws_stencils).
        return catalog
    selected = header + body
    if len(selected) > max_lines:
        selected = header + body[: max(0, max_lines - len(header))]
    return "\n".join(selected)


def get_xml_gen_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    three_d: bool = False,
    user_hint: str = "",
) -> str:
    """Assemble CORE + platform pack (+ optional 3D overlay). No style_instructions dup."""
    model_name = _resolve_model_name(model_id, provider)
    platform_key = (platform or "aws").strip().lower()
    if platform_key not in _ICON_PLATFORMS:
        platform_key = "aws"

    parts: list[str] = []

    if three_d:
        # 3D path: isometric prompt + 3d refs only — never aws4 icon pack / orthogonal style.
        parts.append(_load_prompt("xml_gen_prompt_3d.txt"))
        for ref in _get_reference_files("aws", three_d=True):
            content = _load_reference(ref)
            if content:
                parts.append(_filter_catalog(content, user_hint))
    else:
        parts.append(_load_prompt("xml_gen_core.txt"))
        parts.append(_load_prompt(f"xml_gen_classify_{platform_key}.txt"))
        parts.append(_load_prompt(f"xml_gen_icons_{platform_key}.txt"))
        for ref in _get_reference_files(platform_key, three_d=False):
            content = _load_reference(ref)
            if content:
                parts.append(_filter_catalog(content, user_hint))

    custom = (custom_system_message or "").strip()
    if custom:
        parts.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")

    return "\n\n".join(p for p in parts if p).replace("{{MODEL_NAME}}", model_name)
