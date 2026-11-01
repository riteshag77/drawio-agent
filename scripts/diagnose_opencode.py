"""Diagnose OpenCode Zen HTTP API (models list + minimal chat completion)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402

from app.adapters.outbound.llm.providers import normalize_gateway_base_url  # noqa: E402


def _fetch_models(api_key: str, base_url: str) -> list[str]:
    models_url = base_url.rstrip("/") + "/models"
    request = urllib.request.Request(
        models_url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode())
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []
    return [str(item.get("id", item)) for item in data if isinstance(item, dict)]


def main() -> int:
    api_key = os.getenv("OPENCODE_API_KEY", "").strip()
    if not api_key:
        print("OPENCODE_API_KEY is not set.")
        return 1

    base_url = normalize_gateway_base_url(
        os.getenv("OPENCODE_BASE_URL", "https://opencode.ai/zen/v1"),
        "https://opencode.ai/zen/v1",
    )
    model = os.getenv("OPENCODE_MODEL", "big-pickle").strip()

    print(f"Base URL: {base_url}")
    print(f"Model: {model}")
    print("Fetching model list...")
    try:
        model_ids = _fetch_models(api_key, base_url)
        print(f"  {len(model_ids)} model(s) available")
        if model not in model_ids:
            print(f"  WARNING: {model!r} not in list; first ids: {model_ids[:5]}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"  models fetch failed: {error}")

    print("Running minimal chat completion...")
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
        timeout=60,
    )
    try:
        response = llm.invoke([HumanMessage(content='Return {"ok": true} as JSON only.')])
    except Exception as error:
        print(f"  chat failed: {error}")
        return 2

    print(f"  response={response.content!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
