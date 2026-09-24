import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.cli import initialize
from crm.tags import Tags


class TagTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "crm.sqlite3"
        with initialize(self.db) as conn:
            conn.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES('p','Test','test')"
            )
        self.tags = Tags(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_catalog_is_idempotent_without_assigning_people(self):
        with initialize(self.db):
            pass
        self.assertEqual(len(self.tags.list()), 6)
        self.assertEqual(self.tags.list("p"), [])
        self.assertEqual(
            {t["label"] for t in self.tags.list()},
            {
                "assistant developers",
                "enterprise developers",
                "open source developers",
                "developer infra platforms",
                "solo developer",
                "founder",
            },
        )

    def test_overlapping_tags_remove_readd_and_retry_preserve_history(self):
        for tag in ("founder", "assistant_developers"):
            self.tags.set("p", tag, "add", "Synthetic role evidence", "test")
        self.assertEqual(len(self.tags.list("p")), 2)
        self.assertFalse(self.tags.set("p", "founder", "add", "Retry", "test")["changed"])
        self.tags.set("p", "founder", "remove", "Role corrected", "test")
        self.assertEqual([t["tag_id"] for t in self.tags.list("p")], ["assistant_developers"])
        self.tags.set("p", "founder", "add", "New supporting evidence", "test")
        with initialize(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM person_tag_events").fetchone()[0], 4
            )
            self.assertEqual(conn.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 0)

    def test_invalid_identity_tag_and_missing_evidence_rejected(self):
        for person, tag, evidence in [
            ("missing", "founder", "source"),
            ("p", "invented", "source"),
            ("p", "founder", " "),
        ]:
            with self.assertRaises(ValueError):
                self.tags.set(person, tag, "add", evidence, "test")
        self.assertEqual(self.tags.list("p"), [])

    def test_history_cannot_be_rewritten(self):
        self.tags.set("p", "founder", "add", "Synthetic evidence", "test")
        with initialize(self.db) as conn:
            for statement in (
                "DELETE FROM person_tag_events",
                "UPDATE person_tag_events SET action='remove'",
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(statement)
