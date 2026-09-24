import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from crm.activity import Ledger
from crm.cli import ROOT
from crm.posthog_export import prepare, reconcile

spec = importlib.util.spec_from_file_location("linkedin_export_store", ROOT / "linkedin/store.py")
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class PosthogExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "source.sqlite3"
        store.initialize(self.path)
        self.db = store.connect(self.path)
        self.db.execute(
            "INSERT INTO accounts(account_id,public_identifier,display_name,first_detected_at,last_detected_at) VALUES('a','private-handle','Private Name','2026-09-09','2026-09-09')"
        )
        self.db.execute(
            "INSERT INTO people(person_id,canonical_name,normalized_name,created_at,updated_at) VALUES('p','Private Target','private target','2026-09-09','2026-09-09')"
        )
        self.db.execute(
            "INSERT INTO action_intents(action_id,idempotency_key,account_id,person_id,action_type,workflow,policy_scope,reserved_at) VALUES('act','unique','a','p','comment','casual','private@example.com','2026-09-09')"
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def confirm(self):
        store.record_confirmed_send(
            self.db,
            action_id="act",
            body="Private message body",
            message_kind="casual_comment",
            platform_reference="https://example.com/private",
            confirmation_method="synthetic_test",
            occurred_at=datetime(2026, 9, 9, 12, tzinfo=timezone.utc),
        )

    def test_receipt_to_payload_replay_privacy_and_reconciliation(self):
        self.confirm()
        before = "\n".join(self.db.iterdump())
        first = prepare(self.path, "a", "2026-09-09")
        self.assertEqual(first, prepare(self.path, "a", "2026-09-09"))
        self.assertEqual(before, "\n".join(self.db.iterdump()))
        self.assertEqual(first["prepared_events"], 1)
        exported = json.dumps(first)
        for private in (
            "Private Name",
            "Private Target",
            "private@example.com",
            "Private message body",
            "https://example.com/private",
        ):
            self.assertNotIn(private, exported)
        event_id = first["events"][0]["uuid"]
        self.assertFalse(reconcile(first, [])["complete"])
        self.assertTrue(reconcile(first, [event_id])["complete"])
        self.assertFalse(reconcile(first, [event_id, event_id])["complete"])
        self.assertFalse(reconcile(first, [event_id, "unexpected"])["complete"])

    def test_no_receipts_is_not_end_to_end_success(self):
        result = prepare(self.path, "a", "2026-09-09")
        self.assertEqual(result["prepared_events"], 0)
        self.assertFalse(reconcile(result, [])["complete"])
        with self.assertRaises(ValueError):
            prepare(self.path, "wrong-account", "2026-09-09")

    def test_live_queue_bridge_preserves_receipt_identity(self):
        self.confirm()
        prepared = prepare(self.path, "a", "2026-09-09")
        ledger = Ledger(Path(self.tmp.name) / "activity.sqlite3")
        self.assertEqual(ledger.import_linkedin(self.path)["imported"], 1)
        self.assertEqual(ledger.import_linkedin(self.path)["imported"], 0)
        with ledger.connect() as conn:
            payload = json.loads(conn.execute("SELECT payload_json FROM activity").fetchone()[0])
        self.assertEqual(payload["uuid"], prepared["events"][0]["uuid"])
        self.assertEqual(payload["timestamp"], prepared["events"][0]["timestamp"])
        self.assertEqual(payload["properties"]["entry_type"], "public_reply")
        self.assertEqual(payload["properties"]["program"], "casual_engagement")

    def test_mismatched_or_missing_outbox_is_rejected(self):
        self.confirm()
        self.db.execute("UPDATE analytics_outbox SET payload_json='{}'")
        self.db.commit()
        result = prepare(self.path, "a", "2026-09-09")
        self.assertEqual(result["rejected_receipts"], 1)
        self.assertEqual(result["prepared_events"], 0)
        self.db.execute("DELETE FROM analytics_outbox")
        self.db.commit()
        self.assertEqual(prepare(self.path, "a", "2026-09-09")["rejected_receipts"], 1)

    def test_complete_day_and_missing_database_guards(self):
        with self.assertRaises(ValueError):
            prepare(self.path, "a", datetime.now(timezone.utc).date().isoformat())
        missing = Path(self.tmp.name) / "missing.sqlite3"
        with self.assertRaises(Exception):
            prepare(missing, "a", "2026-09-09")
        self.assertFalse(missing.exists())
        self.confirm()
        self.assertEqual(prepare(self.path, "a", "2026-09-08")["prepared_events"], 0)
