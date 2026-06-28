"""Diagnose Cursor SDK local runtime (models list + minimal prompt)."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core.config import BASE_DIR  # noqa: E402
from app.core.cursor_bridge import apply_windows_bridge_patch  # noqa: E402
from app.llm.agent import _format_cursor_error  # noqa: E402


def main() -> int:
    api_key = os.getenv("CURSOR_API_KEY", "").strip()
    if not api_key:
        print("CURSOR_API_KEY is not set.")
        return 1

    apply_windows_bridge_patch()

    from cursor_sdk import Agent, AgentOptions, Cursor, LocalAgentOptions
    from cursor_sdk.errors import CursorAgentError

    print("Listing models...")
    try:
        models = Cursor.models.list(api_key=api_key)
        print(f"  {len(models)} model(s) available")
        model = os.getenv("CURSOR_MODEL", "composer-2.5")
        ids = [getattr(m, "id", str(m)) for m in models]
        if model not in ids:
            print(f"  WARNING: CURSOR_MODEL={model!r} not in list; first ids: {ids[:5]}")
    except CursorAgentError as error:
        print(f"  models.list failed: {_format_cursor_error(error)}")
        model = os.getenv("CURSOR_MODEL", "composer-2.5")

    print(f"Running minimal local prompt (model={model}, cwd={BASE_DIR})...")
    options = AgentOptions(
        api_key=api_key,
        model=model,
        name="drawio-agent-diagnose",
        local=LocalAgentOptions(cwd=str(BASE_DIR), setting_sources=[]),
    )
    try:
        result = Agent.prompt('Return {"ok": true} as JSON only.', options)
    except CursorAgentError as error:
        print(f"  prompt failed: {_format_cursor_error(error)}")
        return 2

    print(f"  status={result.status}")
    print(f"  result={result.result!r}")
    return 0 if result.status == "finished" else 2


if __name__ == "__main__":
    raise SystemExit(main())
