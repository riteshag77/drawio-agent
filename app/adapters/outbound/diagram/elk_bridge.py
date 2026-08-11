"""Bridge to the ELK (Eclipse Layout Kernel) graph-layout engine via ``elkjs``.

ELK computes node placement and edge routing together from one graph
description — see ``tools/elk_layout/layout.js`` (a thin, generic stdin/stdout
wrapper around ``elkjs``; all algorithm/spacing choices are made here in
Python, as graph ``layoutOptions``, not hardcoded in the JS).

``ElkLayoutError`` (raised here and in ``elk_layout_network.py``/
``elk_layout_archimate.py``/``elk_graph.py``) is **not** caught by every
caller — verified by grep across ``app/``: it propagates uncaught through
``generate_xml_diagram_from_ir`` for every ELK-based view except Network
(``compiler/network.py::compile_network_ir`` wraps it into ``CompilerError``,
the one exception). There is no "pre-ELK deterministic code path" left to
fall back to for any of these views — the cursor-arithmetic layouts ELK
replaced no longer exist (see e.g. ``compiler/network.py``'s own docstring).
A previous version of this comment claimed a fallback contract that was
already gone by the time it was written; if ELK becomes unavailable
(Node/elkjs missing, timeout, malformed output), a compile for any of these
views other than Network currently raises an unhandled internal exception,
not a graceful degradation.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess

from app.adapters.outbound.config.env_config import EnvConfigAdapter, get_config

logger = logging.getLogger(__name__)

__all__ = ["ElkLayoutError", "run_elk"]

_LAYOUT_SCRIPT = EnvConfigAdapter.base_dir() / "tools" / "elk_layout" / "layout.js"


class ElkLayoutError(RuntimeError):
    """Raised when ELK layout cannot be computed — callers must fall back."""


def run_elk(graph: dict, *, timeout: float | None = None) -> dict:
    """Lay out ``graph`` (an ELK graph JSON object) via the Node ``elkjs`` runner.

    Returns the laid-out graph (nodes carry ``x``/``y``/``width``/``height``;
    edges carry ``sections`` with ``startPoint``/``bendPoints``/``endPoint``).
    Raises ``ElkLayoutError`` on any failure — missing Node/elkjs, a non-zero
    exit, a timeout, or output that isn't parseable JSON.
    """
    node_path = shutil.which("node")
    if not node_path:
        raise ElkLayoutError("node executable not found on PATH")
    if not _LAYOUT_SCRIPT.is_file():
        raise ElkLayoutError(f"ELK layout script missing: {_LAYOUT_SCRIPT}")

    effective_timeout = timeout if timeout is not None else get_config().elk_layout_timeout_seconds
    try:
        proc = subprocess.run(
            [node_path, str(_LAYOUT_SCRIPT)],
            input=json.dumps(graph),
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ElkLayoutError(f"elk layout timed out after {effective_timeout}s") from exc
    except OSError as exc:
        raise ElkLayoutError(f"failed to spawn node: {exc}") from exc

    if proc.returncode != 0:
        raise ElkLayoutError(f"elk layout exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}")

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ElkLayoutError(f"elk layout returned unparseable output: {exc}") from exc
