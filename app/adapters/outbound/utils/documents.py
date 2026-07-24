"""Document/image/diagram attachment extraction — turns an uploaded file into
extractable text, a base64 vision payload, or validated draw.io XML."""

from __future__ import annotations

import base64
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from io import BytesIO

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024  # 15 MB
MAX_TEXT_CHARS = 12_000
# Align with ChatRequest.current_xml max_length — full diagrams must not be
# truncated like plain-text context (12k), or Edit mode loses the architecture.
MAX_DIAGRAM_CHARS = 1_500_000

TEXT_EXTENSIONS = {".txt", ".md", ".json", ".yaml", ".yml"}
DIAGRAM_EXTENSIONS = {".drawio", ".xml"}

_SIGNATURES: dict[bytes, str] = {
    b"%PDF-": "application/pdf",
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}

# Signatures for executables/archives that must never be accepted, no matter what
# extension or content-type the client claims — these aren't parseable as any
# supported document type and have no legitimate reason to arrive here.
_BLOCKED_SIGNATURES: dict[bytes, str] = {
    b"MZ": "a Windows executable",
    b"\x7fELF": "a Linux executable",
    b"\xca\xfe\xba\xbe": "a Mach-O/Java class binary",
    b"\xfe\xed\xfa": "a Mach-O binary",
    b"PK\x03\x04": (
        "a zip/Office archive (compressed .drawio and docx/xlsx/pptx are not supported — "
        "export an uncompressed .drawio / .xml from draw.io instead)"
    ),
    b"#!": "a script",
}

# Lightweight PDF active-content markers. Not a substitute for a real AV engine —
# this only catches the common "PDF carries an auto-run/launch/JS payload" shape,
# which is the main malicious pattern relevant to a file we never open in a viewer.
_PDF_ACTIVE_CONTENT_MARKERS = (b"/JavaScript", b"/JS", b"/OpenAction", b"/Launch", b"/EmbeddedFile", b"/AA")

_COMPRESSED_ATTR = re.compile(r'compressed\s*=\s*["\']true["\']', re.IGNORECASE)


@dataclass
class DocumentResult:
    kind: str  # "text" | "vision" | "diagram" | "error"
    filename: str
    content: str | None = None
    truncated: bool = False
    mime: str | None = None
    data_b64: str | None = None
    message: str | None = None


def _sniff_mime(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    for signature, mime in _SIGNATURES.items():
        if data.startswith(signature):
            return mime
    return None


def _is_probably_text(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in TEXT_EXTENSIONS)


def _is_probably_diagram(filename: str) -> bool:
    lower = filename.lower()
    return any(lower.endswith(ext) for ext in DIAGRAM_EXTENSIONS)


def _looks_like_drawio_bytes(data: bytes) -> bool:
    head = data.lstrip()[:200].lower()
    return head.startswith(b"<?xml") or head.startswith(b"<mxfile") or head.startswith(b"<mxgraphmodel")


def extract_document(filename: str, data: bytes) -> DocumentResult:
    """Classify and extract an uploaded file. Never raises — every failure mode
    (corrupt, encrypted, oversized, unsupported, mismatched extension) becomes a
    ``kind="error"`` result so one bad attachment can't break the rest of the batch."""
    if not data:
        return DocumentResult(kind="error", filename=filename, message="File is empty.")
    if len(data) > MAX_ATTACHMENT_BYTES:
        limit_mb = MAX_ATTACHMENT_BYTES // 1_048_576
        return DocumentResult(
            kind="error", filename=filename,
            message=f"File is too large ({len(data) / 1_048_576:.1f} MB) — the limit is {limit_mb} MB.",
        )

    for signature, label in _BLOCKED_SIGNATURES.items():
        if data.startswith(signature):
            return DocumentResult(kind="error", filename=filename, message=f"'{filename}' looks like {label}.")

    sniffed = _sniff_mime(data)

    if sniffed == "application/pdf":
        return _extract_pdf(filename, data)
    if sniffed and sniffed.startswith("image/"):
        return DocumentResult(kind="vision", filename=filename, mime=sniffed, data_b64=_b64(data))

    if _is_probably_diagram(filename) or (_looks_like_drawio_bytes(data) and not _is_probably_text(filename)):
        if sniffed is not None:
            return DocumentResult(
                kind="error", filename=filename,
                message=f"'{filename}' looks like a {sniffed} file, not draw.io XML.",
            )
        return _extract_diagram(filename, data)

    if _is_probably_text(filename):
        if sniffed is not None:
            return DocumentResult(
                kind="error", filename=filename,
                message=f"'{filename}' looks like a {sniffed} file, not text — its contents don't match its extension.",
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return DocumentResult(kind="error", filename=filename, message=f"Could not read '{filename}' as text (not valid UTF-8).")
        return _text_result(filename, text)

    return DocumentResult(
        kind="error", filename=filename,
        message=(
            f"Unsupported file type for '{filename}'. Attach a PDF, a plain text/markdown/JSON/YAML "
            "file, an image (PNG/JPEG/GIF/WEBP), or a draw.io / XML diagram (.drawio, .xml)."
        ),
    )


def _extract_diagram(filename: str, data: bytes) -> DocumentResult:
    """Validate and normalize an uncompressed draw.io / mxGraph XML attachment."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return DocumentResult(
            kind="error", filename=filename,
            message=f"Could not read '{filename}' as XML (not valid UTF-8).",
        )

    if len(text) > MAX_DIAGRAM_CHARS:
        return DocumentResult(
            kind="error", filename=filename,
            message=(
                f"'{filename}' is too large as diagram XML "
                f"({len(text):,} chars; limit {MAX_DIAGRAM_CHARS:,})."
            ),
        )

    sample = text[:8000]
    if _COMPRESSED_ATTR.search(sample) and "<mxCell" not in text:
        return DocumentResult(
            kind="error", filename=filename,
            message=(
                f"'{filename}' is a compressed draw.io diagram. Export uncompressed "
                ".drawio / .xml from draw.io (File → Export as → XML) and try again."
            ),
        )

    from app.adapters.outbound.diagram.xml_tools import (
        choose_fixed_xml,
        coerce_drawio_xml,
        ensure_diagram_document,
        validate_and_fix_xml,
    )

    try:
        coerced = coerce_drawio_xml(text)
        document = ensure_diagram_document(coerced, page_name=filename.rsplit(".", 1)[0] or "Architecture")
        check = validate_and_fix_xml(document)
        normalized = choose_fixed_xml(document, check)
        ET.fromstring(normalized)
    except ET.ParseError as exc:
        return DocumentResult(
            kind="error", filename=filename,
            message=f"'{filename}' is not well-formed XML ({exc}).",
        )
    except Exception as exc:
        return DocumentResult(
            kind="error", filename=filename,
            message=f"Could not parse '{filename}' as a draw.io diagram: {exc}",
        )

    if "<mxCell" not in normalized:
        return DocumentResult(
            kind="error", filename=filename,
            message=f"'{filename}' has no diagram cells (mxCell) after parsing.",
        )
    if 'vertex="1"' not in normalized and 'edge="1"' not in normalized:
        # Roots 0/1 alone are not a usable architecture diagram.
        cell_count = normalized.count("<mxCell")
        if cell_count <= 2:
            return DocumentResult(
                kind="error", filename=filename,
                message=f"'{filename}' does not contain a usable diagram (no shapes or edges).",
            )

    return DocumentResult(
        kind="diagram",
        filename=filename,
        content=normalized,
        mime="application/xml",
    )


def _text_result(filename: str, text: str) -> DocumentResult:
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]
    return DocumentResult(kind="text", filename=filename, content=text, truncated=truncated)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _extract_pdf(filename: str, data: bytes) -> DocumentResult:
    """Validate a PDF (malicious content, corruption, encryption) and hand it to
    the LLM's native document understanding — pypdf is used only for validation
    here, never for content extraction, since extract_text() silently drops any
    embedded images/diagrams that the model would otherwise be able to see."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.errors import PdfReadError

    if any(marker in data for marker in _PDF_ACTIVE_CONTENT_MARKERS):
        return DocumentResult(
            kind="error", filename=filename,
            message=f"'{filename}' contains embedded scripts/actions and was rejected for safety.",
        )

    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError:
        return DocumentResult(kind="error", filename=filename, message=f"Could not read '{filename}' — the PDF appears to be corrupted.")
    except Exception as exc:
        return DocumentResult(kind="error", filename=filename, message=f"Could not read '{filename}': {exc}")

    decrypted = False
    if reader.is_encrypted:
        try:
            decrypt_result = reader.decrypt("")
        except Exception:
            decrypt_result = 0
        if not decrypt_result:
            return DocumentResult(kind="error", filename=filename, message=f"'{filename}' is password-protected and can't be read.")
        decrypted = True

    try:
        page_count = len(reader.pages)
    except Exception as exc:
        return DocumentResult(kind="error", filename=filename, message=f"Could not read '{filename}': {exc}")
    if page_count == 0:
        return DocumentResult(kind="error", filename=filename, message=f"'{filename}' has no pages.")

    if decrypted:
        # The original bytes are still encrypted at rest — the LLM provider has no
        # password either, so re-serialize the already-decrypted content instead
        # of sending the raw (still-locked) upload.
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        buffer = BytesIO()
        writer.write(buffer)
        final_bytes = buffer.getvalue()
    else:
        final_bytes = data

    return DocumentResult(kind="vision", filename=filename, mime="application/pdf", data_b64=_b64(final_bytes))
