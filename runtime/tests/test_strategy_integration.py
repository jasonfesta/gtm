"""Cross-strategy rehearsal using actual local modules and synthetic records only."""

import sqlite3
import unittest
from pathlib import Path

import test_human_inbox as reply_fixture
import test_human_reply_planning as timeline_fixture
import test_outreach_queue as outbound_fixture

from crm import database
from crm.human_inbox import Watcher as ReplyWatcher
from crm.human_reply_planning import Watcher as TimelineWatcher
from crm.outreach_queue import DailyOutbound


class StrategyIntegrationTests(unittest.TestCase):
    def test_incoming_reply_stops_prepared_outbound_and_survives_restore(self):
        fixture = outbound_fixture.DailyOutboundTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture.prepared()
        self.assertEqual(sum(s["state"] == "due_review" for s in fixture.plan()["steps"]), 1)
        path = Path(fixture.temp.name) / "replies.sqlite3"
        watcher = ReplyWatcher(path)
        stream = watcher.configure("x", "sender", {"id": "me"}, "task-dm", 0)
        watcher.associate(
            "x",
            "sender",
            "task-thread",
            "them",
            "p",
            "synthetic identity",
            verify_person=lambda p: p == "p",
        )
        message = reply_fixture.message(
            account="sender", conversation="task-thread", occurred_at=outbound_fixture.NOW
        )
        self.assertEqual(
            watcher.poll(stream, reply_fixture.Reader([message]))["status"], "complete"
        )
        event_id = reply_fixture.digest(["x", "sender", "m1"])
        watcher.review(event_id, "human", "synthetic reviewer", "actual fixture question reviewed")
        result = fixture.workflow.consume_incoming(watcher)
        self.assertEqual(result["confirmed"], 1)
        self.assertTrue(
            all("conversation_needs_review" in s["holds"] for s in fixture.plan()["steps"])
        )
        self.assertEqual(fixture.workflow.consume_incoming(ReplyWatcher(path))["confirmed"], 0)
        restored = Path(fixture.temp.name) / "restored.sqlite3"
        with fixture.workflow.connection() as source, sqlite3.connect(restored) as dest:
            source.backup(dest)
            self.assertEqual(dest.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        fixture.workflow = DailyOutbound(fixture.history, restored)
        self.assertTrue(
            all("conversation_needs_review" in s["holds"] for s in fixture.plan()["steps"])
        )
        with sqlite3.connect(fixture.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 1)
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM outreach_events WHERE direction='outbound'"
                ).fetchone()[0],
                0,
            )

    def test_social_hydration_draft_ignore_restart_and_expiry(self):
        for channel in ("x", "linkedin"):
            with self.subTest(channel=channel):
                fixture = timeline_fixture.TimelineTests()
                fixture.setUp()
                try:
                    key, review = fixture.reviewed(channel)
                    with database.schema_connection() as crm:
                        person = fixture.w.import_lead(
                            key, review, timeline_fixture.AT, fixture_connection=crm
                        )
                        draft = fixture.w.prepare(
                            key,
                            review,
                            {
                                "a": "what context helps rank the agents?",
                                "b": "how do you compare overlapping capabilities?",
                            },
                            "synthetic context review",
                            timeline_fixture.AT,
                        )
                        self.assertFalse(draft["send_capable"])
                        fixture.w.close()
                        fixture.w = TimelineWatcher(fixture.path)
                        self.assertEqual(
                            fixture.w.db.execute("SELECT count(*) FROM approvals").fetchone()[0], 0
                        )
                        self.assertEqual(
                            fixture.w.import_lead(
                                key, review, timeline_fixture.AT, fixture_connection=crm
                            ),
                            person,
                        )
                        self.assertEqual(
                            crm.execute("SELECT count(*) FROM people").fetchone()[0], 1
                        )
                        self.assertEqual(
                            crm.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 0
                        )
                        approval = dict(
                            actor="human",
                            decision_reference="synthetic-only",
                            account=review["account"],
                            target_url=draft["target_url"],
                            target_post_id=key,
                            exact_text=draft["variants"]["a"],
                            decision="approved",
                        )
                        with self.assertRaises(ValueError):
                            fixture.w.record_approval(
                                draft["draft_id"], approval, "2026-09-10T19:16:00+00:00"
                            )
                        self.assertEqual(
                            fixture.w.db.execute("SELECT count(*) FROM approvals").fetchone()[0], 0
                        )
                finally:
                    fixture.tearDown()
