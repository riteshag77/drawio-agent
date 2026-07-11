"""URL content extraction for diagram context — port of next-ai-draw-io lib/url-utils.ts"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import ssl
from html import unescape
from urllib.parse import urlparse

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


def _decode_chunked(body: bytes) -> bytes:
    out = bytearray()
    pos = 0
    while pos < len(body):
        nl = body.find(b"\r\n", pos)
        if nl < 0:
            break
        size_line = body[pos:nl].split(b";", 1)[0].strip()
        try:
            size = int(size_line, 16)
        except ValueError as exc:
            raise ValueError("Invalid chunked transfer encoding") from exc
        pos = nl + 2
        if size == 0:
            break
        out.extend(body[pos : pos + size])
        pos += size + 2  # skip chunk data + CRLF
    return bytes(out)


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

    ssl_ctx: ssl.SSLContext | None = None
    server_hostname: str | None = None
    if scheme == "https":
        ssl_ctx = ssl.create_default_context()
        server_hostname = hostname

    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(ip, port, ssl=ssl_ctx, server_hostname=server_hostname),
        timeout=20.0,
    )
    try:
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {hostname}\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(request.encode("ascii", errors="ignore"))
        await writer.drain()

        raw = bytearray()
        while len(raw) <= MAX_RESPONSE_BYTES:
            chunk = await asyncio.wait_for(reader.read(65536), timeout=20.0)
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError(f"Response exceeds maximum size ({MAX_RESPONSE_BYTES} bytes)")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    header_end = raw.find(b"\r\n\r\n")
    if header_end < 0:
        raise ValueError("Invalid HTTP response")
    header_blob = raw[:header_end].decode("iso-8859-1", errors="replace")
    body = bytes(raw[header_end + 4 :])

    lines = header_blob.split("\r\n")
    status_line = lines[0] if lines else ""
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError(f"Invalid HTTP status line: {status_line!r}")
    status = int(parts[1])

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = _decode_chunked(body)

    return status, headers, body


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
            # Absolute or relative Location
            if location.startswith("http://") or location.startswith("https://"):
                current = location
            else:
                base = f"{parsed.scheme}://{hostname}"
                if not location.startswith("/"):
                    location = "/" + location
                current = base + location
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
