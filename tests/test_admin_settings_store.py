"""Admin settings overrides — persistence and os.environ application."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.adapters.outbound import admin_settings_store
from app.adapters.outbound.config import env_config


class _TempAdminSettings:
    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self._saved_base_dir = env_config.EnvConfigAdapter.base_dir
        temp_path = Path(self._dir.name)
        env_config.EnvConfigAdapter.base_dir = staticmethod(lambda: temp_path)
        return self

    def __exit__(self, *exc):
        env_config.EnvConfigAdapter.base_dir = self._saved_base_dir
        self._dir.cleanup()
        return False


class AdminSettingsStoreTests(unittest.TestCase):
    def test_set_and_get_overrides(self) -> None:
        with _TempAdminSettings():
            admin_settings_store.set_setting("TEST_KEY", "value1")
            self.assertEqual(admin_settings_store.get_overrides(), {"TEST_KEY": "value1"})
            self.assertTrue(admin_settings_store.is_overridden("TEST_KEY"))
            admin_settings_store.clear_settings()

    def test_set_applies_to_environ_immediately(self) -> None:
        with _TempAdminSettings():
            os.environ.pop("TEST_ENV_KEY", None)
            admin_settings_store.set_setting("TEST_ENV_KEY", "hello")
            self.assertEqual(os.environ.get("TEST_ENV_KEY"), "hello")
            admin_settings_store.set_setting("TEST_ENV_KEY", "")
            self.assertIsNone(os.environ.get("TEST_ENV_KEY"))
            admin_settings_store.clear_settings()

    def test_empty_value_clears_override(self) -> None:
        with _TempAdminSettings():
            admin_settings_store.set_setting("TEST_KEY", "value1")
            admin_settings_store.set_setting("TEST_KEY", "")
            self.assertFalse(admin_settings_store.is_overridden("TEST_KEY"))
            admin_settings_store.clear_settings()

    def test_apply_all_to_environ_reapplies_persisted_overrides(self) -> None:
        with _TempAdminSettings():
            admin_settings_store.set_setting("TEST_REAPPLY_KEY", "persisted")
            os.environ.pop("TEST_REAPPLY_KEY", None)
            admin_settings_store.apply_all_to_environ()
            self.assertEqual(os.environ.get("TEST_REAPPLY_KEY"), "persisted")
            admin_settings_store.clear_settings()
            os.environ.pop("TEST_REAPPLY_KEY", None)

    def test_missing_file_returns_empty(self) -> None:
        with _TempAdminSettings():
            self.assertEqual(admin_settings_store.get_overrides(), {})


if __name__ == "__main__":
    unittest.main()
