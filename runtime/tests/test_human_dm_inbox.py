import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm.human_dm_inbox import XDMWatcher

NOW = "2026-09-21T14:00:00+00:00"


class XDMWatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.watcher = XDMWatcher(self.root / "x-dm.sqlite3")
        self.run_id = self.watcher.begin_run(
            account="jason-x",
            identity={"handle": "@jasonfesta", "profile_url": "https://x.com/jasonfesta"},
            checked_at=NOW,
            evidence="synthetic verified account menu",
        )
        self.facts = {
            "account": "jason-x",
            "request_id": "request-1",
            "conversation_id": "conversation-1",
            "incoming_id": "incoming-1",
            "sender_handle": "@builder",
            "sender_profile_url": "https://x.com/builder",
            "incoming_message": "could you show me how the agent handoff works?",
            "detected_at": NOW,
            "conversation_url": "https://x.com/messages/1",
            "evidence": "synthetic X request readback",
        }
        self.request_key = self.watcher.observe(self.run_id, self.facts)

    def tearDown(self):
        self.tmp.cleanup()

    def receipt(self, name, calls):
        def submit(payload, idempotency_key):
            calls.append((payload, idempotency_key))
            return {
                "status": "confirmed",
                "external_reference": name + "-receipt",
                "observed_at": NOW,
            }

        return submit

    def verify_accept_and_context(self, *, sufficient=True, sensitive=False):
        self.watcher.review_sender(
            self.run_id,
            self.request_key,
            decision="legitimate",
            evidence="public profile and message checked",
            person_id="person-1",
        )
        accept_calls = []
        self.watcher.accept(
            self.run_id,
            self.request_key,
            self.receipt("accept", accept_calls),
        )
        self.watcher.capture_context(
            self.run_id,
            self.request_key,
            {
                "incoming_message": self.facts["incoming_message"],
                "incoming_id": self.facts["incoming_id"],
                "conversation_history": ["first incoming message"],
                "crm_summary": "known AI builder; no suppression",
                "readback_complete": True,
                "context_sufficient": sufficient,
                "sensitive": sensitive,
            },
        )
        return accept_calls

    def test_nine_step_happy_path_is_replay_safe(self):
        accept_calls = self.verify_accept_and_context()
        body = "yes — happy to. which part of the handoff are you working on?"
        self.watcher.prepare_reply(
            self.run_id,
            self.request_key,
            body=body,
            review="answers the actual question without inventing context",
        )
        with self.assertRaisesRegex(ValueError, "approval"):
            self.watcher.send_reply(self.run_id, self.request_key, lambda *_: None)
        self.watcher.approve_reply(
            self.request_key, exact_body=body, approver="jason exact-message approval"
        )
        reply_calls = []
        slack_calls = []
        first = self.watcher.send_reply(
            self.run_id,
            self.request_key,
            self.receipt("reply", reply_calls),
        )
        second = self.watcher.send_reply(
            self.run_id,
            self.request_key,
            self.receipt("reply", reply_calls),
        )
        self.assertEqual(first, second)
        self.watcher.flag_slack(
            self.run_id,
            self.request_key,
            self.receipt("slack", slack_calls),
        )
        self.watcher.flag_slack(
            self.run_id,
            self.request_key,
            self.receipt("slack", slack_calls),
        )
        self.assertEqual(len(accept_calls), 1)
        self.assertEqual(len(reply_calls), 1)
        self.assertEqual(len(slack_calls), 1)
        self.assertNotIn("incoming_message", slack_calls[0][0])
        steps = {row["step_number"] for row in self.watcher.audit(self.request_key)}
        self.assertEqual(steps, set(range(2, 10)))
        self.assertEqual(self.watcher.status()["states"], {"reply_sent": 1})

    def test_spam_is_held_before_accept(self):
        self.watcher.review_sender(
            self.run_id,
            self.request_key,
            decision="spam",
            evidence="reviewed synthetic spam indicators",
        )
        calls = []
        result = self.watcher.accept(
            self.run_id,
            self.request_key,
            self.receipt("accept", calls),
        )
        self.assertEqual(result["status"], "held")
        self.assertEqual(calls, [])

    def test_insufficient_or_sensitive_context_holds_reply(self):
        self.verify_accept_and_context(sufficient=False)
        self.assertIsNone(
            self.watcher.prepare_reply(
                self.run_id,
                self.request_key,
                body="should not be used",
                review="insufficient context",
            )
        )
        with self.assertRaisesRegex(ValueError, "approval"):
            self.watcher.send_reply(self.run_id, self.request_key, lambda *_: None)

    def test_uncertain_action_is_not_retried(self):
        self.watcher.review_sender(
            self.run_id,
            self.request_key,
            decision="legitimate",
            evidence="checked",
        )
        calls = []

        def uncertain(payload, idempotency_key):
            calls.append((payload, idempotency_key))
            raise TimeoutError("synthetic provider timeout")

        first = self.watcher.accept(self.run_id, self.request_key, uncertain)
        second = self.watcher.accept(self.run_id, self.request_key, uncertain)
        self.assertEqual(first["status"], "uncertain")
        self.assertEqual(second["status"], "uncertain")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.watcher.status()["uncertain_actions"], 1)

    def test_changed_request_facts_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "changed"):
            self.watcher.observe(
                self.run_id,
                {**self.facts, "incoming_message": "different readback"},
            )

    def test_wrong_account_and_incomplete_context_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "account"):
            self.watcher.observe(
                self.run_id,
                {
                    **self.facts,
                    "account": "other-x",
                    "request_id": "request-2",
                    "incoming_id": "incoming-2",
                },
            )
        self.watcher.review_sender(
            self.run_id,
            self.request_key,
            decision="legitimate",
            evidence="checked",
        )
        self.watcher.accept(
            self.run_id,
            self.request_key,
            self.receipt("accept", []),
        )
        with self.assertRaisesRegex(ValueError, "readback"):
            self.watcher.capture_context(
                self.run_id,
                self.request_key,
                {
                    "incoming_message": self.facts["incoming_message"],
                    "incoming_id": self.facts["incoming_id"],
                    "conversation_history": [],
                    "crm_summary": "",
                    "readback_complete": False,
                    "context_sufficient": False,
                    "sensitive": False,
                },
            )

    def test_provider_time_is_separate_from_detection(self):
        from crm.cli import initialize
        from crm.relationships import Relationships

        facts = {
            **self.facts,
            "incoming_id": "incoming-timed",
            "request_id": "request-timed",
            "occurred_at": "2026-09-21T13:00:00+00:00",
        }
        key = self.watcher.observe(self.run_id, facts)
        self.watcher.review_sender(
            self.run_id, key, decision="legitimate", evidence="readback", person_id="person-1"
        )
        shared_path = self.root / "timed.sqlite3"
        with initialize(shared_path) as conn:
            conn.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES('person-1','Person','person')"
            )

        def factory():
            conn = sqlite3.connect(shared_path)
            conn.row_factory = sqlite3.Row
            return conn

        self.watcher.sync_shared_history(key, shared_factory=factory)
        self.watcher.sync_shared_history(key, shared_factory=factory)
        row = Relationships(shared_path).list("human")[0]
        self.assertEqual(len(row["contact_history"]), 1)
        event = row["last_contact"][0]
        self.assertEqual(event["surface"], "dm")
        self.assertTrue(event["occurred_at"].startswith("2026-09-21T13:00"))
        self.assertTrue(event["observed_at"].startswith("2026-09-21T14:00"))
        with self.assertRaises(ValueError):
            self.watcher.observe(self.run_id, {**facts, "occurred_at": "2026-09-21T15:00:00Z"})

    def test_shared_history_omits_private_message_body(self):
        self.verify_accept_and_context()
        shared_path = self.root / "shared.sqlite3"
        schema = Path(__file__).resolve().parents[1] / "sql/schema.sql"
        with sqlite3.connect(shared_path) as connection:
            connection.executescript(schema.read_text())
            connection.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES ('person-1','Builder','builder')"
            )

        def factory():
            connection = sqlite3.connect(shared_path)
            connection.row_factory = sqlite3.Row
            return connection

        ids = self.watcher.sync_shared_history(self.request_key, shared_factory=factory)
        self.assertEqual(len(ids), 1)
        self.assertEqual(
            ids, self.watcher.sync_shared_history(self.request_key, shared_factory=factory)
        )
        with factory() as connection:
            rows = connection.execute("SELECT * FROM outreach_events").fetchall()
            self.assertEqual(len(rows), 1)
            self.assertNotIn(self.facts["incoming_message"], json.dumps(dict(rows[0])))


if __name__ == "__main__":
    unittest.main()
