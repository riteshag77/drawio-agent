"""Windows fix for cursor-sdk bridge discovery (selectors cannot watch pipe fds)."""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import subprocess
    from typing import Any, Mapping

_patched = False


def apply_windows_bridge_patch() -> None:
    global _patched
    if _patched or sys.platform != "win32":
        return

    import cursor_sdk._bridge as bridge

    def _read_discovery_threaded(
        process: "subprocess.Popen[str]", timeout: float
    ) -> "Mapping[str, Any]":
        from cursor_sdk.errors import CursorSDKError

        if process.stderr is None:
            raise CursorSDKError("Bridge process stderr is unavailable")

        lines: queue.Queue[str | None] = queue.Queue()

        def _reader() -> None:
            try:
                for line in process.stderr:
                    lines.put(line)
            finally:
                lines.put(None)

        threading.Thread(target=_reader, daemon=True).start()
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            try:
                line = lines.get(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                line = "__timeout__"
            if line is None:
                break
            if line != "__timeout__":
                discovery = bridge.parse_discovery_line(line)
                if discovery is not None:
                    return discovery

            exit_code = process.poll()
            if exit_code is not None:
                raise CursorSDKError(
                    f"Bridge exited before discovery with status {exit_code}"
                )

        raise CursorSDKError("Timed out waiting for bridge discovery")

    bridge._read_discovery = _read_discovery_threaded
    _patched = True
