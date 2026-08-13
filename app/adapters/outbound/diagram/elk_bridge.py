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
import threading

from app.adapters.outbound.config.env_config import EnvConfigAdapter, get_config

logger = logging.getLogger(__name__)

__all__ = ["ElkLayoutError", "run_elk", "shutdown_elk_sidecar"]

_LAYOUT_SCRIPT = EnvConfigAdapter.base_dir() / "tools" / "elk_layout" / "layout.js"
_SERVE_SCRIPT = EnvConfigAdapter.base_dir() / "tools" / "elk_layout" / "serve.js"


class ElkLayoutError(RuntimeError):
    """Raised when ELK layout cannot be computed — callers must fall back."""


class _Sidecar:
    """A single long-lived ``serve.js`` process, guarded by a lock.

    Starting Node and loading elkjs costs ~300-400 ms; the layout itself costs
    ~46 ms for a typical view. Keeping one process alive turns that fixed cost
    into a one-off.

    Serialized on purpose. elkjs is single-threaded, so a pool would only add
    parallelism across processes — measured at 5 views, one serialized sidecar
    runs 233 ms against 932 ms for today's five concurrent cold spawns, and a
    five-process pool only reaches 170 ms. The remaining 63 ms does not justify
    five lifecycles, five restart paths and five times the memory.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 0

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _start(self, node_path: str) -> None:
        self._proc = subprocess.Popen(
            [node_path, str(_SERVE_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        logger.debug("Started ELK sidecar (pid %s)", self._proc.pid)

    def _kill(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    def layout(self, graph: dict, node_path: str, timeout: float) -> dict:
        """One request/response round-trip. Raises ``ElkLayoutError`` to fall back."""
        with self._lock:
            if not self._alive():
                self._kill()  # reap a dead process before replacing it
                self._start(node_path)

            self._next_id += 1
            request_id = self._next_id
            assert self._proc is not None and self._proc.stdin and self._proc.stdout

            try:
                self._proc.stdin.write(
                    json.dumps({"id": request_id, "graph": graph}, separators=(",", ":")) + "\n"
                )
                self._proc.stdin.flush()
            except (OSError, ValueError) as exc:
                self._kill()
                raise ElkLayoutError(f"ELK sidecar write failed: {exc}") from exc

            line = _read_line_with_timeout(self._proc, timeout)
            if line is None:
                # Wedged or dead mid-request. The pipe's framing is now
                # untrustworthy (a late reply would be read as the *next*
                # request's response), so the process cannot be reused.
                self._kill()
                raise ElkLayoutError(f"ELK sidecar timed out after {timeout}s")

            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                self._kill()
                raise ElkLayoutError(f"ELK sidecar returned unparseable output: {exc}") from exc

            if payload.get("id") != request_id:
                # Responses are FIFO, so a mismatch means the stream has
                # desynchronised — discard the process rather than return a
                # different request's layout.
                self._kill()
                raise ElkLayoutError("ELK sidecar response id mismatch — stream desynchronised")

            if not payload.get("ok"):
                # A layout failure is the graph's fault, not the process's:
                # serve.js stays up and the caller still falls back.
                raise ElkLayoutError(f"elk layout failed: {payload.get('error')}")

            return payload["result"]

    def shutdown(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            if proc is None:
                return
            try:
                if proc.stdin:
                    proc.stdin.close()  # serve.js exits on stdin EOF
                proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except OSError:
                    pass


_sidecar = _Sidecar()


def _read_line_with_timeout(proc: subprocess.Popen[str], timeout: float) -> str | None:
    """Blocking ``readline`` with a deadline.

    A reader thread rather than ``select``: on Windows ``select`` does not
    accept pipe handles, and this code is developed and run there. The thread
    is a daemon, so a wedged read cannot keep the interpreter alive.
    """
    result: list[str] = []

    def _read() -> None:
        try:
            assert proc.stdout is not None
            line = proc.stdout.readline()
            if line:
                result.append(line)
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(timeout)
    if reader.is_alive() or not result:
        return None
    return result[0]


def shutdown_elk_sidecar() -> None:
    """Stop the ELK sidecar. Call on application shutdown."""
    _sidecar.shutdown()


def run_elk(graph: dict, *, timeout: float | None = None) -> dict:
    """Lay out ``graph`` (an ELK graph JSON object) via the Node ``elkjs`` runner.

    Returns the laid-out graph (nodes carry ``x``/``y``/``width``/``height``;
    edges carry ``sections`` with ``startPoint``/``bendPoints``/``endPoint``).
    Raises ``ElkLayoutError`` on any failure — missing Node/elkjs, a non-zero
    exit, a timeout, or output that isn't parseable JSON.

    Prefers the long-lived sidecar and falls back to spawning ``layout.js``
    per call. The fallback is deliberate: a sidecar that dies, wedges or
    desynchronises degrades to the slower path that always worked, rather than
    failing a request — see ``ELK_SIDECAR_ENABLED`` to turn it off entirely.
    """
    node_path = shutil.which("node")
    if not node_path:
        raise ElkLayoutError("node executable not found on PATH")
    if not _LAYOUT_SCRIPT.is_file():
        raise ElkLayoutError(f"ELK layout script missing: {_LAYOUT_SCRIPT}")

    config = get_config()
    effective_timeout = timeout if timeout is not None else config.elk_layout_timeout_seconds

    if config.elk_sidecar_enabled and _SERVE_SCRIPT.is_file():
        try:
            return _sidecar.layout(graph, node_path, effective_timeout)
        except ElkLayoutError as exc:
            # Fall through to the per-call spawn. Logged at warning because a
            # healthy sidecar should not be failing — a persistent stream of
            # these means the fast path is silently off.
            logger.warning("ELK sidecar unavailable, falling back to per-call spawn: %s", exc)

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
