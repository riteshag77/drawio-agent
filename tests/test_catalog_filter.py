"""Hint-filtering of the icon catalogs.

The filter exists so a 126-line catalog does not enter every prompt whole. What
matters is that the slice it produces is still *self-describing*: a bare row
like ``| aws_dynamodb_table | aws_dynamodb |`` means one thing under "Keys with
no usable icon — never emit these" and the opposite with no heading at all.
"""

from __future__ import annotations

import unittest

from app.adapters.outbound.llm.prompts.system import _filter_catalog, _load_reference

CATALOG = """# AWS services — `shape_type` vocabulary

Copy a key verbatim. A service that is not listed still takes your best guess.

## Compute
| shape_type | Service |
|---|---|
| aws_lambda | Lambda |
| aws_ec2 | EC2 |

## Database
| shape_type | Service |
|---|---|
| aws_dynamodb | DynamoDB |
| aws_rds | RDS |

## Keys with no usable icon — never emit these
| Do not use | Use instead |
|---|---|
| aws_dynamodb_table | aws_dynamodb |
| aws_s3_bucket | aws_s3 |
"""


class CatalogFilterTests(unittest.TestCase):
    def _filtered(self, hint: str) -> str:
        return _filter_catalog(CATALOG, hint, max_lines=20)

    def test_short_catalogs_pass_through_untouched(self) -> None:
        self.assertEqual(_filter_catalog(CATALOG, "lambda", max_lines=500), CATALOG)

    def test_no_hint_returns_everything(self) -> None:
        self.assertEqual(_filter_catalog(CATALOG, "", max_lines=20), CATALOG)

    def test_preamble_always_survives(self) -> None:
        """It carries 'copy verbatim' and 'guess if not listed' — drop it and the
        model has rows with no instruction on how to use them."""
        out = self._filtered("lambda")
        self.assertIn("Copy a key verbatim", out)
        self.assertIn("best guess", out)

    def test_matching_row_keeps_its_heading_and_columns(self) -> None:
        out = self._filtered("lambda")
        self.assertIn("## Compute", out)
        self.assertIn("| shape_type | Service |", out)
        self.assertIn("| aws_lambda | Lambda |", out)

    def test_unmatched_sections_are_dropped_whole(self) -> None:
        out = self._filtered("lambda")
        self.assertNotIn("## Database", out)
        self.assertNotIn("aws_rds", out)

    def test_prohibition_rows_never_arrive_without_their_heading(self) -> None:
        """Regression: the old filter latched its header flag off on the first
        ordinary line, so this row survived stripped of the heading that made it
        a prohibition — reading as an ordinary catalog entry."""
        out = self._filtered("dynamodb")
        self.assertIn("aws_dynamodb_table", out)
        self.assertIn("never emit these", out)
        self.assertIn("| Do not use | Use instead |", out)

    def test_falls_back_to_full_catalog_when_nothing_matches(self) -> None:
        self.assertEqual(self._filtered("kubernetes helm istio"), CATALOG)

    def test_real_catalogs_keep_their_structure(self) -> None:
        for ref, hint, expect in [
            ("aws_stencils.txt", "lambda dynamodb", "| aws_lambda | Lambda |"),
            ("gcp-icons-common.txt", "cloud run", "gcp_cloud_run"),
            ("azure-icons-common.txt", "app service", "azure_app_service"),
            ("multi-icons-top.txt", "lambda cloud run", "| aws_lambda | Lambda |"),
        ]:
            with self.subTest(ref):
                out = _filter_catalog(_load_reference(ref), hint)
                self.assertIn(expect, out)
                self.assertTrue(out.lstrip().startswith("#"), "preamble/title was dropped")
                # Every surviving data row sits under some heading.
                self.assertIn("##", out)
