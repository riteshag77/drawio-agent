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

    def test_model_override_reaches_the_provider_context(self) -> None:
        """The Validation Provider picker offers the same multi-model
        optgroups as the chat composer (e.g. NVIDIA NIM) — `model` must
        actually reach provider resolution, not just get silently accepted
        and dropped by the schema."""
        from app.adapters.outbound.llm import providers as providers_module

        client = self._client()
        seen: dict[str, object] = {}

        def _fake_run(image_b64, xml, provider, logger=None):
            seen["model"] = providers_module._model_override_ctx.get()
            return VisualQaResult(passed=True, issues=[], summary="ok")

        with patch("app.adapters.inbound.web.routes.run_visual_qa_llm", side_effect=_fake_run):
            res = client.post("/diagram/validate-visual", json={
                "image_base64": _PNG_B64, "xml": _XML, "provider": "nvidia", "model": "some-model-id",
            })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(seen["model"], "some-model-id")
        # The override must not leak into a later request on the same worker.
        self.assertIsNone(providers_module._model_override_ctx.get())

    def test_custom_provider_routes_through_the_custom_key(self) -> None:
        """A BYOK selection must flip `provider` to the CUSTOM_PROVIDER_KEY
        sentinel and scope the payload via the context var — same mechanism
        _run_chat already uses for /chat, so Visual QA can hit a private/local
        endpoint exactly like a normal chat turn can."""
        from app.adapters.outbound.llm import providers as providers_module

        client = self._client()
        seen: dict[str, object] = {}

        def _fake_run(image_b64, xml, provider, logger=None):
            seen["provider"] = provider
            seen["custom_payload"] = providers_module._custom_provider_ctx.get()
            return VisualQaResult(passed=True, issues=[], summary="ok")

        with patch("app.adapters.inbound.web.routes.run_visual_qa_llm", side_effect=_fake_run):
            res = client.post("/diagram/validate-visual", json={
                "image_base64": _PNG_B64, "xml": _XML,
                "custom_provider": {
                    "label": "My Ollama", "client": "openai", "api_key": "not-needed-for-local",
                    "base_url": "http://localhost:11434/v1", "model": "llama3",
                },
            })
        self.assertEqual(res.status_code, 200)
        self.assertEqual(seen["provider"], providers_module.CUSTOM_PROVIDER_KEY)
        self.assertEqual(seen["custom_payload"]["base_url"], "http://localhost:11434/v1")
        self.assertIsNone(providers_module._custom_provider_ctx.get())


if __name__ == "__main__":
    unittest.main()
