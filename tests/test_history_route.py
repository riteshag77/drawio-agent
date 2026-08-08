"""POST /chat history recording — including the mermaid ("simple" intent)
path, which has no `xml` field and previously never got a version saved."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.adapters.outbound import history_store
from app.adapters.outbound.config import env_config
from app.domain.entities.mermaid import MermaidDiagram


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


class MermaidHistoryRouteTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_mermaid_turn_is_versioned(self) -> None:
        with _TempHistory():
            client = self._client()
            fake = MermaidDiagram(mermaid="flowchart TD\nA-->B", assistant_message="Generated Mermaid diagram.")
            with patch("app.adapters.inbound.web.routes.generate_mermaid_diagram", return_value=fake):
                res = client.post("/chat", json={
                    "messages": [{"role": "user", "content": "flowchart of a coffee brewing process"}],
                    "session_id": "mermaid-session",
                    "diagram_mode": "simple",
                })
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["diagram_type"], "mermaid")

            versions = history_store.list_versions("mermaid-session")
            self.assertEqual(len(versions), 1)
            self.assertEqual(versions[0].xml, "flowchart TD\nA-->B")
            self.assertEqual(versions[0].meta.get("diagram_type"), "mermaid")
            self.assertEqual(versions[0].summary()["diagram_type"], "mermaid")


if __name__ == "__main__":
    unittest.main()
