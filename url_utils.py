"""URL content extraction for diagram context — port of next-ai-draw-io lib/url-utils.ts"""

from __future__ import annotations

import re
from html import unescape

import httpx

MAX_EXTRACTED_CHARS = 12000
USER_AGENT = "drawio-agent/1.0 (+https://github.com/drawio-agent)"


def extract_text_from_html(html_content: str) -> tuple[str, str]:
    """Return (title, plain text) from HTML."""
    title_match = re.search(r"<title[^>]*>([^<]*)</title>", html_content, re.IGNORECASE)
    title = unescape(title_match.group(1).strip()) if title_match else "Untitled"

    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html_content)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(re.sub(r"\s+", " ", text)).strip()
    if len(text) > MAX_EXTRACTED_CHARS:
        text = text[:MAX_EXTRACTED_CHARS] + "…"
    return title, text


async def fetch_url_content(url: str) -> dict:
    """Fetch URL and return title, content, char_count."""
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        response = await client.get(url, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "html" not in content_type.lower():
        text = response.text[:MAX_EXTRACTED_CHARS]
        return {"title": url, "content": text, "char_count": len(text)}

    title, text = extract_text_from_html(response.text)
    return {"title": title, "content": text, "char_count": len(text)}
