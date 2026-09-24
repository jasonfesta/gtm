import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.human_inbox import Watcher
from crm.reply_locations import ReplyLocations


class ReplyLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "fixture.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.executescript((Path(__file__).resolve().parents[1] / "sql/schema.sql").read_text())
            c.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES ('p','Test','test')"
            )
        self.locations = ReplyLocations(Watcher(self.root / "watch.sqlite3"))
        self.facts = dict(
            channel="x",
            account="x:me",
            account_profile_url="https://x.com/me",
            author_profile_url="https://x.com/me",
            provider_id="123",
            reply_url="https://x.com/me/status/123",
            parent_url="https://x.com/them/status/100",
            conversation_url="https://x.com/them/status/100",
            person_id="p",
            observed_at="2026-09-10T19:00:00Z",
            posted_at=None,
            confirmation="provider_readback",
            status="confirmed",
            task_evidence="synthetic AI outreach receipt",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def shared(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def track(self, **changes):
        return self.locations.track(
            {**self.facts, **changes}, "private-evidence", shared_factory=self.shared
        )

    def test_shared_exact_location_and_replay(self):
        key = self.track()
        self.assertEqual(key, self.track())
        with self.shared() as c:
            rows = c.execute("SELECT * FROM evidence_claims").fetchall()
            self.assertEqual(len(rows), 1)
            facts = json.loads(rows[0]["claim_value"])
            self.assertEqual(facts["reply_url"], self.facts["reply_url"])
            self.assertIsNone(facts["posted_at"])
            self.assertNotIn("private-evidence", rows[0]["claim_value"])
            self.assertEqual(c.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 0)

    def test_draft_wrong_author_and_parent_in_place_of_reply_rejected(self):
        for change in (
            {"status": "draft"},
            {"author_profile_url": "https://x.com/other"},
            {"reply_url": "https://x.com/them/status/123"},
            {"reply_url": self.facts["parent_url"]},
        ):
            with self.assertRaises(ValueError):
                self.track(**change)

    def test_due_revisit_and_restart(self):
        key = self.track()
        self.assertEqual(len(self.locations.due(at="2026-09-10T19:00:00Z")), 1)
        self.locations.visit(
            key,
            visit_id="v1",
            checked_at="2026-09-10T19:00:00Z",
            observed_account="x:me",
            observed_url=self.facts["reply_url"],
            outcome="partial",
            reply_events=[],
            evidence="read",
        )
        self.locations = ReplyLocations(self.locations.watcher)
        self.assertEqual(self.locations.due(at="2026-09-10T19:14:59Z"), [])
        self.assertEqual(len(self.locations.due(at="2026-09-10T19:15:00Z")), 1)

    def test_visit_wrong_account_wrong_url_and_unrecorded_reply_rejected(self):
        key = self.track()
        args = dict(
            visit_id="v1",
            checked_at="2026-09-10T19:00:00Z",
            observed_account="x:me",
            observed_url=self.facts["reply_url"],
            outcome="partial",
            reply_events=[],
            evidence="read",
        )
        for change in (
            {"observed_account": "x:other"},
            {"observed_url": self.facts["parent_url"]},
            {"reply_events": ["missing"]},
            {"outcome": "replies"},
        ):
            with self.assertRaises(ValueError):
                self.locations.visit(key, **{**args, **change})

    def test_immutable_visits_and_location_conflict(self):
        key = self.track()
        args = dict(
            visit_id="v1",
            checked_at="2026-09-10T19:00:00Z",
            observed_account="x:me",
            observed_url=self.facts["reply_url"],
            outcome="no_replies",
            reply_events=[],
            evidence="read",
        )
        self.locations.visit(key, **args)
        self.locations.visit(key, **args)
        with self.assertRaises(ValueError):
            self.locations.visit(key, **{**args, "outcome": "deleted"})
        with self.assertRaises(ValueError):
            self.track(parent_url="https://x.com/them/status/101")

    def test_unknown_person_never_creates_false_record(self):
        with self.assertRaises(ValueError):
            self.track(person_id="missing")
        with self.shared() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM evidence_claims").fetchone()[0], 0)

    def test_whole_crm_audit_includes_people_without_any_activity(self):
        from crm.reply_coverage import audit, write_report

        self.track()
        with self.shared() as c:
            c.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES ('untouched','Untouched','untouched')"
            )
            report = audit(c, self.locations)
        self.assertEqual(report["total_people"], 2)
        self.assertEqual(report["counts"], {"confirmed_reply_link": 1, "missing_social_profile": 1})
        self.assertTrue(report["people"][0]["locations"][0]["local_revisit_registered"])
        destination = self.root / "coverage.md"
        write_report(report, destination)
        self.assertIn("untouched", destination.read_text())
        with self.assertRaises(FileExistsError):
            write_report(report, destination)


if __name__ == "__main__":
    unittest.main()
