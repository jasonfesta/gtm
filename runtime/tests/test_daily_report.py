import json
import tempfile
import unittest
from pathlib import Path

from crm.daily_report import render


class DailyReportTests(unittest.TestCase):
    def test_report_lists_sends_handoffs_and_later_use(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            processed = root / "processed"
            held = root / "held"
            drops = root / "drops"
            handoff = root / "handoff" / "x"
            reports = root / "reports"
            processed.mkdir()
            held.mkdir()
            drops.mkdir()
            handoff.mkdir(parents=True)
            processed.joinpath("sent.json").write_text(
                json.dumps(
                    {
                        "name": "Builder",
                        "channel": "x",
                        "text": "curious how you wired the mcp tool loop",
                        "parent_text": "we shipped an mcp server so the tool loop keeps memory",
                        "parent_url": "https://x.com/builder/status/9",
                        "reply_url": "https://x.com/jasonfesta/status/1",
                        "profile_url": "https://x.com/builder",
                        "observed_at": "2026-09-17T18:01:00+00:00",
                    }
                )
            )
            held.joinpath("later.json").write_text(
                json.dumps(
                    {
                        "name": "HN Builder",
                        "channel": "hacker_news",
                        "hold": "later_use",
                        "profile_url": "https://news.ycombinator.com/user?id=builder",
                        "observed_at": "2026-09-17T18:02:00+00:00",
                    }
                )
            )
            drops.joinpath("2026-09-17T18.json").write_text(
                json.dumps(
                    {
                        "hour": "2026-09-17T18",
                        "comments": [
                            {
                                "name": "SearchHit",
                                "channel": "x",
                                "parent_url": "https://x.com/searchhit/status/1",
                            }
                        ],
                        "later_use": [
                            {
                                "name": "HN Builder",
                                "channel": "hacker_news",
                                "parent_url": "https://news.ycombinator.com/item?id=1",
                            }
                        ],
                        "skipped": [],
                    }
                )
            )
            handoff.joinpath("x-1.json").write_text(
                json.dumps(
                    {
                        "channel": "x",
                        "name": "Queued",
                        "parent_url": "https://x.com/queued/status/2",
                    }
                )
            )
            handoff.joinpath("x-1.done.json").write_text(
                json.dumps(
                    {
                        "channel": "x",
                        "name": "Already sent",
                        "parent_url": "https://x.com/sent/status/3",
                    }
                )
            )
            result = render(
                "2026-09-17",
                drops=drops,
                handoff=root / "handoff",
                processed=processed,
                held=held,
                reports=reports,
            )
            self.assertTrue(Path(result["path"]).is_file())
            self.assertIn("Builder", result["text"])
            self.assertIn("Their post", result["text"])
            self.assertIn("we shipped an mcp server so the tool loop keeps memory", result["text"])
            self.assertIn("curious how you wired the mcp tool loop", result["text"])
            self.assertIn("comments 1 · X 1 · LinkedIn 0", result["text"])
            self.assertIn("1 comments (1 X / 0 LinkedIn)", result["slack"])
            self.assertTrue(Path(result["slack_path"]).is_file())
            self.assertIn("later_use", result["text"])
            self.assertIn("Queued", result["text"])
            self.assertNotIn("Already sent", result["text"])
            self.assertIn("comments=1", result["text"])
            self.assertIn("SearchHit", result["text"])
            self.assertIn("one recent X or LinkedIn post", result["text"])

    def test_report_uses_new_york_day_for_utc_evening_sends(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            processed = root / "processed"
            processed.mkdir()
            processed.joinpath("late.json").write_text(
                json.dumps(
                    {
                        "name": "Evening",
                        "channel": "x",
                        "reply_url": "https://x.com/jasonfesta/status/2",
                        "profile_url": "https://x.com/evening",
                        "observed_at": "2026-09-18T00:05:00+00:00",
                    }
                )
            )
            result = render(
                "2026-09-17",
                drops=root / "drops",
                handoff=root / "handoff",
                processed=processed,
                held=root / "held",
                reports=root / "reports",
            )
            self.assertIn("Evening", result["text"])


if __name__ == "__main__":
    unittest.main()
