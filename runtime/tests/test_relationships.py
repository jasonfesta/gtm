import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.cli import initialize
from crm.database import translate
from crm.relationships import Relationships, catalog

AT = "2026-09-20T12:00:00Z"
LATER = "2026-09-21T12:00:00Z"


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "crm.sqlite3"
        with initialize(self.db) as conn:
            conn.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES('p','Person','person')"
            )
            conn.execute(
                "INSERT INTO agents(agent_id,canonical_name,normalized_name) VALUES('a','Agent','agent')"
            )
        self.crm = Relationships(self.db)

    def record(self, kind, provider_id, **extra):
        return self.crm.record(
            "human",
            "p",
            "x",
            kind,
            "operator",
            provider_id,
            LATER,
            "provider readback",
            "test",
            occurred_at=AT,
            target_is_ours=True,
            target_url="https://x.com/operator/status/1",
            **extra,
        )

    def test_hacker_news_outbound_receipt_is_idempotent(self):
        receipt = dict(
            entity_type="human",
            entity_id="p",
            channel="hacker-news",
            kind="outbound",
            account_key="operator",
            provider_id="49818599",
            observed_at=LATER,
            occurred_at=AT,
            evidence="published comment readback",
            reviewer="test",
            target_url="https://news.ycombinator.com/item?id=49817961",
            interaction_url="https://news.ycombinator.com/item?id=49818599",
        )
        self.assertTrue(self.crm.record(**receipt)["created"])
        self.assertFalse(self.crm.record(**receipt)["created"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM relationship_events WHERE channel='hacker-news'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(conn.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 0)

    def test_six_categories_and_unknown_existing_classification(self):
        self.assertEqual(len(catalog()), 6)
        rows = self.crm.list()
        self.assertEqual({r["entity_type"] for r in rows}, {"human", "agent"})
        self.assertTrue(all(r["audience"] is None for r in rows))
        self.assertTrue(all(r["email"] is None and r["dm"] == [] for r in rows))
        self.crm.classify("human", "p", "early_adopters", "source", "test")
        self.crm.classify("agent", "a", "research_agents", "source", "test", can_connect_mcp="yes")
        self.assertNotIn("github", self.crm.list("human")[0]["channels_to_check"])
        self.assertEqual(self.crm.list("agent")[0]["can_connect_mcp"], "yes")
        with self.assertRaises(ValueError):
            self.crm.classify("human", "p", "personal_agents", "source", "test")

    def test_columns_multiple_routes_dm_and_legacy_contacts(self):
        for channel, address in [
            ("email", "p@example.test"),
            ("email", "other@example.test"),
            ("x", "https://x.com/person"),
            ("linkedin", "https://linkedin.com/in/person"),
            ("github", "https://github.com/person"),
            ("reddit", "https://reddit.com/user/person"),
            ("moltbook", "https://moltbook.com/u/person"),
        ]:
            self.crm.save_contact(
                "human",
                "p",
                channel,
                address,
                "available",
                "https://example.test/proof",
                "test",
                last_verified_at=AT,
                supports_dm="yes" if channel == "x" else "unknown",
            )
        row = self.crm.list("human")[0]
        self.assertEqual(len(row["contact_points"]), 7)
        self.assertEqual(len(row["contact_channels"]), 6)
        self.assertEqual(row["dm"][0]["channel"], "x")
        self.assertTrue(
            all(
                row[k]
                for k in (
                    "email",
                    "x_url",
                    "linkedin_url",
                    "github_url",
                    "reddit_url",
                    "moltbook_url",
                )
            )
        )
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM contact_points").fetchone()[0], 4)
        self.crm.save_contact(
            "agent",
            "a",
            "agent_email",
            "a@example.test",
            "available",
            "https://example.test/proof",
            "test",
            last_verified_at=AT,
        )
        self.assertEqual(self.crm.list("agent")[0]["email"], "a@example.test")
        with self.assertRaises(ValueError):
            self.crm.save_contact("human", "p", "reddit", "", "available", "source", "test")

    def test_actual_engagement_dedup_and_outbound_are_separate(self):
        self.record("like", "like-1")
        self.record("comment", "comment-1")
        self.record("reply", "reply-1")
        self.assertFalse(self.record("reply", "reply-1")["created"])
        row = self.crm.list("human")[0]
        self.assertTrue(all(row["last_" + k + "_at"] for k in ("like", "comment", "reply")))
        self.assertIsNone(row["last_outbound_contact_at"])
        self.record("outbound", "sent-1")
        self.assertIsNotNone(self.crm.list("human")[0]["last_outbound_contact_at"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM relationship_events").fetchone()[0], 4
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE relationship_events SET kind="reply"')
        with self.assertRaises(ValueError):
            self.crm.record(
                "agent",
                "a",
                "x",
                "reply",
                "operator",
                "reply-1",
                LATER,
                "different identity",
                "test",
                occurred_at=AT,
                target_is_ours=True,
            )

    def test_unknown_time_and_third_party_engagement_not_fabricated(self):
        self.crm.record(
            "human",
            "p",
            "x",
            "like",
            "operator",
            "like-unknown",
            LATER,
            "observed in notifications",
            "test",
            target_is_ours=True,
            target_url="https://x.com/operator/status/1",
        )
        self.crm.record(
            "human",
            "p",
            "reddit",
            "comment",
            "operator",
            "third-party",
            LATER,
            "source",
            "test",
            occurred_at=AT,
            target_is_ours=False,
            target_url="https://reddit.com/comments/other",
        )
        row = self.crm.list("human")[0]
        self.assertIsNone(row["last_like_at"])
        self.assertIsNotNone(row["last_like_observed_at"])
        self.assertEqual(row["like_unknown_time_count"], 1)
        self.assertIsNone(row["last_comment_at"])
        self.assertIsNone(row["last_reply_at"])

    def test_x_like_compatibility_bridge_is_never_a_reply(self):
        with initialize(self.db) as conn:
            conn.execute(
                "INSERT INTO x_engagement_events(event_id,account,provider_id,interaction,actor_handle,actor_profile_url,target_url,occurred_at,person_id,identity_status,target_status) VALUES('xn','operator','like1','like','person','https://x.com/person','https://x.com/operator/status/1',?,'p','matched','registered')",
                (AT,),
            )
            conn.execute(
                "INSERT INTO outreach_events(event_id,person_id,channel,direction,occurred_at,outcome) VALUES('xn','p','x','inbound',?,'replied')",
                (AT,),
            )
            conn.execute(
                "INSERT INTO outreach_events(event_id,person_id,channel,direction,occurred_at,outcome) VALUES('draft','p','x','outbound',?,'drafted')",
                (LATER,),
            )
        row = self.crm.list("human")[0]
        self.assertIsNotNone(row["last_like_at"])
        self.assertIsNone(row["last_reply_at"])
        self.assertIsNone(row["last_outbound_contact_at"])

    def test_timestamp_offsets_sort_by_instant(self):
        self.crm.record(
            "human",
            "p",
            "x",
            "reply",
            "operator",
            "later",
            LATER,
            "source",
            "test",
            occurred_at="2026-09-20T10:00:00-04:00",
            target_is_ours=True,
        )
        self.crm.record(
            "human",
            "p",
            "x",
            "reply",
            "operator",
            "earlier",
            LATER,
            "source",
            "test",
            occurred_at="2026-09-20T13:00:00Z",
            target_is_ours=True,
        )
        self.assertEqual(self.crm.list("human")[0]["last_reply"]["provider_id"], "later")

    def test_arrays_keep_history_and_latest_for_every_channel_and_kind(self):
        self.record("like", "like-1")
        self.record("reply", "reply-1")
        self.record("outbound", "sent-1")
        self.crm.record(
            "human",
            "p",
            "x",
            "reply",
            "operator",
            "reply-2",
            LATER,
            "readback",
            "test",
            target_is_ours=True,
        )
        self.crm.record(
            "human",
            "p",
            "email",
            "reply",
            "inbox",
            "email-1",
            LATER,
            "readback",
            "test",
            occurred_at=AT,
            target_is_ours=True,
        )
        row = self.crm.list("human")[0]
        self.assertEqual(len(row["contact_history"]), 5)
        self.assertEqual(len(row["last_contact"]), 4)
        self.assertEqual(row["last_contact"][0]["provider_id"], "reply-2")
        self.assertIsNone(row["last_contact"][0]["occurred_at"])
        self.assertEqual(row["last_contact"][0]["time_basis"], "observed")
        self.assertEqual(row["last_contact_time_basis"], "observed")
        self.assertEqual(
            {(e["channel"], e["kind"]) for e in row["last_contact"]},
            {("x", "like"), ("x", "reply"), ("x", "outbound"), ("email", "reply")},
        )
        self.assertEqual(self.crm.list("agent")[0]["last_contact"], [])
        self.assertEqual(self.crm.list("agent")[0]["contact_history"], [])

    def test_postgres_translation_keeps_private_evidence_local(self):
        _, values, *_ = translate(
            "INSERT INTO relationship_events(event_id,profile_id,channel,kind,account_key,provider_id,observed_at,evidence,reviewer) VALUES(?,?,?,?,?,?,?,?,?)",
            ("e", "human:p", "x", "reply", "operator", "p", AT, "PRIVATE BODY", "test"),
        )
        self.assertNotIn("PRIVATE BODY", str(values))


if __name__ == "__main__":
    unittest.main()
