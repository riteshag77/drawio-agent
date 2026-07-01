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
    )
