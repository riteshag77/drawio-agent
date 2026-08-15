"""POST /chat history recording — including the "simple" (non-cloud) intent
path, which previously stored Mermaid source in the `xml` column."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.adapters.outbound import history_store
from app.adapters.outbound.config import env_config
from app.domain.entities import XmlDiagram


class _TempHistory:
    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self._saved = {}
        env = {
            "DIAGRAM_HISTORY_ENABLED": "true",
            "DIAGRAM_HISTORY_PATH": str(Path(self._dir.name) / "history.sqlite3"),
        }
        for key, value in env.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        env_config._config_instance = None
        history_store.reset_ready_flag()
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        env_config._config_instance = None
        history_store.reset_ready_flag()
        self._dir.cleanup()
        return False


class SimpleHistoryRouteTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_simple_turn_is_versioned_as_xml(self) -> None:
        """The simple path stores draw.io XML like every other path.

        It used to store Mermaid source in this same column, tagged
        ``diagram_type: "mermaid"``, because the client was expected to import
        it into draw.io — an import the embed has no action for, so the diagram
        never rendered. Asserting on ``vertex="1"`` is what pins the fix: what
        lands in history is now something draw.io can actually load.
        """
        with _TempHistory():
            client = self._client()
            xml = (
                '<mxfile><diagram name="Diagram"><mxGraphModel><root>'
                '<mxCell id="0"/><mxCell id="1" parent="0"/>'
                '<mxCell id="a" value="Start" style="rounded=1;" vertex="1" parent="1">'
                '<mxGeometry x="0" y="0" width="78" height="78" as="geometry"/></mxCell>'
                "</root></mxGraphModel></diagram></mxfile>"
            )
            fake = XmlDiagram(xml=xml, assistant_message="Generated a coffee brewing flowchart.")
            with patch(
                "app.core.services.xml_pipeline.generate_simple_diagram_from_ir", return_value=fake,
            ):
                res = client.post("/chat", json={
                    "messages": [{"role": "user", "content": "flowchart of a coffee brewing process"}],
                    "session_id": "simple-session",
                    "diagram_mode": "simple",
                })
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["generation_path"], "simple_ir")
            self.assertIn('vertex="1"', body["xml"])
            self.assertNotIn("mermaid", body)

            versions = history_store.list_versions("simple-session")
            self.assertEqual(len(versions), 1)
            self.assertIn('vertex="1"', versions[0].xml)
            self.assertEqual(versions[0].meta.get("diagram_type"), "simple")
            self.assertEqual(versions[0].summary()["diagram_type"], "simple")


if __name__ == "__main__":
    unittest.main()
