import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linkedin_operator
import store


class LinkedInOperatorTests(unittest.TestCase):
    def valid_config(self):
        return {
            "account_id": "linkedin:test",
            "identity": {"linkedin_slug": "test"},
            "timezone": "America/New_York",
            "local_root": "/tmp/linkedin-test",
            "database_path": "/tmp/linkedin-test.sqlite3",
            "browser": {"profile_key": "test"},
            "targeting_policy": {
                "generation": "fresh_daily_v1",
                "inherit_legacy_targeting": False,
                "inherit_legacy_exclusions": False,
                "current_special_exclusions": [],
            },
            "public_replies": {
                "never_connect_or_follow": True,
                "author_repeat_policy": "lifetime",
                "author_cooldown_hours": None,
            },
            "dm_replies": {"never_initiate": True},
        }

    def load(self, config):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "account.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            return linkedin_operator.load_config(path)

    def test_config_requires_fresh_slate_targeting(self):
        config = self.valid_config()
        config["targeting_policy"]["inherit_legacy_exclusions"] = True
        with self.assertRaisesRegex(ValueError, "legacy exclusions"):
            self.load(config)

    def test_fresh_slate_has_no_special_exclusions(self):
        config = self.valid_config()
        config["targeting_policy"]["current_special_exclusions"] = ["Stiched"]
        with self.assertRaisesRegex(ValueError, "special exclusions"):
            self.load(config)

    def test_lifetime_repeat_policy_has_no_cooldown_window(self):
        self.assertIsNone(linkedin_operator.configured_cooldown(self.valid_config()))

    def test_fresh_comment_lint_does_not_use_legacy_linter(self):
        self.assertEqual(
            linkedin_operator.lint_comment("specific useful reply", {"max_words": 24}), []
        )
        self.assertIn(
            "prose_must_be_lowercase",
            linkedin_operator.lint_comment("Legacy Voice", {"max_words": 24}),
        )

    def test_hiring_post_is_not_reserved_by_legacy_campaign_rule(self):
        config = self.valid_config()
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "ledger.sqlite3"
            store.initialize(db_path)
            with store.connect(db_path) as db:
                db.execute(
                    """INSERT INTO accounts(account_id, public_identifier, display_name,
                               first_detected_at, last_detected_at)
                       VALUES ('linkedin:test', 'test', 'Test', '2026-09-09T12:00:00Z',
                               '2026-09-09T12:00:00Z')"""
                )
                db.execute(
                    """INSERT INTO runs(run_id, account_id, source, workflow, status, started_at)
                       VALUES ('run-1', 'linkedin:test', 'interactive', 'casual', 'started',
                               '2026-09-09T12:00:00Z')"""
                )
                prepared = linkedin_operator.ingest_feed(
                    db,
                    config,
                    {
                        "feed": {
                            "candidates": [
                                {
                                    "urn": "urn:li:activity:fresh-hiring-post",
                                    "author": "Eligible Author",
                                    "authorSlug": "eligible-author",
                                    "authorProfileUrl": "https://www.linkedin.com/in/eligible-author/",
                                    "body": "we are hiring a product designer",
                                    "bodySha256": "body-hash",
                                    "hiring": True,
                                }
                            ]
                        }
                    },
                    "run-1",
                    "2026-09-09T12:00:00Z",
                )
            self.assertEqual(len(prepared), 1)
            self.assertTrue(prepared[0]["eligibility"]["allowed"])

    def test_old_year_row_timestamp_stays_before_baseline(self):
        parsed = linkedin_operator.parse_row_timestamp(
            "Aug 23, 2022",
            seen_at="2026-09-09T14:00:00Z",
            timezone_name="America/New_York",
        )
        self.assertEqual(parsed.year, 2022)

    def test_same_day_clock_time_uses_account_timezone(self):
        parsed = linkedin_operator.parse_row_timestamp(
            "9:30 AM",
            seen_at="2026-09-09T14:00:00Z",
            timezone_name="America/New_York",
        )
        self.assertEqual(parsed.isoformat(), "2026-09-09T13:30:00+00:00")

    def test_dm_lint_rejects_sensitive_and_long_copy(self):
        self.assertIn(
            "sensitive_or_judgment_required",
            linkedin_operator.lint_dm("send your bank routing details", 35),
        )
        self.assertIn("too_long", linkedin_operator.lint_dm(" ".join(["word"] * 36), 35))


if __name__ == "__main__":
    unittest.main()
