"""Tests for the attachment extraction adapter (app.adapters.outbound.utils.documents)."""

from __future__ import annotations

import unittest
from io import BytesIO

from app.adapters.outbound.utils.documents import MAX_ATTACHMENT_BYTES, extract_document


def _build_pdf(content_stream: bytes, encrypt: bool = False) -> bytes:
    """Hand-build a minimal single-page PDF with the given content stream —
    no reportlab/fpdf dependency needed just for test fixtures."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 300 300] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content_stream), content_stream),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += (f"{i} 0 obj\n").encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(out)


TEXT_PDF = _build_pdf(b"BT /F1 12 Tf 20 100 Td (Payment service talks to Order DB via REST) Tj ET")
BLANK_PDF = _build_pdf(b"")

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000031e2b96500000015"
    "494441545801010a0004fffbef000000010001008d8a0b380000000049454e44ae426082"
)


class TextExtractionTests(unittest.TestCase):
    def test_plain_text_file(self) -> None:
        result = extract_document("notes.txt", b"Payment service -> Order DB via REST.")
        self.assertEqual(result.kind, "text")
        self.assertFalse(result.truncated)
        self.assertIn("Order DB", result.content)

    def test_markdown_and_yaml_extensions_are_treated_as_text(self) -> None:
        for name in ("notes.md", "config.yaml", "config.yml", "data.json"):
            result = extract_document(name, b"hello world")
            self.assertEqual(result.kind, "text", name)

    def test_text_truncated_over_cap(self) -> None:
        big = ("x" * 20_000).encode("utf-8")
        result = extract_document("big.txt", big)
        self.assertEqual(result.kind, "text")
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.content), 12_000)

    def test_invalid_utf8_text_file_is_an_error(self) -> None:
        result = extract_document("bad.txt", b"\xff\xfe\x00\x01not utf8")
        self.assertEqual(result.kind, "error")
        self.assertIn("not valid UTF-8", result.message)

    def test_empty_file_is_an_error(self) -> None:
        result = extract_document("empty.txt", b"")
        self.assertEqual(result.kind, "error")

    def test_image_bytes_with_text_extension_are_read_via_sniffing(self) -> None:
        # A PNG's real bytes claiming to be a .txt file — content sniffing wins over
        # the claimed extension, so this is still usefully read as an image.
        result = extract_document("sneaky.txt", PNG_BYTES)
        self.assertEqual(result.kind, "vision")
        self.assertEqual(result.mime, "image/png")

    def test_non_image_binary_with_text_extension_is_rejected(self) -> None:
        # Bytes that don't decode as UTF-8 and aren't a recognized binary format.
        result = extract_document("mystery.txt", b"\x01\x02\x03\xff\xfe garbage \x00\x00")
        self.assertEqual(result.kind, "error")

    def test_unsupported_extension_is_rejected(self) -> None:
        result = extract_document("spreadsheet.xlsx", b"not really an xlsx")
        self.assertEqual(result.kind, "error")


class DiagramExtractionTests(unittest.TestCase):
    MINIMAL = (
        b'<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
        b"<mxGraphModel><root>"
        b'<mxCell id="0"/><mxCell id="1" parent="0"/>'
        b'<mxCell id="svc" value="Lambda" style="html=1;" vertex="1" parent="1">'
        b'<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
        b"</root></mxGraphModel></diagram></mxfile>"
    )

    def test_drawio_extension_is_accepted(self) -> None:
        result = extract_document("arch.drawio", self.MINIMAL)
        self.assertEqual(result.kind, "diagram")
        self.assertIn("mxCell", result.content or "")
        self.assertIn("Lambda", result.content or "")

    def test_xml_extension_is_accepted(self) -> None:
        result = extract_document("arch.xml", self.MINIMAL)
        self.assertEqual(result.kind, "diagram")

    def test_malformed_xml_is_rejected(self) -> None:
        result = extract_document("bad.drawio", b"<mxfile><diagram><not-closed>")
        self.assertEqual(result.kind, "error")
        self.assertTrue(result.message)

    def test_empty_roots_only_rejected(self) -> None:
        bare = (
            b'<mxfile host="app.diagrams.net"><diagram name="A" id="d1">'
            b"<mxGraphModel><root>"
            b'<mxCell id="0"/><mxCell id="1" parent="0"/>'
            b"</root></mxGraphModel></diagram></mxfile>"
        )
        result = extract_document("empty.drawio", bare)
        self.assertEqual(result.kind, "error")
        self.assertIn("usable diagram", result.message or "")

    def test_compressed_without_cells_rejected(self) -> None:
        compressed = (
            b'<mxfile host="app.diagrams.net"><diagram id="d1" name="A">'
            b'<mxGraphModel compressed="true"><root/></mxGraphModel>'
            b"</diagram></mxfile>"
        )
        result = extract_document("zippy.drawio", compressed)
        self.assertEqual(result.kind, "error")
        self.assertIn("compressed", (result.message or "").lower())

    def test_zip_signature_drawio_rejected(self) -> None:
        result = extract_document("packed.drawio", b"PK\x03\x04" + b"\x00" * 32)
        self.assertEqual(result.kind, "error")


class SizeAndSecurityGuardrailTests(unittest.TestCase):
    def test_oversized_file_is_rejected(self) -> None:
        data = b"a" * (MAX_ATTACHMENT_BYTES + 1)
        result = extract_document("huge.txt", data)
        self.assertEqual(result.kind, "error")
        self.assertIn("too large", result.message)

    def test_windows_executable_disguised_as_text_is_rejected(self) -> None:
        result = extract_document("totally-a-doc.txt", b"MZ" + b"\x90" * 64)
        self.assertEqual(result.kind, "error")
        self.assertIn("executable", result.message)

    def test_zip_archive_disguised_as_pdf_is_rejected(self) -> None:
        result = extract_document("report.pdf", b"PK\x03\x04" + b"\x00" * 64)
        self.assertEqual(result.kind, "error")

    def test_script_shebang_is_rejected(self) -> None:
        result = extract_document("script.txt", b"#!/bin/sh\nrm -rf /\n")
        self.assertEqual(result.kind, "error")


class ImageExtractionTests(unittest.TestCase):
    def test_png_image_returns_vision_kind(self) -> None:
        result = extract_document("diagram.png", PNG_BYTES)
        self.assertEqual(result.kind, "vision")
        self.assertEqual(result.mime, "image/png")
        self.assertTrue(result.data_b64)


class PdfExtractionTests(unittest.TestCase):
    def test_pdf_with_text_layer_is_still_sent_natively(self) -> None:
        # Even a PDF with a perfectly good text layer is submitted to the LLM as
        # a native document — pypdf's extract_text() would silently drop any
        # embedded diagrams/images on the page, so we never rely on it for content.
        result = extract_document("architecture.pdf", TEXT_PDF)
        self.assertEqual(result.kind, "vision")
        self.assertEqual(result.mime, "application/pdf")
        self.assertTrue(result.data_b64)

    def test_blank_scanned_pdf_is_sent_natively(self) -> None:
        result = extract_document("scanned.pdf", BLANK_PDF)
        self.assertEqual(result.kind, "vision")
        self.assertEqual(result.mime, "application/pdf")
        self.assertTrue(result.data_b64)

    def test_corrupt_pdf_is_an_error(self) -> None:
        result = extract_document("corrupt.pdf", b"%PDF-1.4\nnot a real pdf body")
        self.assertEqual(result.kind, "error")

    def test_pdf_with_active_content_marker_is_rejected(self) -> None:
        malicious = _build_pdf(b"") + b"\n/OpenAction /Launch (calc.exe)"
        result = extract_document("bad.pdf", malicious)
        self.assertEqual(result.kind, "error")
        self.assertIn("embedded scripts", result.message)

    def test_empty_password_encrypted_pdf_is_decrypted_before_sending(self) -> None:
        import base64

        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(BytesIO(TEXT_PDF))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(user_password="", owner_password="s3cret")
        buffer = BytesIO()
        writer.write(buffer)
        encrypted_bytes = buffer.getvalue()

        result = extract_document("encrypted.pdf", encrypted_bytes)
        self.assertEqual(result.kind, "vision")
        self.assertEqual(result.mime, "application/pdf")
        self.assertTrue(result.data_b64)

        sent_bytes = base64.b64decode(result.data_b64)
        sent_reader = PdfReader(BytesIO(sent_bytes))
        self.assertFalse(sent_reader.is_encrypted)

    def test_password_protected_pdf_is_an_error(self) -> None:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(BytesIO(TEXT_PDF))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt(user_password="hunter2", owner_password="s3cret")
        buffer = BytesIO()
        writer.write(buffer)

        result = extract_document("locked.pdf", buffer.getvalue())
        self.assertEqual(result.kind, "error")
        self.assertIn("password-protected", result.message)


if __name__ == "__main__":
    unittest.main()
