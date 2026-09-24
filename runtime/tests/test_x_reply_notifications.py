import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.human_inbox import Watcher, digest

AT = "2026-09-21T16:00:00+00:00"


class XNotificationWatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.watcher = Watcher(self.root / "watcher.sqlite3")
        self.shared = self.root / "shared.sqlite3"
        with sqlite3.connect(self.shared) as conn:
            conn.executescript((Path(__file__).resolve().parents[1] / "sql/schema.sql").read_text())
            conn.execute(
                "INSERT INTO people(person_id,full_name,normalized_name,identity_status) VALUES ('p1','Person','person','verified')"
            )
            conn.execute(
                """INSERT INTO contact_points(contact_id,person_id,contact_type,value,
                   normalized_value,verification_status,first_seen_at)
                   VALUES ('cp1','p1','x','https://x.com/them','x.com/them','confirmed',?)""",
                (AT,),
            )
            conn.execute(
                """INSERT INTO sources(source_id,url,source_type,quality_tier,accessed_at)
                   VALUES ('s1','https://x.com/operator/status/123','x',1,?)""",
                (AT,),
            )
            conn.execute(
                """INSERT INTO evidence_claims(claim_id,entity_type,entity_id,field_name,
                   claim_value,source_id,evidence_relation,verification_status,confidence)
                   VALUES ('cl1','person','p1','public_reply_location',
                   'https://x.com/operator/status/123','s1','mentions','accepted',100)"""
            )

    def tearDown(self):
        self.temp.cleanup()

    def factory(self):
        conn = sqlite3.connect(self.shared)
        conn.row_factory = sqlite3.Row
        return conn

    def capture(self):
        return {
            "binding": {
                "account": "operator",
                "observed_at": AT,
                "evidence": "x account menu showed @operator",
            },
            "source": {
                "url": "https://x.com/notifications",
                "tab": "all",
                "observed_at": AT,
                "readback_complete": True,
            },
            "notifications": [
                {
                    "interaction": "reply",
                    "actor_handle": "@Them",
                    "provider_id": "456",
                    "target_url": "https://x.com/operator/status/123",
                    "interaction_url": "https://x.com/them/status/456",
                    "occurred_at": AT,
                    "body": "yes, how are you handling tool selection?",
                    "evidence": "x-notifications-dom:reply-456",
                },
                {
                    "interaction": "like",
                    "actor_handle": "them",
                    "provider_id": "like:them:123",
                    "target_url": "https://x.com/operator/status/123",
                    "occurred_at": AT,
                    "body": "",
                    "evidence": "x-notifications-dom:like-them-123",
                },
            ],
        }

    def event_id(self, provider_id):
        return "xn_" + digest(["operator", provider_id])

    def test_logs_every_like_and_reply_once_in_local_and_shared_crm(self):
        first = self.watcher.ingest_x_notifications(
            self.capture(), expected_account="operator", shared_factory=self.factory
        )
        second = self.watcher.ingest_x_notifications(
            self.capture(), expected_account="operator", shared_factory=self.factory
        )
        self.assertEqual(first["observed"], 2)
        self.assertEqual(first["shared_confirmed"], 2)
        self.assertEqual(second["shared_confirmed"], 2)
        with self.watcher.connection() as conn:
            rows = conn.execute(
                "SELECT interaction,identity_status,target_status,shared_state FROM x_notification_events ORDER BY interaction"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(
                all(tuple(row)[1:] == ("matched", "registered", "confirmed") for row in rows)
            )
        with self.factory() as conn:
            rows = conn.execute(
                "SELECT outcome,notes FROM outreach_events ORDER BY outcome"
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(
                {json.loads(row["notes"])["interaction"] for row in rows}, {"like", "reply"}
            )

    def test_unknown_time_is_preserved_and_repoll_does_not_change_first_observation(self):
        from crm.relationships import Relationships

        with self.factory() as conn:
            root = Path(__file__).resolve().parents[1]
            conn.executescript((root / "sql/tags_schema.sql").read_text())
            conn.executescript((root / "sql/relationships_schema.sql").read_text())
        capture = self.capture()
        capture["notifications"] = [capture["notifications"][1]]
        del capture["notifications"][0]["occurred_at"]
        first = self.watcher.ingest_x_notifications(
            capture, expected_account="operator", shared_factory=self.factory
        )
        capture["source"]["observed_at"] = "2026-09-21T17:00:00+00:00"
        second = self.watcher.ingest_x_notifications(
            capture, expected_account="operator", shared_factory=self.factory
        )
        self.assertEqual(first["shared_confirmed"], 1)
        self.assertEqual(second["shared_confirmed"], 1)
        row = Relationships(self.shared).list("human", "p1")[0]
        self.assertEqual(len(row["contact_history"]), 1)
        self.assertIsNone(row["last_like_at"])
        self.assertEqual(row["last_contact"][0]["time_basis"], "observed")
        self.assertTrue(row["last_contact"][0]["observed_at"].startswith("2026-09-21T16:00"))

    def test_unmatched_event_is_retained_without_fabricating_shared_person(self):
        capture = self.capture()
        capture["notifications"] = [
            {
                **capture["notifications"][0],
                "actor_handle": "unknown_person",
                "provider_id": "789",
                "interaction_url": "https://x.com/unknown_person/status/789",
            }
        ]
        result = self.watcher.ingest_x_notifications(
            capture, expected_account="operator", shared_factory=self.factory
        )
        self.assertEqual(result["unmatched"], 1)
        with self.watcher.connection() as conn:
            row = conn.execute("SELECT * FROM x_notification_events").fetchone()
            self.assertEqual(row["identity_status"], "unmatched")
            self.assertEqual(row["shared_state"], "confirmed")
        with self.factory() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 0)
            self.assertEqual(
                conn.execute("SELECT count(*) FROM x_engagement_events").fetchone()[0], 1
            )

    def test_shared_failure_holds_but_does_not_erase_observation(self):
        def unavailable():
            raise RuntimeError("offline")

        result = self.watcher.ingest_x_notifications(
            self.capture(), expected_account="operator", shared_factory=unavailable
        )
        self.assertEqual(result["held"], 2)
        with self.watcher.connection() as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM x_notification_events").fetchone()[0], 2
            )
            self.assertEqual(
                {row[0] for row in conn.execute("SELECT shared_state FROM x_notification_events")},
                {"held"},
            )

    def test_reply_prepares_public_reply_and_dm_but_like_has_no_public_reply(self):
        self.watcher.ingest_x_notifications(
            self.capture(), expected_account="operator", shared_factory=self.factory
        )
        reply_event = self.event_id("456")
        like_event = self.event_id("like:them:123")

        def generated(prompt, **_):
            text = (
                "happy to compare notes on that"
                if "public reply" in prompt
                else "thanks for engaging, curious what you are building"
            )
            return {"text": json.dumps({"text": text}), "model": "gpt-4o-mini"}

        public_id = self.watcher.prepare_x_action(reply_event, "public_reply", complete=generated)
        dm_id = self.watcher.prepare_x_action(
            reply_event, "dm", complete=generated, shared_factory=self.factory
        )
        self.assertNotEqual(public_id, dm_id)
        with self.assertRaisesRegex(ValueError, "actual reply"):
            self.watcher.prepare_x_action(like_event, "public_reply", complete=generated)
        like_dm_id = self.watcher.prepare_x_action(
            like_event, "dm", complete=generated, shared_factory=self.factory
        )
        self.assertNotEqual(dm_id, like_dm_id)
        self.assertEqual(len(self.watcher.pending_x_actions()), 3)

    def test_action_requires_exact_approval_and_provider_readback(self):
        self.watcher.ingest_x_notifications(
            self.capture(), expected_account="operator", shared_factory=self.factory
        )

        def generated(*_, **__):
            return {
                "text": json.dumps({"text": "happy to compare notes on that"}),
                "model": "gpt-4o-mini",
            }

        proposal = self.watcher.prepare_x_action(
            self.event_id("456"), "public_reply", complete=generated
        )
        row = self.watcher.pending_x_actions()[0]
        with self.assertRaisesRegex(ValueError, "changed"):
            self.watcher.approve_x_action(proposal, approver="operator", content_sha256="wrong")
        self.watcher.approve_x_action(
            proposal,
            approver="operator",
            content_sha256=digest(row["payload"]),
        )
        receipt = {
            "provider_id": "999",
            "confirmed_at": AT,
            "account": "operator",
            "author_handle": "operator",
            "url": "https://x.com/operator/status/999",
            "body": row["payload"]["text"],
            "evidence": "post-send X readback",
        }
        receipt_id = self.watcher.confirm_x_action(proposal, receipt)
        self.assertEqual(receipt_id, self.watcher.confirm_x_action(proposal, receipt))
        self.assertEqual(len(self.watcher.pending_x_actions("sent")), 1)

    def test_each_new_engagement_can_continue_dm_and_sync_confirmed_receipt(self):
        self.watcher.ingest_x_notifications(
            self.capture(), expected_account="operator", shared_factory=self.factory
        )
        reply_event = self.event_id("456")

        def generated(*_, **__):
            return {
                "text": json.dumps({"text": "thanks for the reply, curious what you are building"}),
                "model": "test-model",
            }

        proposal = self.watcher.prepare_x_action(
            reply_event, "dm", complete=generated, shared_factory=self.factory
        )
        row = self.watcher.pending_x_actions()[0]
        self.watcher.approve_x_action(
            proposal,
            approver="standing-engagement-dm-rule",
            content_sha256=digest(row["payload"]),
        )
        receipt = {
            "provider_id": "dm-999",
            "confirmed_at": AT,
            "account": "operator",
            "author_handle": "operator",
            "body": row["payload"]["text"],
            "evidence": "post-send X DM readback",
        }
        self.watcher.confirm_x_action(proposal, receipt)
        shared_event = self.watcher.sync_x_action_history(proposal, shared_factory=self.factory)
        self.assertEqual(
            self.watcher.sync_x_action_history(proposal, shared_factory=self.factory),
            shared_event,
        )
        with self.factory() as conn:
            saved = conn.execute(
                "SELECT notes FROM outreach_events WHERE event_id=?", (shared_event,)
            ).fetchone()
            self.assertEqual(json.loads(saved["notes"])["action_kind"], "dm")

        capture = self.capture()
        capture["notifications"] = [
            {
                **capture["notifications"][0],
                "provider_id": "457",
                "interaction_url": "https://x.com/them/status/457",
            }
        ]
        self.watcher.ingest_x_notifications(
            capture, expected_account="operator", shared_factory=self.factory
        )
        second_event = "xn_" + digest(["operator", "457"])
        second_proposal = self.watcher.prepare_x_action(
            second_event,
            "dm",
            complete=generated,
            shared_factory=self.factory,
        )
        self.assertNotEqual(proposal, second_proposal)
        with self.assertRaisesRegex(ValueError, "engagement_x_dm_already_proposed_or_sent"):
            self.watcher.prepare_x_action(
                second_event,
                "dm",
                complete=generated,
                shared_factory=self.factory,
            )

    def test_reply_reserves_one_body_free_slack_self_dm_alert(self):
        self.watcher.ingest_x_notifications(
            self.capture(), expected_account="operator", shared_factory=self.factory
        )
        destination = "https://app.slack.com/client/T123/D456?cdn_fallback=1"
        event_id = self.event_id("456")
        alert = self.watcher.prepare_slack_reply_alert(event_id, destination)
        self.assertEqual(self.watcher.prepare_slack_reply_alert(event_id, destination), alert)
        pending = self.watcher.pending_slack_reply_alerts()
        self.assertEqual(len(pending), 1)
        self.assertFalse(pending[0]["payload"]["message_body_included"])
        self.assertNotIn("yes, how are you handling", json.dumps(pending[0]["payload"]))
        with self.assertRaisesRegex(ValueError, "not likes"):
            self.watcher.prepare_slack_reply_alert(self.event_id("like:them:123"), destination)
        receipt = {
            "destination_url": destination,
            "confirmed_at": AT,
            "evidence": "Slack self-DM readback",
            "message_url": "https://darwinstudios.slack.com/archives/D456/p1",
        }
        self.assertEqual(self.watcher.confirm_slack_reply_alert(alert, receipt), alert)
        self.assertEqual(self.watcher.confirm_slack_reply_alert(alert, receipt), alert)
        self.assertEqual(len(self.watcher.pending_slack_reply_alerts("confirmed")), 1)


if __name__ == "__main__":
    unittest.main()
