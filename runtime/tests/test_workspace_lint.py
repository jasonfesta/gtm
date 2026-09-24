"""Standalone source checks distinguish external dependencies from broken links."""

import tempfile
import unittest
from pathlib import Path

from crm.workspace_lint import run


class SourceLintTests(unittest.TestCase):
    def test_omitted_private_and_application_links_are_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "README.md").write_text(
                "[private](runtime/accounts/operator.json)\n[application](control-plane/README.md)\n"
            )
            result = run(root, source_only=True)
            self.assertEqual(result["errors"], [])
            self.assertEqual(len(result["external_dependencies"]), 2)
            self.assertEqual(len(run(root)["errors"]), 2)

    def test_missing_source_links_still_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "README.md").write_text("[typo](runtime/setpu/README.md)\n")
            self.assertEqual(len(run(root, source_only=True)["errors"]), 1)

    def test_local_archive_is_not_maintained_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "README.md").write_text("# Current source\n")
            archive = root / "archive"
            archive.mkdir()
            (archive / "stale.md").write_text("[old missing link](missing.md)\n")
            result = run(root, source_only=True)
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["files"], 1)
