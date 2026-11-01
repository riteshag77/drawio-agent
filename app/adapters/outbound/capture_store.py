"""Persists every full, untruncated /chat response to disk for later playback.

Deliberately separate from ``core.logging.StepLogger`` — the logger is a
human-facing diagnostic trail (and, even after removing its truncation, still
only covers pipeline *steps*); this module snapshots the complete request +
response pair as one JSON file, which is what a playback viewer needs to
faithfully re-render a past run without calling the LLM/MCP again.

Never raises out to the caller — a capture-write failure must not break the
live chat response it's shadowing.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from app.adapters.outbound.config.env_config import get_config

# Untruncated captures (full XML/cost/WAF/compliance/LLM payloads) can each be
# tens to hundreds of KB — with no retention cap this directory grows without
# bound under regular use, unlike the rotating/retention-pruned StepLogger
# text log. Prune oldest files beyond this count on every write instead.
_MAX_CAPTURES = 500


def _captures_dir() -> Path:
    raw = get_config().captures_dir
    path = Path(raw)
    if not path.is_absolute():
        from app.adapters.outbound.config.env_config import EnvConfigAdapter

        path = EnvConfigAdapter.base_dir() / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def _prompt_snippet(request_body: dict[str, Any]) -> str:
    messages = request_body.get("messages") or []
    text = ""
    if messages:
        text = messages[-1].get("content", "") or ""
    elif request_body.get("user_prompt"):
        text = request_body["user_prompt"]
    text = re.sub(r"\s+", " ", text).strip()
    return text[:160]


def save_capture(request_body: dict[str, Any], response: dict[str, Any]) -> str | None:
    """Write one JSON file with the full request + response. Returns the capture id, or None on failure."""
    try:
        ts = time.time()
        # Zero-padded nanosecond epoch as the sort key — filename lexicographic
        # order must match creation order even for captures written microseconds
        # apart (e.g. several suggestion-apply turns in a tight test/dev loop),
        # which a 1-second-resolution timestamp prefix can't guarantee. Not meant
        # to be human-readable; display uses the stored `timestamp` float instead.
        capture_id = f"{time.time_ns():020d}_{uuid.uuid4().hex[:8]}"
        record = {
            "id": capture_id,
            "timestamp": ts,
            "prompt_snippet": _prompt_snippet(request_body),
            "request": request_body,
            "response": response,
        }
        captures_dir = _captures_dir()
        path = captures_dir / f"{capture_id}.json"
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        _prune_oldest(captures_dir)
        return capture_id
    except Exception:
        return None


def _prune_oldest(captures_dir: Path) -> None:
    try:
        keep = _MAX_CAPTURES
        files = sorted(captures_dir.glob("*.json"), key=lambda p: p.name)
        for stale in files[:-keep] if len(files) > keep else []:
            stale.unlink(missing_ok=True)
    except Exception:
        pass  # pruning is best-effort — never let it break a capture write


def list_captures(limit: int = 200) -> list[dict[str, Any]]:
    """Lightweight metadata for every capture, newest first."""
    try:
        files = sorted(_captures_dir().glob("*.json"), key=lambda p: p.name, reverse=True)
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for path in files[:limit]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append({
            "id": record.get("id", path.stem),
            "timestamp": record.get("timestamp"),
            "prompt_snippet": record.get("prompt_snippet", ""),
        })
    return out


def load_capture(capture_id: str) -> dict[str, Any] | None:
    """Full saved record ({id, timestamp, prompt_snippet, request, response}), or None if missing/invalid."""
    safe_id = re.sub(r"[^A-Za-z0-9_]", "", capture_id or "")
    if not safe_id:
        return None
    path = _captures_dir() / f"{safe_id}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
