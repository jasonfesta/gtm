import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.outreach_rules import RULES_PATH, assess, load_policy

NOW = dt.datetime(2026, 9, 10, tzinfo=dt.timezone.utc)


def event(key="one", channel="email", days=7, outcome="sent", **extra):
    return dict(
        event_id=key,
        channel=channel,
        direction="outbound",
        occurred_at=(NOW - dt.timedelta(days=days)).isoformat(),
        outcome=outcome,
        **extra,
    )


class OutreachRulesTests(unittest.TestCase):
    def test_spacing_across_channels_at_exact_boundary(self):
        self.assertIn(
            "cross_channel_cooldown",
            assess("email", [event(channel="x", days=2.999)], now=NOW)["blockers"],
        )
        self.assertEqual(assess("email", [event(channel="x", days=3)], now=NOW)["blockers"], [])

    def test_daily_fetch_does_not_shorten_72_hour_wait(self):
        for days in (1, 2, 2.999):
            self.assertIn(
                "cross_channel_cooldown",
                assess("linkedin", [event(channel="x", days=days)], now=NOW)["blockers"],
            )
        history = [event(channel="x", days=3)]
        self.assertEqual(assess("linkedin", history, now=NOW)["blockers"], [])
        history.append(
            dict(event("reply", channel="email", days=1, outcome="replied"), direction="inbound")
        )
        self.assertIn("conversation_needs_review", assess("linkedin", history, now=NOW)["blockers"])

    def test_three_touch_drip_on_each_channel(self):
        for channel in ("x", "linkedin", "email"):
            for count in range(4):
                history = [event(str(n), channel=channel, days=7 * (n + 1)) for n in range(count)]
                result = assess(channel, history, now=NOW)
                self.assertEqual("person_touch_limit" in result["blockers"], count == 3)
                self.assertEqual("channel_touch_limit" in result["blockers"], count >= 1)
        history = [event("one", channel="x"), event("two", channel="linkedin"), event("three")]
        self.assertIn("person_touch_limit", assess("x", history, now=NOW)["blockers"])

    def test_engagement_stops_drip_but_allows_one_actual_response(self):
        history = [event(str(n), channel="x") for n in range(3)]
        incoming = event("incoming", channel="x", days=0, outcome="replied")
        incoming["direction"] = "inbound"
        history.append(incoming)
        self.assertIn("conversation_needs_review", assess("linkedin", history, now=NOW)["blockers"])
        self.assertEqual(assess("x", history, now=NOW, reply_to="incoming")["blockers"], [])
        history.append(event("answer", channel="x", days=0, in_reply_to_message_id="incoming"))
        self.assertIn(
            "incoming_already_answered",
            assess("x", history, now=NOW, reply_to="incoming")["blockers"],
        )
        self.assertIn(
            "actual_incoming_reply_required",
            assess("x", [], now=NOW, reply_to="missing")["blockers"],
        )
        history.append(event("stop", outcome="opted_out"))
        self.assertIn(
            "recipient_stop", assess("x", history, now=NOW, reply_to="incoming")["blockers"]
        )

    def test_markdown_changes_drive_runtime_and_invalid_policy_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "Rules.md"
            original = RULES_PATH.read_text()
            path.write_text(
                original.replace('"max_unanswered_touches": 3', '"max_unanswered_touches": 2')
            )
            with patch("crm.outreach_rules.RULES_PATH", path):
                self.assertIn(
                    "person_touch_limit", assess("x", [event(), event("two")], now=NOW)["blockers"]
                )
                path.write_text(
                    original.replace('"minimum_gap_hours": 72', '"minimum_gap_hours": 336')
                )
                self.assertIn("cross_channel_cooldown", assess("x", [event()], now=NOW)["blockers"])
                path.write_text("missing policy")
                with self.assertRaisesRegex(ValueError, "policy unavailable or invalid"):
                    assess("x", [], now=NOW)
            path.write_text(
                original.replace('"max_unanswered_touches": 3', '"max_unanswered_touches": -1')
            )
            with self.assertRaises(ValueError):
                load_policy(path)

    def test_receipt_deduplication_and_drafts(self):
        history = [
            event(external_reference="message"),
            event("receipt", outcome="delivered", external_reference="message"),
            event("draft", days=0, outcome="drafted"),
        ]
        result = assess("email", history, now=NOW)
        self.assertEqual(result["total_touches"], 1)
        self.assertEqual(result["blockers"], ["channel_touch_limit"])

    def test_stops_across_channels_and_bad_timestamps(self):
        for outcome, reason in [
            ("opted_out", "recipient_stop"),
            ("declined", "recipient_stop"),
            ("replied", "conversation_needs_review"),
            ("bounced", "bounce_needs_review"),
            ("unknown", "uncertain_submission_reconcile"),
        ]:
            with self.subTest(outcome=outcome):
                self.assertIn(
                    reason,
                    assess("email", [event(channel="x", outcome=outcome)], now=NOW)["blockers"],
                )
        for timestamp in ["invalid", "2026-09-01T00:00:00", "2027-01-01T00:00:00Z"]:
            row = event()
            row["occurred_at"] = timestamp
            self.assertIn(
                "history_timestamp_needs_review", assess("email", [row], now=NOW)["blockers"]
            )
