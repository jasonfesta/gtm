"""Offline failure/recovery proof using the real CRM schema and receipt writer."""

import datetime as dt
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from crm import database
from crm.human_reply_capture import CRMGuard
from crm.reply_queue import ReplyQueue

AT = dt.datetime(2026, 9, 21, 20, 0, tzinfo=dt.timezone.utc)


def item(number=1, handle=None):
    handle = handle or f"builder{number}"
    return {
        "id": str(number),
        "handle": handle,
        "url": f"https://x.com/{handle}/status/{number}",
        "reply": "the recovery behavior is the interesting part.",
        "person_id": f"person_{handle}",
        "new_contact": True,
        "fingerprint": f"fingerprint{number}",
        "lane": "reads_",
    }


def receipt(post):
    provider_id = str(9000 + int(post["id"]))
    return {
        "id": post["id"],
        "status": "confirmed",
        "provider_id": provider_id,
        "reply_url": f"https://x.com/jasonfesta/status/{provider_id}",
        "account": "jasonfesta",
        "account_profile_url": "https://x.com/jasonfesta",
        "observed_at": AT.isoformat(),
    }


class ReplyQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.queue_path = Path(self.tmp.name) / "queue.sqlite3"
        self.crm_path = Path(self.tmp.name) / "crm.sqlite3"
        source = database.schema_connection()
        target = sqlite3.connect(self.crm_path)
        source.backup(target)
        target.close()
        source.close()
        self.queue = ReplyQueue(self.queue_path)

    def factory(self):
        db = sqlite3.connect(self.crm_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def events(self):
        db = self.factory()
        try:
            return db.execute("SELECT * FROM outreach_events").fetchall()
        finally:
            db.close()

    def add(self, *items):
        return self.queue.enqueue({"items": list(items)}, at=AT)

    def test_fifteen_posts_logged_once_after_restart(self):
        self.add(*(item(n) for n in range(1, 16)))
        calls = []

        def sender(post):
            calls.append(post["id"])
            return receipt(post)

        for _ in range(15):
            self.queue.send_one(sender)
        self.assertEqual(len(self.events()), 0)  # CRM is not in the sender path.
        resumed = ReplyQueue(self.queue_path)
        self.assertIsNone(resumed.send_one(sender))
        self.assertEqual(len(resumed.flush(connection_factory=self.factory)), 15)
        self.assertEqual(resumed.flush(connection_factory=self.factory), [])
        self.assertEqual(len(calls), 15)
        self.assertEqual(len(self.events()), 15)
        self.assertEqual({r["state"] for r in resumed.jobs()}, {"logged"})

    def test_provider_accepted_then_process_died_never_resends(self):
        self.add(item())
        provider_receipts = []

        def interrupted(post):
            provider_receipts.append(receipt(post))
            raise RuntimeError("died before saving receipt")

        with self.assertRaises(RuntimeError):
            self.queue.send_one(interrupted)
        resumed = ReplyQueue(self.queue_path)
        self.assertIsNone(resumed.send_one(interrupted))
        self.assertEqual(len(provider_receipts), 1)
        job = resumed.jobs()[0]
        self.assertEqual(job["state"], "sending")
        resumed.confirm(job["parent_id"], job["token"], provider_receipts[0])
        resumed.flush(connection_factory=self.factory)
        self.assertEqual(len(self.events()), 1)

    def test_crm_commit_then_ack_crash_replays_without_duplicate(self):
        self.add(item())
        self.queue.send_one(receipt)

        def crash():
            raise RuntimeError("died after CRM commit")

        with self.assertRaises(RuntimeError):
            self.queue.flush(connection_factory=self.factory, after_write=crash)
        self.assertEqual(len(self.events()), 1)
        resumed = ReplyQueue(self.queue_path)
        resumed.flush(connection_factory=self.factory)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(resumed.jobs()[0]["state"], "logged")

    def test_crm_outage_keeps_receipt_for_retry(self):
        self.add(item())
        self.queue.send_one(receipt)

        def offline():
            raise OSError("offline")

        with self.assertRaises(OSError):
            self.queue.flush(connection_factory=offline)
        self.assertEqual(self.queue.jobs()[0]["state"], "posted")
        self.assertIsNone(self.queue.claim())
        self.queue.flush(connection_factory=self.factory)
        self.assertEqual(len(self.events()), 1)

    def test_partial_crm_batch_failure_replays(self):
        self.add(item(1), item(2))
        self.queue.send_one(receipt)
        self.queue.send_one(receipt)
        count = 0

        def partial():
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("offline mid-batch")
            return self.factory()

        with self.assertRaises(OSError):
            self.queue.flush(connection_factory=partial)
        self.assertEqual(len(self.events()), 1)
        self.queue.flush(connection_factory=self.factory)
        self.assertEqual(len(self.events()), 2)

    def test_pending_and_recent_handle_dedupe(self):
        self.assertEqual(self.add(item()), ["1"])
        self.assertEqual(self.add(item(), item(2, "BUILDER1")), [])
        self.queue.send_one(receipt)
        self.assertEqual(self.add(item(3, "builder1")), [])
        self.assertEqual(
            self.queue.enqueue({"items": [item(3, "builder1")]}, at=AT + dt.timedelta(hours=72)),
            ["3"],
        )

    def test_concurrent_claimers_have_one_owner(self):
        self.add(item())
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: self.queue.claim(), range(2)))
        self.assertEqual(sum(c is not None for c in claims), 1)

    def test_invalid_or_conflicting_receipt_cannot_advance(self):
        self.add(item())
        claim = self.queue.claim()
        invalid = {**receipt(item()), "account": "someone_else"}
        with self.assertRaises(ValueError):
            self.queue.confirm("1", claim["token"], invalid)
        self.assertEqual(self.queue.jobs()[0]["state"], "sending")
        self.queue.confirm("1", claim["token"], receipt(item()))
        self.queue.confirm("1", claim["token"], receipt(item()))
        with self.assertRaises(ValueError):
            self.queue.confirm(
                "1",
                claim["token"],
                {**receipt(item()), "observed_at": (AT + dt.timedelta(seconds=1)).isoformat()},
            )

    def test_existing_crm_guard_checks_72_hours_and_allows_absent_handle(self):
        self.add(item())
        self.queue.send_one(receipt)
        self.queue.flush(connection_factory=self.factory)
        guard = CRMGuard(connection_factory=self.factory)
        checks = guard.check({"builder1", "new_handle"}, at=AT + dt.timedelta(hours=71))
        self.assertIn("cross_channel_cooldown", checks["builder1"]["reason"])
        self.assertTrue(checks["new_handle"]["allowed"])
        after = guard.check({"builder1"}, at=AT + dt.timedelta(hours=72))
        self.assertNotIn("cross_channel_cooldown", after["builder1"]["reason"])
        # Existing cold-outreach caps still apply; this prototype does not relax them.
        self.assertIn("channel_touch_limit", after["builder1"]["reason"])


if __name__ == "__main__":
    unittest.main()
