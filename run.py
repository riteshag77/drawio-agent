#!/usr/bin/env python3
"""Development server launcher."""

from __future__ import annotations

import uvicorn

from app.core.config import HOST, PORT, init_config, _resolve_config

init_config()
_resolve_config()

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=HOST,
        port=PORT,
        reload=True,
        # Prompt packs (config/xml_gen/**/*.txt) and provider config (config/*.json) are
        # read once and cached in-process (see prompts/system.py's lru_cache) —
        # without these, editing them while the dev server runs has no effect
        # until a manual restart, since uvicorn's default reload watch only
        # covers .py files.
        reload_includes=["*.txt", "*.json"],
    )
