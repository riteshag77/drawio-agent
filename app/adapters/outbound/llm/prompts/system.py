"""System prompt builder — CORE + platform packs (token-efficient)."""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from app.adapters.outbound.config.env_config import EnvConfigAdapter

_log = logging.getLogger(__name__)

MAX_CUSTOM_SYSTEM_MESSAGE_LEN = 5000
# Soft budget for assembled system prompt (~tokens ≈ chars/4).
# CORE + platform packs + filtered catalog.
MAX_SYSTEM_PROMPT_TOKENS = 8000

# Sentinel marking the boundary between the stable (cacheable) prefix and the
# per-request volatile suffix. The LLM adapter (``llm.caching.build_system_message``)
# splits on this to place a provider prompt-cache breakpoint; it is never sent to
# the model. Uses NUL bytes so it cannot collide with prompt text.
CACHE_BREAKPOINT = "CACHE_BREAKPOINT"

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


def _assemble_prompt_parts(
    model_id: str | None,
    *,
    provider: str | None,
    platform: str,
    custom_system_message: str,
    three_d: bool,
    user_hint: str,
) -> tuple[str, str]:
    """Return ``(stable_prefix, volatile_suffix)``.

    ``stable_prefix`` = CORE + platform packs — byte-identical across requests for
    a given platform/model, so it is the natural prompt-cache prefix.
    ``volatile_suffix`` = the user-hint-filtered reference catalog + any custom
    instructions — these vary per request and must sit *after* the cache
    breakpoint so they never invalidate the cached prefix.
    """
    model_name = _resolve_model_name(model_id, provider)
    platform_key = (platform or "aws").strip().lower()
    if platform_key not in _ICON_PLATFORMS:
        platform_key = "aws"

    stable: list[str] = []
    volatile: list[str] = []

    if three_d:
        # 3D path: isometric prompt + 3d refs only — never aws4 icon pack / orthogonal style.
        stable.append(_load_prompt("xml_gen_prompt_3d.txt"))
        for ref in _get_reference_files("aws", three_d=True):
            content = _load_reference(ref)
            if content:
                volatile.append(_filter_catalog(content, user_hint))
    else:
        stable.append(_load_prompt("xml_gen_core.txt"))
        stable.append(_load_prompt(f"xml_gen_classify_{platform_key}.txt"))
        stable.append(_load_prompt(f"xml_gen_icons_{platform_key}.txt"))
        for ref in _get_reference_files(platform_key, three_d=False):
            content = _load_reference(ref)
            if content:
                volatile.append(_filter_catalog(content, user_hint))

    custom = (custom_system_message or "").strip()
    if custom:
        volatile.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")

    stable_text = "\n\n".join(p for p in stable if p).replace("{{MODEL_NAME}}", model_name)
    volatile_text = "\n\n".join(p for p in volatile if p).replace("{{MODEL_NAME}}", model_name)
    return stable_text, volatile_text


def _warn_if_over_budget(prompt: str) -> None:
    tokens = ESTIMATE_PROMPT_TOKENS(prompt)
    if tokens > MAX_SYSTEM_PROMPT_TOKENS:
        _log.warning(
            "Assembled system prompt ~%d tokens exceeds soft budget %d — "
            "consider trimming the prompt packs or catalog filter.",
            tokens, MAX_SYSTEM_PROMPT_TOKENS,
        )


def get_xml_gen_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    three_d: bool = False,
    user_hint: str = "",
) -> str:
    """Assemble CORE + platform pack (+ optional 3D overlay) as one plain string."""
    stable, volatile = _assemble_prompt_parts(
        model_id, provider=provider, platform=platform,
        custom_system_message=custom_system_message, three_d=three_d, user_hint=user_hint,
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
) -> str:
    """Same assembly, with a ``CACHE_BREAKPOINT`` sentinel between the stable
    prefix and the volatile suffix. The LLM adapter splits on the sentinel to
    place a provider prompt-cache breakpoint; the sentinel is never sent."""
    stable, volatile = _assemble_prompt_parts(
        model_id, provider=provider, platform=platform,
        custom_system_message=custom_system_message, three_d=three_d, user_hint=user_hint,
    )
    _warn_if_over_budget(f"{stable}\n\n{volatile}" if volatile else stable)
    # Marker sits at the end of the stable prefix even when the suffix is empty,
    # so the whole prefix is still marked cacheable.
    return f"{stable}\n\n{CACHE_BREAKPOINT}{volatile}"
