import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.activity import Ledger, PostHog


class FakePostHog:
    def __init__(self):
        self.seen = set()
        self.captured = []
        self.fail = False

    def verify_project(self):
        pass

    def observed(self, event):
        return event["uuid"] in self.seen

    def capture(self, event):
        self.captured.append(event)
        if self.fail:
            raise TimeoutError("credential must never appear in logs")


class ActivityTests(unittest.TestCase):
    def test_posthog_uses_explicit_environment_without_legacy_checkout(self):
        with patch.dict(
            "os.environ",
            {
                "POSTHOG_PROJECT_ID": "121185",
                "POSTHOG_PROJECT_TOKEN": "test-token",
                "POSTHOG_PERSONAL_API_KEY": "test-key",
            },
            clear=True,
        ):
            client = PostHog()
            self.addCleanup(client.http.close)
            self.assertEqual(client.token, "test-token")
            self.assertEqual(client.key, "test-key")
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(ValueError):
            PostHog()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scope = patch("crm.activity.ROOT", self.root)
        self.scope.start()
        self.ledger = Ledger(self.root / "activity.sqlite3")
        self.evidence = self.root / "evidence.json"
        self.evidence.write_text('{"private":"provider readback"}')
        self.facts = dict(
            platform="x",
            account="private-handle",
            provider_id="private-provider-id",
            occurred_at="2026-09-09T13:00:00+00:00",
            kind="dm",
            reviewer="local operator",
            confirmation="provider_readback",
            status="confirmed",
            author_account="private-handle",
            person_id="private-person",
            body="private message body",
            target_reference="private exact recipient",
        )

    def tearDown(self):
        self.scope.stop()
        self.temp.cleanup()

    def test_record_retry_conflict_privacy_and_ingestion(self):
        receipt = self.ledger.record(self.facts, self.evidence)
        self.assertEqual(receipt, self.ledger.record(self.facts, self.evidence))
        with self.assertRaises(ValueError):
            self.ledger.record({**self.facts, "body": "changed"}, self.evidence)
        client = FakePostHog()
        first = self.ledger.sync(client)
        self.assertEqual(first["accepted"], 1)
        self.assertEqual(first["confirmed"], 0)
        self.ledger.sync(client)
        self.assertEqual(client.captured[0], client.captured[1])
        self.assertNotIn("private", json.dumps(client.captured))
        client.seen.add(client.captured[0]["uuid"])
        self.assertEqual(self.ledger.sync(client)["confirmed"], 1)
        self.assertEqual(self.ledger.sync(client)["checked"], 0)

    def test_ambiguous_capture_is_retryable_and_errors_redacted(self):
        self.ledger.record(self.facts, self.evidence)
        client = FakePostHog()
        client.fail = True
        self.assertEqual(self.ledger.sync(client)["failed"], 1)
        with self.ledger.connect() as conn:
            row = conn.execute("SELECT * FROM activity").fetchone()
            self.assertEqual(row["state"], "pending")
            self.assertEqual(row["last_error"], "TimeoutError")
        client.seen.add(client.captured[0]["uuid"])
        self.assertEqual(self.ledger.sync(client)["confirmed"], 1)
        self.assertEqual(len(client.captured), 1)

    def test_reply_parent_and_recipient_are_required(self):
        parent = self.ledger.record(self.facts, self.evidence)
        reply = {
            **self.facts,
            "kind": "inbound_reply",
            "provider_id": "reply1",
            "occurred_at": "2026-09-09T15:00:00Z",
            "in_reply_to": parent,
            "in_reply_to_provider_id": self.facts["provider_id"],
            "reply_classification": "unknown",
        }
        for overrides in (
            {"person_id": "other"},
            {"in_reply_to": "missing"},
            {"in_reply_to_provider_id": "wrong"},
        ):
            with self.assertRaises(ValueError):
                self.ledger.record({**reply, **overrides}, self.evidence)
        self.ledger.record(reply, self.evidence)
        client = FakePostHog()
        self.ledger.sync(client)
        outbound, inbound = client.captured
        self.assertEqual(
            outbound["properties"]["recipient_key"], inbound["properties"]["recipient_key"]
        )
        self.assertEqual(inbound["event"], "gtm.reply_received")

    def test_unconfirmed_wrong_author_future_and_external_evidence_rejected(self):
        for overrides in (
            {"status": "submitted"},
            {"confirmation": "clicked"},
            {"author_account": "other"},
            {"occurred_at": "2999-01-01T00:00:00Z"},
            {"platform": "smartlead"},
        ):
            with self.assertRaises(ValueError):
                self.ledger.record({**self.facts, **overrides}, self.evidence)
        with self.assertRaises(ValueError):
            self.ledger.record(self.facts, self.root / "missing")

    def test_empty_status_is_not_a_fake_send_and_snapshot_replay_is_stable(self):
        client = FakePostHog()
        first = self.ledger.snapshot(client)
        self.assertEqual(first["accepted"], 5)
        self.assertTrue(all(e["event"] == "gtm.local_activity_snapshot" for e in client.captured))
        self.assertTrue(all(e["properties"]["total"] == 0 for e in client.captured))
        self.assertEqual(self.ledger.status(), [])
        saved = list(client.captured)
        client.seen.update(e["uuid"] for e in saved)
        self.assertEqual(self.ledger.snapshot(client)["confirmed"], 5)
        with self.ledger.connect() as conn:
            self.assertEqual(
                conn.execute("SELECT count(*) FROM snapshots WHERE state='confirmed'").fetchone()[
                    0
                ],
                5,
            )

    def test_x_dm_reconciliation_counts_conversations_and_links_real_reply(self):
        evidence = self.root / "dm-reconciliation.json"
        evidence.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "account": "jasonfesta",
                    "account_profile_url": "https://x.com/jasonfesta",
                    "observed_outbound_message_count": 3,
                    "conversations": [
                        {
                            "conversation_id": "1-2",
                            "name": "One",
                            "handle": "one",
                            "profile_url": "https://x.com/one",
                            "conversation_url": "https://x.com/i/chat/1-2",
                            "all_message_ids": [
                                "00000000-0000-4000-8000-000000000001",
                                "00000000-0000-4000-8000-000000000002",
                            ],
                            "outbound": {
                                "message_id": "00000000-0000-4000-8000-000000000001",
                                "occurred_at": "2026-09-21T21:00:00Z",
                                "body": "outbound",
                            },
                            "reply": {
                                "message_id": "00000000-0000-4000-8000-000000000002",
                                "occurred_at": "2026-09-21T21:05:00Z",
                                "body": "reply",
                                "classification": "human",
                            },
                        },
                        {
                            "conversation_id": "1-3",
                            "name": "Two",
                            "handle": "two",
                            "profile_url": "https://x.com/two",
                            "conversation_url": "https://x.com/i/chat/1-3",
                            "all_message_ids": ["00000000-0000-4000-8000-000000000003"],
                            "outbound": {
                                "message_id": "00000000-0000-4000-8000-000000000003",
                                "occurred_at": "2026-09-21T21:01:00Z",
                                "body": "outbound two",
                            },
                        },
                    ],
                }
            )
        )
        result = self.ledger.import_x_dm_reconciliation(evidence)
        self.assertEqual(result["conversations"], 2)
        self.assertEqual(result["replies"], 1)
        self.assertEqual(result["observed_outbound_messages"], 3)
        self.assertEqual(self.ledger.import_x_dm_reconciliation(evidence)["conversations"], 2)
        client = FakePostHog()
        self.ledger.sync(client)
        self.assertEqual(
            [event["event"] for event in client.captured],
            ["gtm.message_sent", "gtm.reply_received", "gtm.message_sent"],
        )
        snapshot = self.ledger.snapshot(client)
        self.assertEqual(snapshot["accepted"], 5)
        dm = next(
            event
            for event in client.captured
            if event["event"] == "gtm.local_activity_snapshot"
            and event["properties"]["platform"] == "x"
            and event["properties"]["entry_type"] == "dm"
        )
        self.assertEqual(dm["properties"]["total"], 2)
        self.assertEqual(dm["properties"]["reply_total"], 1)

    def gmail_fixture(self):
        (self.root / "data").mkdir()
        with sqlite3.connect(self.root / "data/crm.sqlite3") as conn:
            conn.executescript("""
                CREATE TABLE email_preparations(preparation_id,account,state,person_id,exact_address,
                    gmail_thread_id,created_at,draft_id,revision);
                CREATE TABLE copy_revisions(draft_id,revision,subject,body);
                INSERT INTO email_preparations VALUES('prep','sender@example.com','gmail_draft','person',
                    'recipient@example.com','thread','2026-09-08T00:00:00Z','draft',1);
                INSERT INTO copy_revisions VALUES('draft',1,'subject','reviewed body');
            """)
        return dict(
            platform="gmail",
            account="sender@example.com",
            provider_id="gmail-sent",
            occurred_at="2026-09-09T13:00:00Z",
            kind="email",
            reviewer="operator",
            confirmation="provider_readback",
            status="confirmed",
            preparation_id="prep",
            thread_id="thread",
            rfc_message_id="<sent@example.com>",
            person_id="person",
            labels=["SENT"],
            from_address="sender@example.com",
            to_addresses=["recipient@example.com"],
            subject="subject",
            body="reviewed body",
        )

    def test_gmail_requires_exact_saved_copy_and_sent_readback(self):
        facts = self.gmail_fixture()
        for change in (
            {"body": "unreviewed"},
            {"labels": ["DRAFT"]},
            {"person_id": "wrong"},
            {"thread_id": "wrong"},
            {"to_addresses": ["other@example.com"]},
        ):
            with self.assertRaises(ValueError):
                self.ledger.record({**facts, **change}, self.evidence)
        self.ledger.record(facts, self.evidence)
        client = FakePostHog()
        self.ledger.sync(client)
        self.assertNotIn("@", json.dumps(client.captured))
        self.assertNotIn("reviewed body", json.dumps(client.captured))

    def test_gmail_reply_requires_original_rfc_link_and_automation_check(self):
        facts = self.gmail_fixture()
        parent = self.ledger.record(facts, self.evidence)
        reply = {
            **facts,
            "provider_id": "gmail-reply",
            "kind": "inbound_reply",
            "labels": ["INBOX"],
            "from_address": "recipient@example.com",
            "to_addresses": ["sender@example.com"],
            "rfc_message_id": "<reply@example.com>",
            "in_reply_to": parent,
            "in_reply_to_provider_id": "gmail-sent",
            "in_reply_to_rfc_message_id": "<sent@example.com>",
            "reply_classification": "human",
            "auto_submitted": "no",
        }
        for change in ({"auto_submitted": "auto-replied"}, {"in_reply_to_rfc_message_id": "wrong"}):
            with self.assertRaises(ValueError):
                self.ledger.record({**reply, **change}, self.evidence)
        self.ledger.record(reply, self.evidence)


if __name__ == "__main__":
    unittest.main()
