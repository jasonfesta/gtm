import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import store


class LinkedInStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "ledger.sqlite3"
        store.initialize(self.db_path)
        self.db = store.connect(self.db_path)
        now = "2026-09-09T12:00:00Z"
        for account_id, slug in (("acct-a", "operator-a"), ("acct-b", "operator-b")):
            self.db.execute(
                "INSERT INTO accounts(account_id, public_identifier, display_name, first_detected_at, last_detected_at) VALUES (?, ?, ?, ?, ?)",
                (account_id, slug, slug, now, now),
            )
        self.db.execute(
            "INSERT INTO people(person_id, canonical_name, normalized_name, identity_status, created_at, updated_at) VALUES ('person-1', 'Target Person', 'target person', 'verified', ?, ?)",
            (now, now),
        )
        self.db.execute(
            "INSERT INTO posts(post_id, post_signature, observed_at) VALUES ('post-123', 'target-person|post-123', ?)",
            (now,),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def reserve(self, action_id, account_id, key):
        self.db.execute(
            """INSERT INTO action_intents(
                action_id, idempotency_key, account_id, person_id, action_type,
                workflow, policy_scope, reserved_at
            ) VALUES (?, ?, ?, 'person-1', 'comment', 'casual', 'post:123', '2026-09-09T12:01:00Z')""",
            (action_id, key, account_id),
        )

    def test_idempotency_key_blocks_retry(self):
        self.reserve("action-1", "acct-a", "acct-a|linkedin|comment|post:123|casual")
        with self.assertRaises(sqlite3.IntegrityError):
            self.reserve("action-2", "acct-a", "acct-a|linkedin|comment|post:123|casual")

    def test_different_accounts_have_separate_action_scope(self):
        self.reserve("action-1", "acct-a", "acct-a|linkedin|comment|post:123|casual")
        self.reserve("action-2", "acct-b", "acct-b|linkedin|comment|post:123|casual")
        count = self.db.execute("SELECT COUNT(*) FROM action_intents").fetchone()[0]
        self.assertEqual(count, 2)

    def test_message_history_is_append_only(self):
        self.db.execute(
            """INSERT INTO messages(
                message_id, account_id, person_id, direction, message_kind, body,
                occurred_at, observed_at, source
            ) VALUES ('message-1', 'acct-a', 'person-1', 'outbound', 'casual_comment',
                      'specific comment', '2026-09-09T12:02:00Z', '2026-09-09T12:02:01Z', 'manual')"""
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute(
                "UPDATE messages SET body = 'replacement' WHERE message_id = 'message-1'"
            )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM messages WHERE message_id = 'message-1'")

    def test_same_channel_cold_touch_blocks_same_person(self):
        self.db.execute(
            "INSERT INTO posts(post_id, post_signature, observed_at) VALUES ('new-post', 'target-person|new-post', '2026-09-09T12:00:00Z')"
        )
        self.db.execute(
            """INSERT INTO messages(
                message_id, account_id, person_id, direction, message_kind, body,
                occurred_at, observed_at, source
            ) VALUES ('message-1', 'acct-a', 'person-1', 'outbound', 'casual_comment',
                      'specific comment', '2026-09-09T11:00:00Z', '2026-09-09T11:00:01Z', 'manual')"""
        )
        decision = store.eligibility(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            post_id="new-post",
            now=store._time("2026-09-09T12:00:00Z"),
        )
        self.assertEqual(decision["reason"], "channel_touch_limit")

    def test_old_contact_cannot_repeat_channel_cold_touch(self):
        self.db.execute(
            "INSERT INTO posts(post_id, post_signature, observed_at) VALUES ('later-post', 'target-person|later-post', '2026-09-09T12:00:00Z')"
        )
        self.db.execute(
            """INSERT INTO messages(
                message_id, account_id, person_id, direction, message_kind, body,
                occurred_at, observed_at, source
            ) VALUES ('old-comment', 'acct-a', 'person-1', 'outbound', 'casual_comment',
                      'old contact', '2020-01-01T12:00:00Z', '2026-09-09T12:00:00Z', 'import')"""
        )
        decision = store.eligibility(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            post_id="later-post",
            cooldown_hours=None,
            now=store._time("2026-09-09T12:00:00Z"),
        )
        self.assertEqual(decision["reason"], "channel_touch_limit")

    def test_three_social_touches_stop_cold_but_allow_incoming_reply(self):
        for n, kind in enumerate(("casual_comment", "dm", "lead_comment")):
            self.db.execute(
                "INSERT INTO messages(message_id,account_id,person_id,direction,message_kind,body,occurred_at,observed_at,source) "
                "VALUES(?,'acct-a','person-1','outbound',?,'text','2026-08-01T12:00:00Z','2026-08-01T12:00:00Z','manual')",
                (str(n), kind),
            )
        args = dict(
            account_id="acct-a",
            person_id="person-1",
            workflow="casual",
            now=store._time("2026-09-10T12:00:00Z"),
        )
        decision = store.eligibility(self.db, action_type="comment", post_id="post-123", **args)
        self.assertFalse(decision["allowed"])
        self.assertIn(decision["reason"], {"channel_touch_limit", "person_touch_limit"})
        self.db.execute(
            "INSERT INTO messages(message_id,account_id,person_id,direction,message_kind,body,occurred_at,observed_at,source) "
            "VALUES('incoming','acct-a','person-1','inbound','dm','question','2026-09-10T11:00:00Z','2026-09-10T11:00:00Z','manual')"
        )
        decision = store.eligibility(
            self.db, action_type="reply", in_reply_to_message_id="incoming", **args
        )
        self.assertTrue(decision["allowed"])
        decision = store.eligibility(self.db, action_type="comment", post_id="post-123", **args)
        self.assertFalse(decision["allowed"])

    def test_initial_dm_requires_complete_history_review(self):
        decision = store.eligibility(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="dm",
            workflow="lead",
        )
        self.assertEqual(decision["reason"], "private_history_incomplete")

    def test_existing_private_conversation_blocks_cold_dm(self):
        self.db.execute(
            """INSERT INTO history_coverage(
                coverage_id, account_id, person_id, surface, coverage_status
            ) VALUES ('coverage-1', 'acct-a', 'person-1', 'dm', 'complete')"""
        )
        self.db.execute(
            """INSERT INTO messages(
                message_id, account_id, person_id, direction, message_kind, body,
                occurred_at, observed_at, source
            ) VALUES ('prior-inbound', 'acct-a', 'person-1', 'inbound', 'dm',
                      'old message', '2026-09-01T12:00:00Z', '2026-09-09T12:00:00Z', 'import')"""
        )
        decision = store.eligibility(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="dm",
            workflow="lead",
        )
        self.assertEqual(decision["reason"], "conversation_needs_review")

    def test_reservation_is_account_scoped_and_retry_safe(self):
        first = store.reserve_action(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            target_key="post:123",
            policy_scope="casual",
            post_id="post-123",
        )
        second = store.reserve_action(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            target_key="post:123",
            policy_scope="casual",
            post_id="post-123",
        )
        other_account = store.reserve_action(
            self.db,
            account_id="acct-b",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            target_key="post:123",
            policy_scope="casual",
            post_id="post-123",
        )
        self.assertEqual(first["reason"], "reserved")
        self.assertEqual(second["reason"], "action_already_reserved")
        self.assertEqual(other_account["reason"], "author_action_pending")

    def test_markdown_history_export_preserves_exact_text(self):
        body = "first line\nsecond line with `code`"
        self.db.execute(
            """INSERT INTO messages(
                message_id, account_id, person_id, direction, message_kind, body,
                occurred_at, observed_at, source
            ) VALUES ('message-export', 'acct-a', 'person-1', 'inbound', 'dm', ?,
                      '2026-09-09T12:02:00Z', '2026-09-09T12:02:01Z', 'manual')""",
            (body,),
        )
        self.db.commit()
        target = Path(self.tmp.name) / "history.md"
        store.export_history(self.db_path, account_id="acct-a", person_id="person-1", output=target)
        exported = target.read_text(encoding="utf-8")
        self.assertIn(body, exported)
        self.assertIn("absence from this export does not mean no earlier contact", exported)

    def test_confirmed_send_creates_private_safe_outbox_once(self):
        reservation = store.reserve_action(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            target_key="post:123",
            policy_scope="casual",
            post_id="post-123",
        )
        recorded = store.record_confirmed_send(
            self.db,
            action_id=reservation["action_id"],
            body="exact private message text",
            message_kind="casual_comment",
            platform_reference="urn:li:comment:confirmed",
            confirmation_method="test_confirmation",
            occurred_at=store._time("2026-09-09T14:00:00Z"),
        )
        payload = json.loads(
            self.db.execute(
                "SELECT payload_json FROM analytics_outbox WHERE event_id = ?",
                (recorded["outbox_event_id"],),
            ).fetchone()[0]
        )
        self.assertEqual(payload["platform"], "linkedin")
        self.assertEqual(payload["reply_surface"], "public_comment")
        self.assertNotIn("exact private message text", json.dumps(payload))
        self.assertNotIn("Target Person", json.dumps(payload))
        with self.assertRaises(ValueError):
            store.record_confirmed_send(
                self.db,
                action_id=reservation["action_id"],
                body="exact private message text",
                message_kind="casual_comment",
                platform_reference="urn:li:comment:confirmed",
                confirmation_method="test_confirmation",
            )

    def test_uncertain_action_has_no_message_or_outbox(self):
        reservation = store.reserve_action(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            target_key="post:123",
            policy_scope="casual",
            post_id="post-123",
        )
        store.record_action_result(
            self.db,
            action_id=reservation["action_id"],
            state="uncertain",
            error_message="submitted but not visibly confirmed",
        )
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM analytics_outbox").fetchone()[0], 0)

    def test_daily_cap_counts_confirmed_public_replies(self):
        reservation = store.reserve_action(
            self.db,
            account_id="acct-a",
            person_id="person-1",
            action_type="comment",
            workflow="casual",
            target_key="post:123",
            policy_scope="casual",
            post_id="post-123",
        )
        store.record_confirmed_send(
            self.db,
            action_id=reservation["action_id"],
            body="a reply",
            message_kind="casual_comment",
            platform_reference="urn:li:comment:daily",
            confirmation_method="test_confirmation",
            occurred_at=store._time("2026-09-09T14:00:00Z"),
        )
        count = store.public_reply_count_for_day(
            self.db,
            account_id="acct-a",
            now=store._time("2026-09-09T20:00:00Z"),
        )
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
