import contextlib
import io
import unittest
from unittest.mock import patch

import update


SUCCESS = {
    "contentKind": "statement",
    "expectedCount": 1,
    "attemptedCount": 1,
    "publishedCount": 1,
    "knownAbsentCount": 0,
    "failedCount": 0,
    "statusCounts": {
        "ready": 1,
        "known_absent": 0,
        "transient_failure": 0,
        "invalid_structure": 0,
        "pending": 0,
    },
    "completed": True,
    "assetGc": {"removedFiles": 0, "removedBytes": 0},
}


class UpdateCliTests(unittest.TestCase):
    def test_plain_statement_update_bootstraps_without_pointer_check(self):
        with patch("update.update_statements", return_value=SUCCESS) as crawl, patch(
            "update.rebuild_statements"
        ) as rebuild, contextlib.redirect_stdout(io.StringIO()):
            exit_code = update.main(["--statements"])

        self.assertEqual(exit_code, 0)
        crawl.assert_called_once_with()
        rebuild.assert_not_called()

    def test_statement_rebuild_forces_progressive_refresh(self):
        with patch("update.rebuild_statements", return_value=SUCCESS) as crawl, patch(
            "update.update_statements"
        ) as incremental, contextlib.redirect_stdout(io.StringIO()):
            exit_code = update.main(["--statements", "--rebuild"])

        self.assertEqual(exit_code, 0)
        crawl.assert_called_once_with()
        incremental.assert_not_called()

    def test_plain_editorial_update_bootstraps_without_pointer_check(self):
        report = {**SUCCESS, "contentKind": "editorial"}
        with patch("update.update_editorials", return_value=report) as crawl, patch(
            "update.rebuild_editorials"
        ) as rebuild, contextlib.redirect_stdout(io.StringIO()):
            exit_code = update.main(["--editorials"])

        self.assertEqual(exit_code, 0)
        crawl.assert_called_once_with()
        rebuild.assert_not_called()

    def test_failed_items_make_cli_nonzero_without_hiding_published_items(self):
        report = {**SUCCESS, "completed": False, "failedCount": 1}
        with patch("update.update_statements", return_value=report), contextlib.redirect_stdout(
            io.StringIO()
        ):
            exit_code = update.main(["--statements"])

        self.assertEqual(exit_code, 1)

    def test_help_describes_direct_publication_not_activation(self):
        help_text = update.build_argument_parser().format_help().lower()

        self.assertNotIn("generation", help_text)
        self.assertNotIn("activation", help_text)
        self.assertIn("publish", help_text)

    def test_validate_modes_remain_read_only_for_production_store(self):
        with patch(
            "update.validate_statement",
            return_value={"ok": True, "problemCode": "1700A"},
        ) as validate, contextlib.redirect_stdout(io.StringIO()):
            exit_code = update.main(["--validate-statement", "1700A"])

        self.assertEqual(exit_code, 0)
        validate.assert_called_once_with("1700A")


if __name__ == "__main__":
    unittest.main()
