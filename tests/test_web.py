"""Tests for SSRF-safe URL fetch helpers and HTML extraction."""

from __future__ import annotations

import unittest

from app.adapters.outbound.utils.web import (
    assert_public_http_url,
    extract_text_from_html,
)


class ExtractTextFromHtmlTests(unittest.TestCase):
    def test_strips_script_and_style(self) -> None:
        html = """
        <html><head><title> Demo Page </title>
        <style>.x { color: red }</style>
        <script>alert(1)</script>
        </head><body><h1>Hello</h1><p>World &amp; friends</p>
        <script>evil()</script>
        </body></html>
        """
        title, text = extract_text_from_html(html)
        self.assertEqual(title, "Demo Page")
        self.assertIn("Hello", text)
        self.assertIn("World & friends", text)
        self.assertNotIn("alert", text)
        self.assertNotIn("evil", text)
        self.assertNotIn("color: red", text)

    def test_untitled_when_missing_title(self) -> None:
        title, text = extract_text_from_html("<html><body><p>Only body</p></body></html>")
        self.assertEqual(title, "Untitled")
        self.assertIn("Only body", text)

    def test_truncates_long_text(self) -> None:
        body = "word " * 5000
        title, text = extract_text_from_html(f"<html><head><title>T</title></head><body>{body}</body></html>")
        self.assertEqual(title, "T")
        self.assertTrue(text.endswith("…"))
        self.assertLessEqual(len(text), 12001)


class AssertPublicHttpUrlTests(unittest.TestCase):
    def test_rejects_non_http(self) -> None:
        with self.assertRaises(ValueError):
            assert_public_http_url("ftp://example.com/x")

    def test_rejects_blocked_literal(self) -> None:
        with self.assertRaises(ValueError):
            assert_public_http_url("http://127.0.0.1/")
        with self.assertRaises(ValueError):
            assert_public_http_url("http://169.254.169.254/latest/meta-data")
        with self.assertRaises(ValueError):
            assert_public_http_url("http://10.0.0.1/")

    def test_rejects_odd_port(self) -> None:
        with self.assertRaises(ValueError):
            assert_public_http_url("http://example.com:8080/")


if __name__ == "__main__":
    unittest.main()
