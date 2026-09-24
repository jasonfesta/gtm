import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.setup_check import inspect_checkout, inspect_database


class SetupCheckTests(unittest.TestCase):
    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"
            self.assertEqual(inspect_database(path, {"people"})["status"], "missing")
            self.assertFalse(path.exists())

    def test_empty_or_corrupt_database_is_not_treated_as_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "crm.sqlite3"
            with sqlite3.connect(path):
                pass
            self.assertEqual(inspect_database(path, {"people"})["status"], "blocked")
            path.write_bytes(b"not a sqlite database")
            self.assertEqual(inspect_database(path, {"people"})["status"], "blocked")
            self.assertEqual(path.read_bytes(), b"not a sqlite database")

    def test_renamed_checkout_with_spaces_and_inherited_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "new owner" / "gtm"
            folder = root / "runtime/linkedin/accounts"
            folder.mkdir(parents=True)
            (folder / "old-owner.json").write_text(
                json.dumps(
                    {
                        "database_path": "/old-owner/gtm/runtime/linkedin/data/linkedin.sqlite3",
                        "local_root": str(root / "runtime/linkedin"),
                    }
                )
            )
            binding = root / "runtime/accounts/operator.json"
            binding.parent.mkdir()
            binding.write_text("{}")
            report = inspect_checkout(root)
            self.assertEqual(report["workspace_root"], str(root.resolve()))
            self.assertEqual(
                report["accounts"]["stale_local_paths"],
                [
                    {
                        "file": "runtime/linkedin/accounts/old-owner.json",
                        "field": "database_path",
                    }
                ],
            )
            self.assertEqual(
                report["accounts"]["status"], "owner_confirmation_and_live_reverification_required"
            )
            self.assertFalse(report["ready_for_root_check"])
            self.assertFalse(report["ready_for_outreach"])
            self.assertFalse((root / "runtime/data/crm.sqlite3").exists())

    def test_database_uri_handles_spaces_and_special_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "person #1 ?.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE people(id TEXT)")
            before = path.read_bytes()
            self.assertEqual(inspect_database(path, {"people"})["status"], "ok")
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
