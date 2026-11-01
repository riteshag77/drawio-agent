"""Detects a leaf whose ``saas_*`` shape_type names a real curated vendor
its own label doesn't match — the wrong-brand-logo bug found live in the
prompt battle test (see ``config/references/saas.txt``'s own warning about
it). A leaf like ``{"label": "Clearinghouse A", "shape_type": "saas_fis"}``
renders the real FIS logo on something that isn't FIS.

Deliberately the same shape as ``core/services/governance.py``: a pure,
deterministic structural check (word-overlap against the catalog's known
labels) feeding the existing one-shot corrective pass — never a second
LLM judgment call, never a hard block. Lives here rather than in
``domain/ir/*.py`` because it needs to read the catalog file, and domain
IR modules are deliberately I/O-free (see CLAUDE.md's architecture notes).
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.adapters.outbound.config.env_config import EnvConfigAdapter

_CATALOG_PATH = EnvConfigAdapter.base_dir() / "config" / "references" / "saas.txt"

#: Words too generic to prove a match on their own (would let an unrelated
#: label like "Container Platform" false-negative-match saas_fis's "Modern
#: Banking Platform" needle). Excluded from both sides of the comparison.
_GENERIC_WORDS = frozenset({
    "platform", "cloud", "service", "services", "data", "core", "system",
    "systems", "management", "the", "and", "for",
})

_WORD_RE = re.compile(r"[a-z0-9]+")


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) >= 3 and w not in _GENERIC_WORDS}


@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, set[str]]:
    """``{shape_type: {needle words}}`` for every curated vendor row."""
    if not _CATALOG_PATH.is_file():
        return {}
    out: dict[str, set[str]] = {}
    for line in _CATALOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("saas_") or "|" not in line:
            continue
        shape_type, _, needles = line.partition("|")
        shape_type = shape_type.strip()
        words = _words(needles) | _words(shape_type.removeprefix("saas_").replace("_", " "))
        if shape_type and words:
            out[shape_type] = words
    return out


def find_saas_vendor_mismatches(ir) -> list[str]:
    """Warning strings for every leaf whose ``saas_*`` shape_type is a real
    curated vendor its own label shares no word with. Best-effort: an
    unreadable/missing catalog degrades to "no findings," never an error —
    matches every other structural-check call site in this codebase.
    """
    all_corridors = getattr(ir, "all_corridors", None)
    if not callable(all_corridors):
        return []  # ArchiMate/Network have no saas_* leaves to check (see module docstring)
    try:
        catalog = _load_catalog()
    except OSError:
        return []
    if not catalog:
        return []

    warnings: list[str] = []
    for corridor in all_corridors():
        for leaf in corridor.leaves:
            shape_type = (leaf.shape_type or "").strip()
            needle_words = catalog.get(shape_type)
            if needle_words is None:
                continue  # not a known curated vendor — an invented key, nothing to check
            label_words = _words(leaf.label or "")
            if label_words & needle_words:
                continue
            vendor_name = shape_type.removeprefix("saas_").replace("_", " ")
            warnings.append(
                f"leaf {leaf.id!r} (label {leaf.label!r}) uses shape_type={shape_type!r} — "
                f"a real, curated vendor icon ({vendor_name}) — but the label doesn't name that "
                "vendor. Either this genuinely is that vendor (fix the label to say so), or it "
                "isn't (invent a new saas_<name> key instead of borrowing an unrelated vendor's "
                "icon — see the SaaS catalog's own guidance on this)."
            )
    return warnings
