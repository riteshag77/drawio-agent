"""System prompt helpers shared by IR assembly and the 3D XML path.

Fresh cloud diagrams use ``prompts/ir.py`` only. The only remaining mxfile
prompt pack is ``prompt_3d.txt`` (isometric). Edit mode uses surgical ops —
not these packs.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from app.adapters.outbound.config.env_config import EnvConfigAdapter

_log = logging.getLogger(__name__)

MAX_CUSTOM_SYSTEM_MESSAGE_LEN = 5000
MAX_SYSTEM_PROMPT_TOKENS = 13_000

# Sentinel marking the boundary between the stable (cacheable) prefix and the
# per-request volatile suffix. The LLM adapter splits on this; never sent to the model.
CACHE_BREAKPOINT = "CACHE_BREAKPOINT"

CONFIG_DIR = EnvConfigAdapter.base_dir() / "config"
PROMPTS_DIR = CONFIG_DIR / "xml_gen"
REFERENCES_DIR = CONFIG_DIR / "references"


@lru_cache(maxsize=24)
def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
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
    if three_d:
        return ["aws-icons-3d.txt", "aws-icons-allied-telesis.txt"]
    if platform == "multi":
        return ["multi-icons-top.txt"]
    if platform == "gcp":
        return ["gcp-icons-common.txt"]
    if platform == "azure":
        return ["azure-icons-common.txt"]
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
        return catalog
    selected = header + body
    if len(selected) > max_lines:
        selected = header + body[: max(0, max_lines - len(header))]
    return "\n".join(selected)


def _warn_if_over_budget(prompt: str) -> None:
    tokens = ESTIMATE_PROMPT_TOKENS(prompt)
    if tokens > MAX_SYSTEM_PROMPT_TOKENS:
        _log.warning(
            "Assembled system prompt ~%d tokens exceeds soft budget %d — "
            "consider trimming the prompt packs or catalog filter.",
            tokens, MAX_SYSTEM_PROMPT_TOKENS,
        )


def _assemble_3d_prompt_parts(
    model_id: str | None,
    *,
    provider: str | None,
    custom_system_message: str,
    user_hint: str,
) -> tuple[str, str]:
    """Return ``(stable_prefix, volatile_suffix)`` for the isometric 3D path only."""
    model_name = _resolve_model_name(model_id, provider)
    stable = [_load_prompt("prompt_3d.txt")]
    volatile: list[str] = []
    for ref in _get_reference_files("aws", three_d=True):
        content = _load_reference(ref)
        if content:
            volatile.append(_filter_catalog(content, user_hint))
    custom = (custom_system_message or "").strip()
    if custom:
        volatile.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")
    stable_text = "\n\n".join(p for p in stable if p).replace("{{MODEL_NAME}}", model_name)
    volatile_text = "\n\n".join(p for p in volatile if p).replace("{{MODEL_NAME}}", model_name)
    return stable_text, volatile_text


def get_xml_gen_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    three_d: bool = False,
    user_hint: str = "",
    primary_view: str = "network",
) -> str:
    """Assemble the 3D isometric system prompt (only remaining direct-XML pack)."""
    if not three_d:
        raise ValueError(
            "get_xml_gen_system_prompt is only for three_d=True. "
            "Fresh cloud diagrams use IR prompts (prompts/ir.py)."
        )
    stable, volatile = _assemble_3d_prompt_parts(
        model_id, provider=provider,
        custom_system_message=custom_system_message, user_hint=user_hint,
    )
    prompt = f"{stable}\n\n{volatile}" if volatile else stable
    _warn_if_over_budget(prompt)
    return prompt


def get_xml_gen_system_prompt_marked(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    three_d: bool = False,
    user_hint: str = "",
    primary_view: str = "network",
) -> str:
    """3D prompt with ``CACHE_BREAKPOINT`` between stable and volatile parts."""
    if not three_d:
        raise ValueError(
            "get_xml_gen_system_prompt_marked is only for three_d=True. "
            "Fresh cloud diagrams use IR prompts (prompts/ir.py)."
        )
    stable, volatile = _assemble_3d_prompt_parts(
        model_id, provider=provider,
        custom_system_message=custom_system_message, user_hint=user_hint,
    )
    _warn_if_over_budget(f"{stable}\n\n{volatile}" if volatile else stable)
    return f"{stable}\n\n{CACHE_BREAKPOINT}{volatile}"
