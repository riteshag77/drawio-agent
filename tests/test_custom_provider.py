"""Custom (BYOK) LLM provider support — ephemeral ProviderSpec construction,
the request-scoped contextvar, and the /providers/validate endpoint."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from app.adapters.outbound.llm import providers as providers_module
from app.adapters.outbound.llm.providers import (
    CUSTOM_PROVIDER_KEY,
    MissingApiKeyError,
    _spec_from_custom_payload,
    build_langchain_client,
    get_provider,
    require_api_key,
    using_custom_provider,
)

_VALID_PAYLOAD = {
    "label": "My Ollama",
    "client": "openai",
    "api_key": "sk-test-123",
    "base_url": "http://localhost:11434/v1",
    "model": "llama3.1",
}


class SpecFromCustomPayloadTests(unittest.TestCase):
    def test_valid_payload_builds_an_ephemeral_spec(self) -> None:
        spec = _spec_from_custom_payload(_VALID_PAYLOAD)
        self.assertEqual(spec.key, CUSTOM_PROVIDER_KEY)
        self.assertTrue(spec.ephemeral)
        self.assertEqual(spec.client_key, "openai")
        self.assertEqual(spec.default_model, "llama3.1")
        self.assertEqual(spec.api_key_inline, "sk-test-123")
        self.assertEqual(spec.base_url_default, "http://localhost:11434/v1")
        self.assertEqual(spec.label, "My Ollama")

    def test_unknown_client_is_rejected(self) -> None:
        payload = {**_VALID_PAYLOAD, "client": "made_up"}
        with self.assertRaises(ValueError):
            _spec_from_custom_payload(payload)

    def test_empty_model_is_rejected(self) -> None:
        payload = {**_VALID_PAYLOAD, "model": ""}
        with self.assertRaises(ValueError):
            _spec_from_custom_payload(payload)

    def test_malformed_base_url_scheme_is_rejected(self) -> None:
        payload = {**_VALID_PAYLOAD, "base_url": "ftp://example.com"}
        with self.assertRaises(ValueError):
            _spec_from_custom_payload(payload)

    def test_base_url_without_hostname_is_rejected(self) -> None:
        payload = {**_VALID_PAYLOAD, "base_url": "http://"}
        with self.assertRaises(ValueError):
            _spec_from_custom_payload(payload)

    def test_private_base_url_is_allowed(self) -> None:
        """Deliberate: this app is a personal/local dev tool — a local Ollama/LAN
        gateway is a realistic custom provider, not something to block."""
        for url in ("http://localhost:11434/v1", "http://192.168.1.50:8000/v1"):
            with self.subTest(url=url):
                spec = _spec_from_custom_payload({**_VALID_PAYLOAD, "base_url": url})
                self.assertEqual(spec.base_url_default, url)

    def test_empty_base_url_is_allowed(self) -> None:
        payload = {**_VALID_PAYLOAD, "base_url": ""}
        spec = _spec_from_custom_payload(payload)
        self.assertEqual(spec.base_url_default, "")

    def test_missing_label_defaults_to_custom(self) -> None:
        payload = {k: v for k, v in _VALID_PAYLOAD.items() if k != "label"}
        spec = _spec_from_custom_payload(payload)
        self.assertEqual(spec.label, "Custom")


class UsingCustomProviderContextTests(unittest.TestCase):
    def test_get_provider_raises_without_active_context(self) -> None:
        with self.assertRaises(ValueError):
            get_provider(CUSTOM_PROVIDER_KEY)

    def test_get_provider_resolves_from_active_context(self) -> None:
        with using_custom_provider(_VALID_PAYLOAD):
            spec = get_provider(CUSTOM_PROVIDER_KEY)
        self.assertTrue(spec.ephemeral)
        self.assertEqual(spec.default_model, "llama3.1")

    def test_context_resets_after_exit(self) -> None:
        with using_custom_provider(_VALID_PAYLOAD):
            get_provider(CUSTOM_PROVIDER_KEY)  # sanity: doesn't raise inside
        with self.assertRaises(ValueError):
            get_provider(CUSTOM_PROVIDER_KEY)

    def test_context_resets_even_on_exception(self) -> None:
        with self.assertRaises(RuntimeError):
            with using_custom_provider(_VALID_PAYLOAD):
                raise RuntimeError("boom")
        with self.assertRaises(ValueError):
            get_provider(CUSTOM_PROVIDER_KEY)


class RequireApiKeyEphemeralTests(unittest.TestCase):
    def test_raises_when_api_key_missing(self) -> None:
        spec = _spec_from_custom_payload({**_VALID_PAYLOAD, "api_key": ""})
        with self.assertRaises(MissingApiKeyError):
            require_api_key(spec)

    def test_no_op_when_api_key_present(self) -> None:
        spec = _spec_from_custom_payload(_VALID_PAYLOAD)
        require_api_key(spec)  # must not raise


class BuildLangchainClientEphemeralTests(unittest.TestCase):
    def test_ephemeral_client_bypasses_the_cache(self) -> None:
        """Two calls with equal ephemeral specs must construct the client twice —
        a user-supplied secret must never sit in the shared lru_cache."""
        spec = _spec_from_custom_payload(_VALID_PAYLOAD)
        mock_cls = MagicMock(return_value=MagicMock())
        with patch.object(providers_module, "_resolve_client_class", return_value=mock_cls):
            build_langchain_client(spec, timeout=15)
            build_langchain_client(spec, timeout=15)
        self.assertEqual(mock_cls.call_count, 2)

    def test_ephemeral_client_receives_inline_key_and_base_url(self) -> None:
        spec = _spec_from_custom_payload(_VALID_PAYLOAD)
        mock_cls = MagicMock(return_value=MagicMock())
        with patch.object(providers_module, "_resolve_client_class", return_value=mock_cls):
            build_langchain_client(spec, timeout=15)
        kwargs = mock_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "sk-test-123")
        self.assertEqual(kwargs["base_url"], "http://localhost:11434/v1")
        self.assertEqual(kwargs["model"], "llama3.1")

    def test_non_ephemeral_client_still_uses_the_cache(self) -> None:
        """Regression check: the existing 6-provider registry path is unaffected."""
        spec = get_provider("claude")
        mock_cls = MagicMock(return_value=MagicMock())
        with patch.object(providers_module, "_resolve_client_class", return_value=mock_cls), \
                patch.dict("os.environ", {spec.api_key_env: "test-key"}, clear=False):
            providers_module._cached_langchain_client.cache_clear()
            build_langchain_client(spec, timeout=30)
            build_langchain_client(spec, timeout=30)
        self.assertEqual(mock_cls.call_count, 1)
        providers_module._cached_langchain_client.cache_clear()


class ReservedKeyTests(unittest.TestCase):
    def test_custom_key_in_providers_json_raises(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        orig_path = providers_module.PROVIDERS_CONFIG_PATH
        try:
            tmp_dir = Path(tempfile.mkdtemp())
            tmp_path = tmp_dir / "providers.json"
            tmp_path.write_text(json.dumps([
                {"key": "custom", "client": "openai", "default_model": "x", "api_key_env": "X_API_KEY"},
            ]), encoding="utf-8")
            providers_module.PROVIDERS_CONFIG_PATH = tmp_path
            with self.assertRaises(ValueError):
                providers_module.reload_providers()
        finally:
            providers_module.PROVIDERS_CONFIG_PATH = orig_path
            providers_module.reload_providers()


class ChatRequestCustomProviderSchemaTests(unittest.TestCase):
    def test_accepts_a_valid_custom_provider(self) -> None:
        from app.adapters.inbound.web.schemas import ChatMessage, ChatRequest

        req = ChatRequest(
            messages=[ChatMessage(role="user", content="hi")],
            custom_provider={
                "client": "openai", "api_key": "sk-1", "base_url": "", "model": "gpt-4o-mini",
            },
        )
        self.assertIsNotNone(req.custom_provider)
        self.assertEqual(req.custom_provider.client, "openai")
        self.assertEqual(req.custom_provider.label, "Custom")

    def test_defaults_to_none(self) -> None:
        from app.adapters.inbound.web.schemas import ChatMessage, ChatRequest

        req = ChatRequest(messages=[ChatMessage(role="user", content="hi")])
        self.assertIsNone(req.custom_provider)

    def test_rejects_unknown_client(self) -> None:
        from app.adapters.inbound.web.schemas import ChatMessage, ChatRequest

        with self.assertRaises(ValidationError):
            ChatRequest(
                messages=[ChatMessage(role="user", content="hi")],
                custom_provider={"client": "made_up", "api_key": "sk-1", "model": "m"},
            )


class ValidateCustomProviderRouteTests(unittest.TestCase):
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_success(self) -> None:
        fake_client = MagicMock()
        fake_client.invoke.return_value = "OK"
        with patch.object(providers_module, "build_langchain_client", return_value=fake_client):
            client = self._client()
            res = client.post("/providers/validate", json=_VALID_PAYLOAD)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["valid"])
        self.assertIn("latency_ms", body)

    def test_missing_api_key(self) -> None:
        client = self._client()
        res = client.post("/providers/validate", json={**_VALID_PAYLOAD, "api_key": ""})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["valid"])
        self.assertIn("API key", body["error"])

    def test_unsupported_client_is_rejected_by_schema(self) -> None:
        client = self._client()
        res = client.post("/providers/validate", json={**_VALID_PAYLOAD, "client": "made_up"})
        self.assertEqual(res.status_code, 422)

    def test_connection_failure_is_reported_not_500(self) -> None:
        with patch.object(providers_module, "build_langchain_client", side_effect=RuntimeError("connection refused")):
            client = self._client()
            res = client.post("/providers/validate", json=_VALID_PAYLOAD)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertFalse(body["valid"])
        self.assertIn("connection refused", body["error"])


if __name__ == "__main__":
    unittest.main()
