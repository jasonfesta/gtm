import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.cli import initialize
from crm.outreach_queue import CRMHistory, DailyOutbound

NOW = dt.datetime.now(dt.timezone.utc).isoformat()


class DailyOutboundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "crm.sqlite3"
        with initialize(self.db) as conn:
            conn.executescript(
                (Path(__file__).parents[1] / "sql/route_review_schema.sql").read_text()
            )
            conn.execute(
                "INSERT INTO companies(company_id,canonical_name,normalized_name) VALUES ('c','test','test')"
            )
            for person in ("p", "alias"):
                conn.execute(
                    "INSERT INTO people(person_id,primary_company_id,full_name,normalized_name) VALUES (?,?,?,?)",
                    (person, "c", person, person),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO route_review_runs VALUES ('r','hash','{}',?,'test')",
                    (NOW,),
                )
                conn.execute(
                    "INSERT INTO route_reviews(run_id,person_id,qualification,role_status,outreach_score,score_status,readiness,decision_json) VALUES ('r',?,'qualified','supported',2,'supported','ready_for_preparation','{}')",
                    (person,),
                )
            for ch in ("x", "linkedin", "email"):
                conn.execute(
                    "INSERT INTO contact_points(contact_id, person_id, contact_type, value, normalized_value, verification_status, confidence, first_seen_at) VALUES (?,?,?,?,?,'confirmed',90,?)",
                    (ch, "p", ch, "https://example.test/" + ch, "https://example.test/" + ch, NOW),
                )
        self.history = CRMHistory(self.db, synthetic=True)
        self.path = Path(self.temp.name) / "daily.sqlite3"
        self.workflow = DailyOutbound(self.history, self.path)
        self.manifest = dict(
            batch_id="b",
            owner="sender",
            day=NOW[:10],
            source_refs=["fetch-receipt"],
            person_ids=["p"],
            short_batch_reason="synthetic fixture",
        )
        self.research = dict(
            owner="sender",
            researched_at=NOW,
            identity_confirmed=True,
            developer_fit=True,
            identity_evidence=["identity"],
            relevance_evidence=["relevance"],
            history_receipt="history",
            route_reason="active posts",
            route="social_email",
            channels={
                ch: dict(
                    activity_at=NOW,
                    activity_url="https://example.test/activity",
                    available=True,
                    exact_target="https://example.test/" + ch,
                    evidence_refs=["e"],
                    format="email" if ch == "email" else "public_reply",
                    score=3 - i,
                    reason="evidence",
                    post_url="https://example.test/post",
                    post_text="actual post",
                )
                for i, ch in enumerate(("x", "linkedin", "email"))
            },
        )
        self.capacities = {"x:public_reply": 25, "linkedin:public_reply": 25, "email:email": 25}

    def tearDown(self):
        self.temp.cleanup()

    def prepared(self):
        self.workflow.freeze(self.manifest)
        self.workflow.review("b", "p", self.research)
        for ch in ("linkedin", "x", "email"):
            self.workflow.save_draft(
                "b",
                "p",
                ch,
                dict(
                    body="contextual synthetic copy",
                    variant="a",
                    evidence_refs=["e"],
                    critique="checked",
                    reviewer="test",
                    reviewed_at=NOW,
                ),
            )

    def plan(self):
        return self.workflow.plan("b", now=NOW, capacities=self.capacities)

    def receipt(self, **changes):
        result = dict(
            event_id="event",
            person_id="p",
            external_reference="provider-message",
            account="sender",
            strategy="timeline_engagement",
            evidence_ref="private/receipt",
            channel="x",
            direction="inbound",
            outcome="replied",
            occurred_at=NOW,
        )
        result.update(changes)
        return result

    def test_freeze_restart_conflict_and_company_dedup(self):
        first = self.workflow.freeze(self.manifest)
        self.assertEqual(first, DailyOutbound(self.history, self.path).freeze(self.manifest))
        changed = dict(self.manifest, source_refs=["changed"])
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.workflow.freeze(changed)
        other = dict(self.manifest, batch_id="other", day="2026-10-01", person_ids=["alias"])
        self.assertTrue(self.workflow.freeze(other)["people"][0]["hold"])

    def test_no_legacy_fallback_and_bad_batch(self):
        with self.assertRaises(RuntimeError):
            CRMHistory(self.db)
        with self.assertRaises(ValueError):
            self.workflow.freeze(dict(self.manifest, person_ids=["p", "p"]))
        with self.assertRaises(ValueError):
            self.workflow.freeze(
                {k: v for k, v in self.manifest.items() if k != "short_batch_reason"}
            )

    def test_durable_plan_only_one_touch_and_capacity(self):
        self.prepared()
        result = self.plan()
        self.assertEqual(sum(s["state"] == "due_review" for s in result["steps"]), 1)
        self.assertEqual([s["planned_day_offset"] for s in result["steps"]], [0, 3, 6])
        self.assertEqual([s["channel"] for s in result["steps"]], ["email", "x", "linkedin"])
        self.assertEqual(
            next(s["channel"] for s in result["steps"] if s["state"] == "due_review"), "email"
        )
        self.assertIsNone(result["schedule_time"])
        self.assertFalse(result["send_authorized"])
        with self.workflow.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM plans").fetchone()[0], 1)
        self.capacities = {}
        self.assertTrue(all("account_capacity_hold" in s["holds"] for s in self.plan()["steps"]))

    def test_engagement_shared_receipt_replay_and_restart(self):
        self.prepared()
        self.workflow.observe(self.receipt())
        self.workflow.observe(self.receipt())
        self.workflow = DailyOutbound(self.history, self.path)
        self.assertTrue(
            all("conversation_needs_review" in s["holds"] for s in self.plan()["steps"])
        )
        with self.assertRaises(ValueError):
            self.workflow.observe(self.receipt(account="another"))

    def test_uncertain_commit_reconciles_without_duplicate(self):
        self.prepared()
        original = self.history.record

        def uncertain(payload):
            original(payload)
            raise TimeoutError("after commit")

        with patch.object(self.history, "record", side_effect=uncertain):
            with self.assertRaises(TimeoutError):
                self.workflow.observe(self.receipt())
        self.assertTrue(
            all(
                "shared_history_reconciliation_required" in s["holds"] for s in self.plan()["steps"]
            )
        )
        self.assertEqual(self.workflow.reconcile(), 1)
        self.assertEqual(self.workflow.report("b")["pending_history"], 0)

    def test_refresh_and_sender_holds_and_revision(self):
        self.prepared()
        newer = copy.deepcopy(self.research)
        newer["owner"] = "another"
        self.assertEqual(self.workflow.review("b", "p", newer), 2)
        self.assertTrue(
            all(
                "sender_ownership_hold" in s["holds"] and "draft_context_changed" in s["holds"]
                for s in self.plan()["steps"]
            )
        )
        with self.workflow.connection() as conn:
            with self.assertRaises(Exception):
                conn.execute("DELETE FROM drafts")

    def test_suppression_and_cadence_apply_across_strategies(self):
        self.prepared()
        self.workflow.observe(self.receipt(direction="outbound", outcome="sent"))
        self.assertTrue(all("cross_channel_cooldown" in s["holds"] for s in self.plan()["steps"]))
        self.workflow.observe(self.receipt(event_id="stop", outcome="opted_out"))
        self.assertTrue(all("recipient_stop" in s["holds"] for s in self.plan()["steps"]))

    def test_actual_post_and_all_channel_research_required(self):
        self.workflow.freeze(self.manifest)
        self.research["channels"]["x"]["post_text"] = ""
        with self.assertRaisesRegex(ValueError, "actual post"):
            self.workflow.review("b", "p", self.research)

    def test_reply_signal_bridge_acknowledges_after_commit(self):
        from unittest.mock import Mock

        self.prepared()
        watcher = Mock()
        signal = dict(
            event_id="incoming",
            signal_id="signal",
            classification="human",
            person_id="p",
            channel="gmail",
            account="sender",
            provider_id="message",
            occurred_at=NOW,
            evidence="private/source",
        )
        watcher.pending.return_value = [signal]
        with patch.object(self.history, "record", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                self.workflow.consume_incoming(watcher)
        watcher.acknowledge.assert_not_called()
        self.assertEqual(self.workflow.consume_incoming(watcher)["confirmed"], 1)
        watcher.acknowledge.assert_called_once()
        self.assertTrue(
            all("conversation_needs_review" in s["holds"] for s in self.plan()["steps"])
        )
        watcher.pending.return_value = [dict(signal, classification="unknown")]
        self.assertEqual(self.workflow.consume_incoming(watcher)["held"], 1)

    def test_backup_restore_retains_frozen_state_and_drafts(self):
        import sqlite3

        self.prepared()
        original = self.plan()
        restored = Path(self.temp.name) / "restored.sqlite3"
        with self.workflow.connection() as source, sqlite3.connect(restored) as target:
            source.backup(target)
            self.assertEqual(target.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        resumed = DailyOutbound(self.history, restored)
        self.assertEqual(resumed.freeze(self.manifest), self.workflow.report("b"))
        self.assertEqual(resumed.plan("b", now=NOW, capacities=self.capacities), original)
