import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.activity import Ledger
from crm.x_gtm_blob import build_blob, posthog_event, queue_posthog_event


class XGTMBlobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "data").mkdir()
        self.pairs = self.root / "pairs.json"
        self.pairs.write_text(
            json.dumps(
                {
                    "account": "jasonfesta",
                    "observed_at": "2026-09-22T00:40:00Z",
                    "coverage_note": "bounded",
                    "pairs": [
                        {
                            "reply_url": "https://x.com/jasonfesta/status/10",
                            "posted_at": "2026-09-22T00:10:00Z",
                            "reply_text": "outbound reply",
                            "parent_author": "person",
                            "parent_url": "https://x.com/person/status/9",
                        }
                    ],
                }
            )
        )
        self.dms = self.root / "dms.json"
        self.dms.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "account": "jasonfesta",
                    "checked_at": "2026-09-22T00:41:00Z",
                    "coverage": "one conversation",
                    "observed_outbound_message_count": 2,
                    "conversations": [
                        {
                            "conversation_id": "1-2",
                            "name": "Person",
                            "handle": "person",
                            "profile_url": "https://x.com/person",
                            "conversation_url": "https://x.com/i/chat/1-2",
                            "all_message_ids": [
                                "00000000-0000-4000-8000-000000000001",
                                "00000000-0000-4000-8000-000000000002",
                            ],
                            "outbound": {
                                "message_id": "00000000-0000-4000-8000-000000000001",
                                "occurred_at": "2026-09-22T00:11:00Z",
                                "body": "private outbound",
                            },
                        }
                    ],
                }
            )
        )
        self.overlays = self.root / "overlays.json"
        self.overlays.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "account": "jasonfesta",
                    "observed_at": "2026-09-22T00:45:00Z",
                    "observations": [
                        {
                            "conversation_id": "1-2",
                            "native_message_id": None,
                            "occurred_at": "2026-09-22T00:45:00Z",
                            "body": "private inbound",
                            "classification": "human",
                        }
                    ],
                }
            )
        )
        self.reply_db = self.root / "reply.sqlite3"
        with sqlite3.connect(self.reply_db) as connection:
            connection.execute(
                """CREATE TABLE x_notification_events (
                   event_id TEXT,account TEXT,provider_id TEXT,interaction TEXT,
                   actor_handle TEXT,target_url TEXT,interaction_url TEXT,
                   occurred_at TEXT,facts TEXT,person_id TEXT,identity_status TEXT,
                   target_status TEXT,shared_state TEXT)"""
            )
            facts = {
                "body": "private public reply",
                "interaction": "reply",
            }
            connection.execute(
                "INSERT INTO x_notification_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "event-1",
                    "jasonfesta",
                    "11",
                    "reply",
                    "person",
                    "https://x.com/jasonfesta/status/10",
                    "https://x.com/person/status/11",
                    "2026-09-22T00:20:00Z",
                    json.dumps(facts),
                    None,
                    "unmatched",
                    "unregistered",
                    "confirmed",
                ),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_combines_sorts_and_summarizes_private_evidence(self):
        blob = build_blob(
            self.pairs,
            self.reply_db,
            self.dms,
            self.overlays,
            root=self.root,
        )
        self.assertEqual(blob["summary"]["replies"]["authored_total"], 1)
        self.assertEqual(blob["summary"]["replies"]["successful_authored_total"], 1)
        self.assertEqual(blob["summary"]["dms"]["conversation_total"], 1)
        self.assertEqual(blob["summary"]["dms"]["observed_replied_conversation_total"], 1)
        self.assertEqual(blob["summary"]["pending_provider_id_total"], 1)
        self.assertEqual(blob["summary"]["dms"]["normalized_outbound_message_total"], 2)
        self.assertEqual(blob["summary"]["normalized_record_total"], 5)
        self.assertEqual(blob["records"][0]["body"], "private inbound")
        self.assertIn("private outbound", json.dumps(blob["raw_sources"]))
        missing_body = next(
            item
            for item in blob["records"]
            if item["provider_id"] == "00000000-0000-4000-8000-000000000002"
        )
        self.assertEqual(missing_body["body_status"], "not_present_in_reconciliation_source")

    def test_posthog_event_is_aggregate_only_and_queue_is_idempotent(self):
        blob = build_blob(
            self.pairs,
            self.reply_db,
            self.dms,
            self.overlays,
            root=self.root,
        )
        ledger = Ledger(self.root / "activity.sqlite3")
        event = posthog_event(blob, ledger)
        rendered = json.dumps(event)
        self.assertNotIn("private outbound", rendered)
        self.assertNotIn("private inbound", rendered)
        self.assertEqual(event["properties"]["reply_outbound_total"], 1)
        self.assertEqual(event["properties"]["dm_outbound_message_total"], 2)
        self.assertEqual(event["properties"]["dm_message_body_available_total"], 2)
        self.assertEqual(event["properties"]["dm_success_total"], 1)
        self.assertEqual(queue_posthog_event(blob, ledger), event["uuid"])
        self.assertEqual(queue_posthog_event(blob, ledger), event["uuid"])
        with ledger.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM snapshots").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
