"""Admin panel routes — auth gate, provider listing/update, default provider."""

from __future__ import annotations

import hashlib
import hmac
import os
import unittest

from app.adapters.outbound import admin_settings_store
from app.adapters.outbound.config import env_config


class _TempAdminPassword:
    def __init__(self, password: str | None) -> None:
        self.password = password

    def __enter__(self):
        self._saved = os.environ.get("ADMIN_PASSWORD")
        if self.password is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self.password
        env_config._config_instance = None
        return self

    def __exit__(self, *exc):
        if self._saved is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self._saved
        env_config._config_instance = None
        admin_settings_store.clear_settings()
        return False


def _token(password: str) -> str:
    return hmac.new(password.encode("utf-8"), b"admin-session-v1", hashlib.sha256).hexdigest()


class AdminRoutesTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_status_reports_unconfigured_without_password(self) -> None:
        with _TempAdminPassword(None):
            client = self._client()
            res = client.get("/admin/status")
            self.assertEqual(res.status_code, 200)
            self.assertFalse(res.json()["configured"])

    def test_status_reports_configured_with_password(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            res = client.get("/admin/status")
            self.assertTrue(res.json()["configured"])

    def test_providers_route_rejects_without_token(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            res = client.get("/admin/providers")
            self.assertEqual(res.status_code, 401)

    def test_providers_route_404s_when_unconfigured(self) -> None:
        with _TempAdminPassword(None):
            client = self._client()
            res = client.get("/admin/providers", headers={"X-Admin-Token": "whatever"})
            self.assertEqual(res.status_code, 404)

    def test_login_wrong_password_rejected(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            res = client.post("/admin/login", json={"password": "wrong"})
            self.assertEqual(res.status_code, 401)

    def test_login_correct_password_returns_working_token(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            res = client.post("/admin/login", json={"password": "secret123"})
            self.assertEqual(res.status_code, 200)
            token = res.json()["token"]
            self.assertEqual(token, _token("secret123"))

            providers_res = client.get("/admin/providers", headers={"X-Admin-Token": token})
            self.assertEqual(providers_res.status_code, 200)
            body = providers_res.json()
            self.assertIn("providers", body)
            self.assertIn("default_provider", body)
            keys = {p["key"] for p in body["providers"]}
            self.assertIn("claude", keys)
            self.assertIn("gemini", keys)

    def test_update_provider_key_is_reflected_and_masked(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            token = _token("secret123")
            res = client.post(
                "/admin/providers/openai",
                json={"api_key": "sk-testkey1234"},
                headers={"X-Admin-Token": token},
            )
            self.assertEqual(res.status_code, 200)

            providers_res = client.get("/admin/providers", headers={"X-Admin-Token": token})
            openai_entry = next(p for p in providers_res.json()["providers"] if p["key"] == "openai")
            self.assertTrue(openai_entry["api_key_set"])
            self.assertEqual(openai_entry["api_key_source"], "admin")
            self.assertEqual(openai_entry["api_key_hint"], "••••1234")
            self.assertNotIn("sk-testkey1234", providers_res.text)

            os.environ.pop("OPENAI_API_KEY", None)

    def test_clear_provider_key_reverts_override(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            token = _token("secret123")
            client.post("/admin/providers/openai", json={"api_key": "sk-testkey1234"}, headers={"X-Admin-Token": token})
            res = client.delete("/admin/providers/openai/api-key", headers={"X-Admin-Token": token})
            self.assertEqual(res.status_code, 200)
            self.assertFalse(admin_settings_store.is_overridden("OPENAI_API_KEY"))
            os.environ.pop("OPENAI_API_KEY", None)

    def test_unknown_provider_key_is_404(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            token = _token("secret123")
            res = client.post("/admin/providers/not-a-real-provider", json={"api_key": "x"}, headers={"X-Admin-Token": token})
            self.assertEqual(res.status_code, 404)

    def test_set_default_provider(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            token = _token("secret123")
            res = client.post("/admin/default-provider", json={"provider": "claude"}, headers={"X-Admin-Token": token})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["default_provider"], "claude")
            admin_settings_store.set_setting("PROVIDER_DEFAULT", "")

    def test_set_default_provider_rejects_unknown(self) -> None:
        with _TempAdminPassword("secret123"):
            client = self._client()
            token = _token("secret123")
            res = client.post("/admin/default-provider", json={"provider": "not-real"}, headers={"X-Admin-Token": token})
            self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
