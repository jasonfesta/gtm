import unittest

from crm.reddit_discovery import observation, qualify, reply_handoff, thread_identity


class RedditDiscoveryTests(unittest.TestCase):
    def row(self, **changes):
        return {
            "author": "Example_Builder",
            "permalink": "/r/Example/comments/abc123/example/",
            "title": "My Claude workflow",
            "selftext": "I am building a coding tool using Claude.",
            **changes,
        }

    def test_exact_account_and_thread(self):
        item, error = observation(self.row(url="https://example.org"), "run1")
        self.assertIsNone(error)
        self.assertEqual(item["account"], "Example_Builder")
        self.assertEqual(
            item["parent_url"], "https://www.reddit.com/r/Example/comments/abc123/example/"
        )
        self.assertEqual(item["action_key"], "reddit:example_builder:abc123")

    def test_thread_validation(self):
        for value in [
            "https://example.org/r/a/comments/abc/",
            "//evil.test/r/a/comments/abc/",
            "https://reddit.com.evil.test/r/a/comments/abc/",
            "https://user@reddit.com/r/a/comments/abc/",
            "https://reddit.com:bad/r/a/comments/abc/",
            "https://reddit.com/r/a/comments/abc/title/commentid/",
            "https://reddit.com/r/a/",
            "http://reddit.com/r/a/comments/abc/",
        ]:
            with self.subTest(value=value):
                self.assertIsNone(thread_identity(value))
        self.assertEqual(
            thread_identity("https://old.reddit.com/r/a/comments/abc/title/?x=1")[1], "abc"
        )

    def test_accounts_and_qualification(self):
        for author in ["[deleted]", "AutoModerator", "", "bad/name"]:
            self.assertEqual(observation(self.row(author=author), "run")[1], "invalid_account")
        self.assertEqual(
            observation(self.row(title="Claude news", selftext="New tool announced"), "run")[1],
            "no_personal_work_evidence",
        )
        self.assertEqual(
            observation(self.row(title="Cursor", selftext="My mouse cursor disappeared"), "run")[1],
            "no_tool_work_context",
        )

    def test_retries_and_url_variants(self):
        first = qualify(
            [
                self.row(),
                self.row(author="example_builder", permalink="/r/Example/comments/abc123/newslug/"),
            ],
            run_id="r1",
        )
        self.assertEqual(first["qualified_count"], 1)
        retry = qualify([self.row()], run_id="r2", previous=first["items"])
        self.assertEqual(retry["qualified_count"], 0)
        self.assertEqual(retry["rejected"], {"duplicate_action": 1})

    def test_two_threads_one_person(self):
        batch = qualify(
            [self.row(), self.row(permalink="/r/Example/comments/def456/another/")], run_id="r"
        )
        self.assertEqual(batch["qualified_count"], 2)
        self.assertEqual(batch["distinct_people"], 1)

    def test_uncertain_send_contract(self):
        handoff = reply_handoff(qualify([self.row()], run_id="r"))
        self.assertEqual(handoff["owner"], "human_reply")
        self.assertIn("Do not resend", handoff["delivery_contract"]["uncertain_send"])
        self.assertIn("Keep uncertain", handoff["delivery_contract"]["no_match"])

    def test_partial_status_survives_handoff(self):
        batch = qualify([self.row()], run_id="r", provider_status="TIMED-OUT")
        self.assertTrue(reply_handoff(batch)["partial"])
        self.assertEqual(reply_handoff(batch)["provider_status"], "TIMED-OUT")

    def test_empty_and_malformed_input(self):
        self.assertEqual(qualify([], run_id="r")["qualified_count"], 0)
        self.assertEqual(qualify([None], run_id="r")["rejected"], {"invalid_row": 1})
        with self.assertRaises(ValueError):
            qualify({}, run_id="r")


if __name__ == "__main__":
    unittest.main()
