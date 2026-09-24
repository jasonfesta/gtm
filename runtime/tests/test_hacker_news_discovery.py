import unittest

from crm.hacker_news_discovery import normalize_hacker_news, reply_handoff
from crm.human_discovery import relevant_live_observation


class HackerNewsDiscoveryTests(unittest.TestCase):
    def test_comments_use_own_words_not_parent_story_title(self):
        item = normalize_hacker_news(
            {
                "id": "123",
                "author": "SomeUser",
                "type": "comment",
                "title": "Building apps with Claude",
                "commentText": "Interesting!",
                "url": "https://external.example/article",
            }
        )
        self.assertEqual(item["text"], "Interesting!")
        self.assertFalse(relevant_live_observation(item))
        self.assertEqual(item["parent_url"], "https://news.ycombinator.com/item?id=123")
        self.assertIn("SomeUser", item["profile_url"])

    def test_comment_html_is_decoded_and_identity_required(self):
        item = normalize_hacker_news(
            {"id": 123, "author": "builder", "commentText": "I&#x27;m building with <b>Claude</b>."}
        )
        self.assertIn("I'm building with", item["text"])
        self.assertTrue(relevant_live_observation(item))
        self.assertEqual(normalize_hacker_news({"id": "unknown", "author": "builder"}), {})
        self.assertEqual(normalize_hacker_news({"id": "123"}), {})

    def test_exact_item_dedup_and_no_external_article_handoff(self):
        item = normalize_hacker_news(
            {"id": "123", "author": "builder", "commentText": "Building with Claude"}
        )
        handoff = reply_handoff([item, item], run_id="manual-test")
        self.assertEqual(len(handoff["candidates"]), 1)
        self.assertEqual(handoff["candidates"][0]["status"], "discovered")
        with self.assertRaises(ValueError):
            reply_handoff([{**item, "parent_url": "https://external.example/article"}], run_id="x")
        with self.assertRaises(ValueError):
            reply_handoff(
                [{**item, "parent_url": "https://news.ycombinator.com/item?id=456"}], run_id="x"
            )


if __name__ == "__main__":
    unittest.main()
