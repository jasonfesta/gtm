import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm import hydration_import as MODULE

ROOT = Path(__file__).resolve().parents[1]


class HydrationImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.db = self.run / "test.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.executescript((ROOT / "sql/schema.sql").read_text())
            c.execute(
                "INSERT INTO agents(agent_id,canonical_name,normalized_name) VALUES('a','Example','example')"
            )
            c.execute(
                "INSERT INTO sources(source_id,url,source_type,quality_tier,accessed_at) VALUES('s','https://directory.example/a','directory',3,'2026-09-10')"
            )
            c.execute(
                "INSERT INTO listings(listing_id,source_site,source_key,agent_id,source_id,source_name,first_seen_at,last_seen_at) VALUES('l','imessage_store','a','a','s','Example','2026-09-10','2026-09-10')"
            )
        archive = self.run / "page.html"
        archive.write_text("<p>Test Builder</p>")
        receipt = self.run / "receipt.json"
        receipt.write_text(
            json.dumps(
                {
                    "status": 200,
                    "url": "https://example.com/team",
                    "archive": str(archive),
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "text": "Test Builder, software engineer",
                    "links": ["mailto:builder@example.com"],
                }
            )
        )
        self.row = {
            "person_id": "p",
            "name": "Test Builder",
            "product": "Example",
            "agent_id": "a",
            "website": "https://example.com/",
            "role": "Software engineer",
            "relationship": "team",
            "technical": True,
            "notes": "Synthetic fixture",
            "source_url": "https://example.com/team",
            "evidence_file": str(receipt),
            "name_token": "Test Builder",
            "contacts": [],
        }

    def freeze(self):
        data = self.run / "reviewed-people.json"
        data.write_text(json.dumps([self.row]))
        (self.run / "people-manifest.json").write_text(
            json.dumps(
                {
                    "person_ids": ["p"],
                    "denominator": 1,
                    "input_sha256": hashlib.sha256(data.read_bytes()).hexdigest(),
                }
            )
        )

    def test_valid_import_and_backup_preserve_previous_state(self):
        self.freeze()
        result = MODULE.import_people(self.db, self.run, apply=True)
        self.assertEqual(result["added"]["people"], 1)
        with sqlite3.connect(result["backup"]) as c:
            self.assertEqual(c.execute("SELECT count(*) FROM people").fetchone()[0], 0)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(c.execute("SELECT count(*) FROM company_people").fetchone()[0], 1)

    def test_changed_frozen_input_rejected_before_writes(self):
        self.freeze()
        (self.run / "reviewed-people.json").write_text("[]")
        with self.assertRaisesRegex(ValueError, "changed after"):
            MODULE.import_people(self.db, self.run, apply=True)

    def test_namesake_collision_cannot_create_duplicate(self):
        self.freeze()
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES('old','TEST BUILDER','test builder')"
            )
        with self.assertRaisesRegex(ValueError, "explicit merge"):
            MODULE.import_people(self.db, self.run, apply=True)

    def test_contact_missing_from_public_evidence_cannot_be_confirmed(self):
        self.row["contacts"] = [
            {
                "kind": "email",
                "value": "guessed@example.com",
                "status": "confirmed",
                "source_url": self.row["source_url"],
                "evidence_file": self.row["evidence_file"],
            }
        ]
        self.freeze()
        with self.assertRaisesRegex(ValueError, "contact absent"):
            MODULE.import_people(self.db, self.run, apply=True)


if __name__ == "__main__":
    unittest.main()
