"""URL content extraction for diagram context — port of next-ai-draw-io lib/url-utils.ts"""

from __future__ import annotations

import ipaddress
import re
import socket
from html import unescape
from urllib.parse import urlparse

import httpx

MAX_EXTRACTED_CHARS = 12000
MAX_RESPONSE_BYTES = 2_000_000
USER_AGENT = "drawio-agent/1.0 (+https://github.com/drawio-agent)"
ALLOWED_PORTS = {80, 443}


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


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_public_http_url(url: str) -> str:
    """Validate scheme/port and resolve host; reject private/link-local/metadata targets."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise ValueError(f"Port {port} is not allowed")

    host = parsed.hostname
    try:
        # Literal IP in the URL
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise ValueError("URL resolves to a blocked address")
        return url
    except ValueError as exc:
        if "blocked" in str(exc).lower():
            raise
        # hostname — resolve all A/AAAA
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as err:
            raise ValueError(f"Could not resolve host: {host}") from err
        if not infos:
            raise ValueError(f"Could not resolve host: {host}")
        for info in infos:
            addr = info[4][0]
            if _is_blocked_ip(ipaddress.ip_address(addr)):
                raise ValueError("URL resolves to a blocked address")
        return url


async def fetch_url_content(url: str) -> dict:
    """Fetch URL and return title, content, char_count. Blocks SSRF targets."""
    assert_public_http_url(url)

    async with httpx.AsyncClient(follow_redirects=False, timeout=20.0) as client:
        current = url
        response = None
        for _ in range(5):
            assert_public_http_url(current)
            response = await client.get(current, headers={"User-Agent": USER_AGENT})
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise ValueError("Redirect missing Location header")
                current = str(httpx.URL(current).join(location))
                continue
            response.raise_for_status()
            break
        else:
            raise ValueError("Too many redirects")

    assert response is not None
    raw = response.content
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError(f"Response exceeds maximum size ({MAX_RESPONSE_BYTES} bytes)")

    content_type = response.headers.get("content-type", "")
    text_body = raw.decode(response.encoding or "utf-8", errors="replace")
    if "html" not in content_type.lower():
        text = text_body[:MAX_EXTRACTED_CHARS]
        return {"title": url, "content": text, "char_count": len(text)}

    title, text = extract_text_from_html(text_body)
    return {"title": title, "content": text, "char_count": len(text)}
