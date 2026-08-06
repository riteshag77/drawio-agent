"""POST /analyze — re-run WAF/compliance against an already-generated
diagram, split out of the full /chat pipeline so turning the toggle on
after a diagram exists shows results immediately instead of only on the
next chat turn."""

from __future__ import annotations

import unittest

_LAMBDA_XML = (
    '<mxfile><diagram name="Network"><mxGraphModel><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    '<mxCell id="a" style="shape=mxgraph.aws4.resourceIcon;'
    'resIcon=mxgraph.aws4.lambda;" vertex="1" parent="1">'
    '<mxGeometry width="78" height="78" as="geometry"/></mxCell>'
    '</root></mxGraphModel></diagram></mxfile>'
)


class AnalyzeRouteTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_empty_xml_is_a_400(self) -> None:
        client = self._client()
        res = client.post("/analyze", json={"xml": "   "})
        self.assertEqual(res.status_code, 400)

    def test_well_architected_only(self) -> None:
        client = self._client()
        res = client.post("/analyze", json={
            "xml": _LAMBDA_XML, "platform": "aws", "provider": "",
            "well_architected": True, "compliance": False,
        })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "success")
        self.assertIsNotNone(body["well_architected"])
        self.assertIsNone(body["compliance"])
        # A lone Lambda leaf is missing a WAF/KMS/secrets-manager — same rule-based
        # detection the full /chat pipeline runs on, just against this xml directly.
        self.assertEqual(body["well_architected"]["overall_status"], "fail")

    def test_compliance_only_skips_llm_enhancement_with_no_provider(self) -> None:
        client = self._client()
        res = client.post("/analyze", json={
            "xml": _LAMBDA_XML, "platform": "aws", "provider": "",
            "well_architected": False, "compliance": True,
            "compliance_frameworks": ["hipaa"],
        })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIsNone(body["well_architected"])
        self.assertIsNotNone(body["compliance"])
        self.assertIn("hipaa", body["compliance"]["frameworks"])
        self.assertFalse(body["compliance"]["llm_enhanced"])

    def test_both_flags_off_still_returns_success_with_null_results(self) -> None:
        client = self._client()
        res = client.post("/analyze", json={
            "xml": _LAMBDA_XML, "well_architected": False, "compliance": False,
        })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "success")
        self.assertIsNone(body["well_architected"])
        self.assertIsNone(body["compliance"])
        self.assertIsNone(body["suggestions"])

    def test_suggestions_off_by_default(self) -> None:
        """suggestions defaults to False — a caller that doesn't ask for it
        (e.g. the existing WAF/compliance-only re-check) must not pay for an
        LLM call it never requested."""
        client = self._client()
        res = client.post("/analyze", json={
            "xml": _LAMBDA_XML, "well_architected": False, "compliance": False,
        })
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["suggestions"])

    def test_suggestions_reruns_against_the_given_xml(self) -> None:
        """Turning the Suggestions toggle on after a diagram already exists
        should show results immediately — same reasoning as the
        WAF/Compliance re-check, same route, just a different flag."""
        from unittest.mock import patch

        from app.domain.entities.xml_diagram import DiagramSuggestion

        client = self._client()
        fake = [DiagramSuggestion(text="Add a DLQ for the queue.", category="network")]
        with patch(
            "app.adapters.inbound.web.routes.generate_followup_suggestions_for_xml",
            return_value=fake,
        ) as mocked:
            res = client.post("/analyze", json={
                "xml": _LAMBDA_XML, "well_architected": False, "compliance": False,
                "suggestions": True, "view": "network", "human_prompt": "checkout service",
                "applied_suggestions": ["already applied this one"],
            })
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(len(body["suggestions"]), 1)
        self.assertEqual(body["suggestions"][0]["text"], "Add a DLQ for the queue.")
        _, kwargs = mocked.call_args
        self.assertEqual(kwargs["view"], "network")
        self.assertEqual(kwargs["user_request"], "checkout service")
        self.assertEqual(kwargs["applied_suggestions"], ["already applied this one"])

    def test_suggestions_failure_is_a_500_not_a_silent_empty_list(self) -> None:
        from unittest.mock import patch

        client = self._client()
        with patch(
            "app.adapters.inbound.web.routes.generate_followup_suggestions_for_xml",
            side_effect=RuntimeError("boom"),
        ):
            res = client.post("/analyze", json={
                "xml": _LAMBDA_XML, "well_architected": False, "compliance": False,
                "suggestions": True,
            })
        self.assertEqual(res.status_code, 500)


if __name__ == "__main__":
    unittest.main()
