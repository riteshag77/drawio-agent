"""Cursor Cloud Agents client tests."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.adapters.outbound.llm.cursor_client import _extract_json_object, call_cursor_agents
from app.adapters.outbound.llm.providers import ProviderSpec
from app.domain.entities import XmlDiagram


class CursorClientTests(unittest.TestCase):
    def test_extract_json_object_from_fence(self) -> None:
        text = 'Here is output:\n```json\n{"xml": "<mxfile></mxfile>", "assistant_message": "ok", "suggestions": []}\n```'
        data = _extract_json_object(text)
        self.assertIn("xml", data)

    def test_call_cursor_agents_parses_finished_run(self) -> None:
        spec = ProviderSpec(
            key="cursor",
            client_class=None,
            default_model="auto",
            api_key_env="CURSOR_API_KEY",
            kind="cursor_agents",
            base_url_env="CURSOR_API_BASE_URL",
            base_url_default="https://api.cursor.com",
            structured_output="none",
        )
        payload = XmlDiagram(xml="<mxfile></mxfile>", assistant_message="done", suggestions=[]).model_dump_json()

        create_resp = MagicMock()
        create_resp.status_code = 200
        create_resp.json.return_value = {
            "agent": {"id": "bc-1", "latestRunId": "run-1"},
            "run": {"id": "run-1", "status": "CREATING"},
        }

        poll_resp = MagicMock()
        poll_resp.status_code = 200
        poll_resp.json.return_value = {"status": "FINISHED", "result": payload}

        delete_resp = MagicMock()
        delete_resp.status_code = 200

        client = MagicMock()
        client.post.return_value = create_resp
        client.get.return_value = poll_resp
        client.delete.return_value = delete_resp
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)

        with patch.dict("os.environ", {"CURSOR_API_KEY": "test-key"}, clear=False), \
             patch("app.adapters.outbound.llm.cursor_client.httpx.Client", return_value=client), \
             patch("app.adapters.outbound.llm.cursor_client.time.sleep"):
            result = call_cursor_agents("system", "human", spec, XmlDiagram)

        self.assertEqual(result.xml, "<mxfile></mxfile>")
        client.post.assert_called_once()
        client.get.assert_called()
        client.delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
