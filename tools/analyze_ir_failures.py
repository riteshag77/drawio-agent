"""One-off: extract full IR-failure details from a capture file."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    path = next(Path("data/captures").glob(f"*{sys.argv[1]}*.json"))
    d = json.loads(path.read_text(encoding="utf-8"))
    resp = d.get("response") or {}
    logs = resp.get("logs") or d.get("logs") or []
    for e in logs:
        if not isinstance(e, dict):
            continue
        step, msg = e.get("step"), str(e.get("message", ""))
        if step in ("ir", "llm", "views") or "IR" in msg:
            det = e.get("details") or {}
            print(f"== [{e.get('level')}] {step}: {msg[:400]}")
            for k, v in det.items():
                s = json.dumps(v, default=str) if not isinstance(v, str) else v
                print(f"   {k}: {s[:1500]}")
            print()


if __name__ == "__main__":
    main()
