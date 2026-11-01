"""Diagnose Anthropic HTTPS under Windows / truststore."""
from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")


def main() -> None:
    import httpx
    import truststore

    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip().strip('"')
    print("key_len", len(key))
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}

    truststore.inject_into_ssl()
    try:
        r = httpx.get("https://api.anthropic.com/v1/models", headers=headers, timeout=20)
        print("inject_into_ssl", r.status_code, r.text[:100])
    except Exception as exc:
        print("inject_into_ssl FAIL", type(exc).__name__, exc)

    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers=headers,
            timeout=20,
            verify=ctx,
        )
        print("SSLContext", r.status_code, r.text[:100])
    except Exception as exc:
        print("SSLContext FAIL", type(exc).__name__, exc)

    # Mimic app path: ssl_trust then langchain ChatAnthropic invoke
    from app.core import ssl_trust  # noqa: F401
    from app.adapters.outbound.llm.providers import build_langchain_client, get_provider

    spec = get_provider("claude")
    client = build_langchain_client(spec, timeout=30)
    try:
        msg = client.invoke("Reply with exactly: ok")
        print("langchain ok", str(getattr(msg, "content", msg))[:80])
    except Exception as exc:
        err = str(exc)
        if "authentication" in err.lower() or "invalid" in err.lower() or "401" in err:
            print("langchain AUTH FAIL — refresh ANTHROPIC_API_KEY in .env")
        else:
            print("langchain FAIL", type(exc).__name__, err[:200])


if __name__ == "__main__":
    main()
