"""POST /diagram/validate-visual — real screenshot-based vision QA endpoint."""

from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

from app.domain.entities.visual_qa import VisualQaIssue, VisualQaResult

_XML = '<mxfile><diagram><mxGraphModel><root><mxCell id="0"/></root></mxGraphModel></diagram></mxfile>'
_PNG_B64 = base64.b64encode(b"not-a-real-png").decode()


class ValidateVisualRouteTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_passing_result(self) -> None:
        client = self._client()
        fake = VisualQaResult(passed=True, issues=[], summary="Looks clean.")
        with patch("app.adapters.inbound.web.routes.run_visual_qa_llm", return_value=fake) as mocked:
            res = client.post("/diagram/validate-visual", json={
                "image_base64": _PNG_B64, "xml": _XML, "provider": "claude",
            })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "success")
        self.assertTrue(body["passed"])
        self.assertEqual(body["issues"], [])
        self.assertEqual(body["summary"], "Looks clean.")
        mocked.assert_called_once()

    def test_failing_result_with_issues(self) -> None:
        client = self._client()
        fake = VisualQaResult(
            passed=False,
            issues=[VisualQaIssue(category="overlap", severity="critical", description="Two boxes overlap.")],
            summary="Overlap detected.",
        )
        with patch("app.adapters.inbound.web.routes.run_visual_qa_llm", return_value=fake):
            res = client.post("/diagram/validate-visual", json={
                "image_base64": _PNG_B64, "xml": _XML,
            })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["passed"])
        self.assertEqual(len(body["issues"]), 1)
        self.assertEqual(body["issues"][0]["severity"], "critical")

    def test_llm_failure_is_a_500_with_logs(self) -> None:
        client = self._client()
        with patch(
            "app.adapters.inbound.web.routes.run_visual_qa_llm",
            side_effect=RuntimeError("provider unavailable"),
        ):
            res = client.post("/diagram/validate-visual", json={
                "image_base64": _PNG_B64, "xml": _XML,
            })
        self.assertEqual(res.status_code, 500)
        body = res.json()
        self.assertEqual(body["status"], "error")
        self.assertIn("logs", body)

    def test_provider_defaults_when_omitted(self) -> None:
        client = self._client()
        fake = VisualQaResult(passed=True, issues=[], summary="ok")
        with patch("app.adapters.inbound.web.routes.run_visual_qa_llm", return_value=fake) as mocked:
            res = client.post("/diagram/validate-visual", json={"image_base64": _PNG_B64, "xml": _XML})
        self.assertEqual(res.status_code, 200)
        args, _ = mocked.call_args
        self.assertTrue(args[2])  # provider positional arg is non-empty


if __name__ == "__main__":
    unittest.main()
