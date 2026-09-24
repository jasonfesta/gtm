import copy
import json
import tempfile
import unittest
from pathlib import Path

from crm.linkedin_sales_handoff import digest, validate, write_handoff


class LinkedInSalesHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        evidence = self.root / "capture.txt"
        evidence.write_text("Synthetic browser evidence fixture")
        ref = {"path": str(evidence), "sha256": digest(evidence.read_bytes())}
        self.row = {
            "source": "sales_navigator_manual",
            "name": "Test Builder ..",
            "observed_at": "2026-09-23T12:00:00Z",
            "search_query": '"LangGraph"',
            "search_url": "https://www.linkedin.com/sales/search/people?query=test",
            "sales_profile_url": "https://www.linkedin.com/sales/lead/ABC,NAME_SEARCH,xyz",
            "profile_url": "https://www.linkedin.com/in/test-builder/?tracking=1",
            "ai_work_evidence": "I built a LangGraph agent.",
            "relevance": "Agent builder workflow",
            "post": {
                "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
                "author_profile_url": "https://linkedin.com/in/test-builder",
                "text": "I built a LangGraph agent.",
                "verified": True,
                "comment_available": True,
            },
            "evidence": {stage: ref for stage in ("search", "identity", "post")},
        }

    def write(self, rows):
        path = self.root / "input.json"
        path.write_text(json.dumps({"candidates": rows}))
        target = write_handoff(path, self.root / "outbox")
        return target, json.loads(target.read_text())

    def test_identity_and_provenance_preserved(self):
        row = validate(self.row)
        self.assertEqual(row["name"], "Test Builder ..")
        self.assertEqual(row["sales_identity"], "ABC")
        self.assertEqual(row["post"]["activity_id"], "123")
        self.assertEqual(row["profile_url"], "https://www.linkedin.com/in/test-builder/")

    def test_profile_or_share_cannot_be_reply_target(self):
        for url in (
            self.row["profile_url"],
            "https://www.linkedin.com/feed/sales-navigator/urn:li:share:123",
            "https://www.linkedin.com/feed/update/urn:li:share:123/",
            "https://linkedin.com.evil.test/feed/update/urn:li:activity:123",
        ):
            with self.subTest(url=url):
                self.row["post"]["url"] = url
                with self.assertRaises(ValueError):
                    validate(self.row)

    def test_missing_or_mismatched_evidence_fails(self):
        for key, value in (
            ("source", "apify"),
            ("ai_work_evidence", "Invented quote"),
            ("observed_at", "2026-09-23"),
        ):
            row = copy.deepcopy(self.row)
            row[key] = value
            with self.assertRaises(ValueError):
                validate(row)
        self.row["evidence"]["search"]["sha256"] = "wrong"
        with self.assertRaises(ValueError):
            validate(self.row)

    def test_wrong_author_and_unverified_post_fail(self):
        for key, value in (
            ("author_profile_url", "https://linkedin.com/in/someone-else/"),
            ("verified", False),
            ("comment_available", False),
        ):
            row = copy.deepcopy(self.row)
            row["post"][key] = value
            with self.assertRaises(ValueError):
                validate(row)

    def test_replay_and_cross_run_duplicate(self):
        target, first = self.write([self.row])
        again, replay = self.write([self.row])
        self.assertEqual(target, again)
        self.assertEqual(first, replay)
        self.row["observed_at"] = "2026-09-23T13:00:00Z"
        _, second = self.write([self.row])
        self.assertEqual(second["candidates"], [])
        self.assertIn("duplicate post", second["held"][0]["reason"])

    def test_duplicate_person_keeps_first_post_and_holds_second(self):
        second = copy.deepcopy(self.row)
        second["post"]["url"] = (
            "https://www.linkedin.com/posts/test-builder_test-activity-456-abcd/"
        )
        _, batch = self.write([self.row, second, {}])
        self.assertEqual(len(batch["candidates"]), 1)
        self.assertEqual(len(batch["held"]), 2)
        self.assertIn("duplicate person", batch["held"][0]["reason"])

    def test_held_candidate_can_be_completed_later(self):
        missing = copy.deepcopy(self.row)
        missing["post"]["verified"] = False
        _, held = self.write([missing])
        self.assertEqual(len(held["candidates"]), 0)
        _, ready = self.write([self.row])
        self.assertEqual(len(ready["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
