import unittest

from crm.human_source_providers import read_completed_run, reddit_search_input, reddit_thread_url


class SourceProviderTests(unittest.TestCase):
    def test_only_successful_actor_runs_can_produce_results(self):
        for status in ("RUNNING", "READY", "FAILED", "TIMED-OUT", "ABORTED", None):
            with self.assertRaisesRegex(RuntimeError, "apify_run:run1:status:"):
                read_completed_run(
                    {"id": "run1", "status": status, "defaultDatasetId": "d"},
                    auth="test",
                    api_call=lambda *a, **k: [],
                )
        self.assertEqual(
            read_completed_run(
                {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "d"},
                auth="test",
                api_call=lambda *a, **k: [],
            ),
            ([], "run1"),
        )

    def test_reddit_thread_validation_rejects_articles_and_comment_urls(self):
        for url in (
            "https://example.com/article",
            "https://reddit.com.evil/r/a/comments/abc/x",
            "https://www.reddit.com/r/a/comments/abc/x/comment1",
            "//example.com/r/a/comments/abc/x",
            "https://user@reddit.com/r/a/comments/abc/x",
        ):
            self.assertEqual(reddit_thread_url(url), "")
        self.assertEqual(
            reddit_thread_url("/r/test/comments/abc/title/?utm_source=x"),
            "https://www.reddit.com/r/test/comments/abc/title/",
        )

    def test_reddit_input_uses_actor_specific_keywords(self):
        payload = reddit_search_input(["Claude", "Cursor"], 20)
        self.assertEqual(payload["searches"], ["Claude", "Cursor"])
        self.assertNotIn("searchTerms", payload)
        self.assertEqual(payload["maxPostCount"], 10)
        self.assertTrue(payload["skipComments"])
