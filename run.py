"""Development entry point.

Run the API with ``python run.py`` (or ``uvicorn app.main:app --reload``).
"""

from __future__ import annotations

import uvicorn

from app.core.config import HOST, PORT


def main() -> None:
    uvicorn.run("app.main:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
