"""URL content extraction for diagram context — SSRF-safe fetch + HTML text."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

MAX_EXTRACTED_CHARS = 12000
MAX_RESPONSE_BYTES = 2_000_000
USER_AGENT = "drawio-agent/1.0 (+https://github.com/drawio-agent)"
ALLOWED_PORTS = {80, 443}
_FETCH_TIMEOUT = httpx.Timeout(20.0)


def extract_text_from_html(html_content: str) -> tuple[str, str]:
    """Return (title, plain text) from HTML."""
    soup = BeautifulSoup(html_content or "", "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title else "Untitled"
    if not title:
        title = "Untitled"
    if soup.title:
        soup.title.decompose()
    text = " ".join(soup.get_text(separator=" ", strip=True).split())
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


def resolve_public_ip(hostname: str, port: int) -> str:
    """Resolve hostname and return one public IP; reject private/link-local/metadata."""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ValueError("URL resolves to a blocked address")
        return str(literal)

    try:
        infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as err:
        raise ValueError(f"Could not resolve host: {hostname}") from err
    if not infos:
        raise ValueError(f"Could not resolve host: {hostname}")

    public_ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        ip = ipaddress.ip_address(addr)
        if _is_blocked_ip(ip):
            continue
        public_ips.append(addr)
    if not public_ips:
        raise ValueError("URL resolves to a blocked address")
    return public_ips[0]


def assert_public_http_url(url: str) -> tuple[str, str, int, str]:
    """Validate scheme/port; return (url, hostname, port, pinned_ip)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise ValueError(f"Port {port} is not allowed")
    ip = resolve_public_ip(parsed.hostname, port)
    return url, parsed.hostname, port, ip


async def _http_get_pinned(
    *,
    scheme: str,
    hostname: str,
    port: int,
    ip: str,
    path: str,
    query: str,
) -> tuple[int, dict[str, str], bytes]:
    """GET via pinned IP with Host/SNI = original hostname (closes DNS rebinding TOCTOU)."""
    target = path or "/"
    if query:
        target = f"{target}?{query}"

    # Connect to the pinned IP (never re-resolve). Host header + SNI stay on the
    # original hostname so TLS certificates and virtual hosts still work.
    try:
        addr = ipaddress.ip_address(ip)
        host_for_url = f"[{ip}]" if addr.version == 6 else ip
    except ValueError:
        host_for_url = ip
    url = f"{scheme}://{host_for_url}:{port}{target}"
    headers = {
        "Host": hostname,
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
    }
    extensions = {"sni_hostname": hostname} if scheme == "https" else {}

    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=_FETCH_TIMEOUT,
        trust_env=False,  # ignore HTTP(S)_PROXY — proxy could bypass SSRF pin
    ) as client:
        async with client.stream("GET", url, headers=headers, extensions=extensions) as response:
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ValueError(
                        f"Response exceeds maximum size ({MAX_RESPONSE_BYTES} bytes)"
                    )
                chunks.append(chunk)
            body = b"".join(chunks)
            headers_out = {k.lower(): v for k, v in response.headers.items()}
            return response.status_code, headers_out, body


async def fetch_url_content(url: str) -> dict:
    """Fetch URL and return title, content, char_count. Blocks SSRF / DNS rebinding."""
    current = url
    response_url = url
    status = 0
    headers: dict[str, str] = {}
    body = b""

    for _ in range(5):
        _, hostname, port, ip = assert_public_http_url(current)
        parsed = urlparse(current)
        status, headers, body = await _http_get_pinned(
            scheme=parsed.scheme,
            hostname=hostname,
            port=port,
            ip=ip,
            path=parsed.path or "/",
            query=parsed.query,
        )
        response_url = current
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise ValueError("Redirect missing Location header")
            current = urljoin(f"{parsed.scheme}://{hostname}{parsed.path or '/'}", location)
            continue
        if status >= 400:
            raise ValueError(f"HTTP {status} fetching URL")
        break
    else:
        raise ValueError("Too many redirects")

    content_type = headers.get("content-type", "")
    text_body = body.decode("utf-8", errors="replace")
    if "html" not in content_type.lower():
        text = text_body[:MAX_EXTRACTED_CHARS]
        return {"title": response_url, "content": text, "char_count": len(text)}

    title, text = extract_text_from_html(text_body)
    return {"title": title, "content": text, "char_count": len(text)}
