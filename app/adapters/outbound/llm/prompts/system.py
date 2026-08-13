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
    """Keep the preamble, plus each section that still has a matching row.

    Section-aware rather than "leading header block, then body". The previous
    version latched a single ``in_header`` flag off on the first ordinary line
    and never re-armed it, so any heading *below* that point was treated as a
    data row and survived only if it happened to match a hint token. With a
    catalog written as several ``##`` sections that meant the filtered slice
    arrived as bare rows: no preamble telling the model to copy keys verbatim,
    no column headers, and — worst — rows from the "never use these keys"
    table stripped of the heading that made them prohibitions, so they read as
    ordinary catalog entries.

    A section is dropped whole when nothing in it matches, which keeps the
    slice short without ever separating a row from the heading that gives it
    meaning.
    """
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
    if not tokens:
        return catalog

    def _is_heading(line: str) -> bool:
        # Level 2+ only. A lone `#` is the document title, which belongs to the
        # preamble along with the instructions that follow it.
        return line.lstrip().startswith("##")

    def _is_separator(line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and set(stripped) <= set("|-: ")

    # Preamble is everything before the first section; it carries the title and
    # the "copy the key verbatim" instruction, so it always survives.
    first_heading = next((i for i, ln in enumerate(lines) if _is_heading(ln)), len(lines))
    preamble = lines[:first_heading]

    kept: list[list[str]] = []
    section: list[str] = []
    section_has_match = False

    def _flush() -> None:
        nonlocal section, section_has_match
        if section and section_has_match:
            kept.append(section)
        section, section_has_match = [], False

    body_lines = lines[first_heading:]
    for index, line in enumerate(body_lines):
        if _is_heading(line):
            _flush()
            section = [line]
            continue
        if not section:
            continue
        # A table's column header is the row immediately above its `|---|`
        # separator. Keeping both means a surviving data row never arrives
        # without the columns that say what it means.
        next_line = body_lines[index + 1] if index + 1 < len(body_lines) else ""
        if _is_separator(line) or _is_separator(next_line) or not line.strip():
            section.append(line)
            continue
        if any(tok in line.lower() for tok in tokens):
            section.append(line)
            section_has_match = True
    _flush()

    # Nothing matched anywhere — a filtered catalog with no services is worse
    # than a long one, so fall back to the whole file.
    if not kept:
        return catalog

    # Trim by dropping whole sections, never by slicing lines: a cut mid-section
    # is how a heading ends up with no rows under it, or — for the "never emit
    # these" table — how a prohibition loses the rows it was prohibiting.
    selected: list[str] = list(preamble)
    for block in kept:
        if len(selected) + len(block) > max_lines and len(selected) > len(preamble):
            break
        selected.extend(block)
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
    stable = [_load_prompt("prompt_3d.txt")]
    volatile: list[str] = []
    for ref in _get_reference_files("aws", three_d=True):
        content = _load_reference(ref)
        if content:
            volatile.append(_filter_catalog(content, user_hint))
    custom = (custom_system_message or "").strip()
    if custom:
        volatile.append(f"## Custom Instructions\n{custom[:MAX_CUSTOM_SYSTEM_MESSAGE_LEN]}")
    stable_text = "\n\n".join(p for p in stable if p)
    volatile_text = "\n\n".join(p for p in volatile if p)
    return stable_text, volatile_text


def get_xml_gen_system_prompt(
    model_id: str | None = None,
    *,
    provider: str | None = None,
    platform: str = "aws",
    custom_system_message: str = "",
    three_d: bool = False,
    user_hint: str = "",
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
