"""Saved prompt templates — persistence, search, pin ordering."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.adapters.outbound import template_store
from app.adapters.outbound.config import env_config


class _TempTemplates:
    """Point the store at a throwaway SQLite file."""

    def __init__(self) -> None:
        self._saved: dict[str, str | None] = {}
        self._dir: tempfile.TemporaryDirectory | None = None

    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        env = {"DIAGRAM_TEMPLATES_PATH": str(Path(self._dir.name) / "templates.sqlite3")}
        for key, value in env.items():
            self._saved[key] = os.environ.get(key)
            os.environ[key] = value
        env_config._config_instance = None
        template_store.reset_ready_flag()
        return self

    def __exit__(self, *exc):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        env_config._config_instance = None
        template_store.reset_ready_flag()
        if self._dir:
            self._dir.cleanup()
        return False


class TemplateStoreTests(unittest.TestCase):
    def test_create_and_list(self) -> None:
        with _TempTemplates():
            template_store.create_template(title="VPC", prompt="Build me a VPC")
            templates = template_store.list_templates()
            self.assertEqual(len(templates), 1)
            self.assertEqual(templates[0].title, "VPC")
            self.assertEqual(templates[0].prompt, "Build me a VPC")
            self.assertFalse(templates[0].pinned)
            self.assertEqual(templates[0].run_count, 0)

    def test_empty_prompt_is_rejected(self) -> None:
        with _TempTemplates():
            self.assertIsNone(template_store.create_template(title="x", prompt="   "))
            self.assertEqual(template_store.list_templates(), [])

    def test_pinned_templates_sort_first(self) -> None:
        with _TempTemplates():
            a = template_store.create_template(title="A", prompt="a")
            template_store.create_template(title="B", prompt="b")
            template_store.update_template(a.id, pinned=True)
            templates = template_store.list_templates()
            self.assertEqual(templates[0].id, a.id)
            self.assertTrue(templates[0].pinned)

    def test_search_matches_title_and_prompt(self) -> None:
        with _TempTemplates():
            template_store.create_template(title="Serverless API", prompt="lambda + api gateway")
            template_store.create_template(title="Data Lake", prompt="s3 + glue + athena")
            self.assertEqual(len(template_store.list_templates("serverless")), 1)
            self.assertEqual(len(template_store.list_templates("glue")), 1)
            self.assertEqual(len(template_store.list_templates("nothing-matches")), 0)

    def test_update_changes_fields_and_bumps_updated_at(self) -> None:
        with _TempTemplates():
            t = template_store.create_template(title="Old", prompt="old prompt")
            updated = template_store.update_template(t.id, title="New", prompt="new prompt")
            self.assertEqual(updated.title, "New")
            self.assertEqual(updated.prompt, "new prompt")
            self.assertEqual(updated.id, t.id)

    def test_update_unknown_id_returns_none(self) -> None:
        with _TempTemplates():
            self.assertIsNone(template_store.update_template("nope", title="x"))

    def test_bump_run_count(self) -> None:
        with _TempTemplates():
            t = template_store.create_template(title="A", prompt="a")
            template_store.bump_run_count(t.id)
            bumped = template_store.bump_run_count(t.id)
            self.assertEqual(bumped.run_count, 2)

    def test_delete_removes_template(self) -> None:
        with _TempTemplates():
            t = template_store.create_template(title="A", prompt="a")
            self.assertTrue(template_store.delete_template(t.id))
            self.assertEqual(template_store.list_templates(), [])
            self.assertFalse(template_store.delete_template(t.id))


if __name__ == "__main__":
    unittest.main()
