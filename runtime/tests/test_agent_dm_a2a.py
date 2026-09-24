import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.agent_dm_a2a import Adapter, now, validate
from crm.agent_dm_protocol_test import Counterpart, card

BASE = "http://127.0.0.1:8766"


def policy():
    return {
        "identity": card(BASE)["name"],
        "agent_operated": True,
        "controlled_test": True,
        "owner_approval_required": "no",
        "allowed_protocols": ["a2a-0.3.0-jsonrpc"],
    }


def approval():
    return {
        "endpoint": BASE + "/a2a",
        "identity": card(BASE)["name"],
        "agent_operated": True,
        "identity_evidence": BASE + "/.well-known/agent-card.json",
        "policy_evidence": BASE + "/policy",
        "authorized": True,
        "verified_at": now(),
        "history_complete": True,
        "suppressed": False,
        "opted_out": False,
        "budget_available": True,
        "cooldown_clear": True,
        "controlled_test": True,
        "organic_outreach": False,
        "allow_no_auth": True,
        "unanswered_outbound": False,
        "skill_id": "protocol-test",
    }


class A2ATest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.peer = Counterpart(Path(self.temp.name) / "peer.sqlite3", "/unused")
        self.calls = []
        self.mutate = lambda value: value
        self.fail = False
        self.model = patch(
            "crm.agent_dm_protocol_test.complete",
            return_value={"text": '{"reply":"Synthetic test reply"}', "model": "mock"},
        )
        self.model.start()
        self.addCleanup(self.model.stop)
        self.client = Adapter(
            Path(self.temp.name) / "outbox.sqlite3",
            BASE + "/.well-known/agent-card.json",
            BASE + "/policy",
            approval(),
            transport=self.transport,
        )

    def transport(self, url, payload=None, token=None):
        if payload is None:
            return policy() if url.endswith("/policy") else card(BASE)
        self.calls.append(payload["method"])
        if payload["method"] == "message/send":
            result = self.peer.send(payload["params"]["message"], "a2a")
            if self.fail:
                raise TimeoutError("lost response")
        else:
            result = self.mutate(self.peer.get(payload["params"]["id"]))
        return {"jsonrpc": "2.0", "id": payload["id"], "result": result}

    def first(self):
        staged = self.client.stage("first", "Design a useful research query.")
        return self.client.send("first", staged["sha256"])

    def test_two_turn_task_context_and_distinct_messages(self):
        first = self.first()
        staged = self.client.stage("second", "Refine your previous answer.", parent="first")
        second = self.client.send("second", staged["sha256"])
        self.assertEqual(second["state"], "confirmed")
        self.assertEqual((first["task"], first["context"]), (second["task"], second["context"]))
        self.assertEqual(len(json.loads(second["readback"])["history"]), 4)
        self.assertEqual(self.calls, ["message/send", "tasks/get", "message/send", "tasks/get"])
        self.assertEqual(self.client.path.stat().st_mode & 0o777, 0o600)

    def test_uncertain_send_never_resends(self):
        staged = self.client.stage("first", "A question")
        self.fail = True
        with self.assertRaises(TimeoutError):
            self.client.send("first", staged["sha256"])
        with self.assertRaises(ValueError):
            self.client.send("first", staged["sha256"])
        with self.assertRaises(ValueError):
            self.client.reconcile("first")
        self.assertEqual(self.calls, ["message/send"])
        self.assertEqual(self.client.attempt("first")["state"], "uncertain")

    def test_readback_must_match_outbound(self):
        def corrupt(task):
            task["history"][0]["parts"][0]["text"] = "different"
            return task

        self.mutate = corrupt
        with self.assertRaises(ValueError):
            self.first()
        self.assertEqual(self.client.attempt("first")["state"], "uncertain")
        self.mutate = lambda value: value
        self.assertEqual(self.client.reconcile("first")["state"], "confirmed")

    def test_http_acceptance_without_agent_reply_not_confirmed(self):
        def remove_reply(task):
            task["history"] = task["history"][:1]
            return task

        self.mutate = remove_reply
        self.assertEqual(self.first()["state"], "uncertain")
        with self.assertRaises(ValueError):
            self.client.stage("second", "Hello again", parent="first")

    def test_wrong_hash_and_duplicate_first_contact(self):
        staged = self.client.stage("first", "One")
        with self.assertRaises(ValueError):
            self.client.send("first", "wrong")
        self.assertEqual(self.calls, [])
        with self.assertRaises(ValueError):
            self.client.stage("second", "Two")
        self.client.send("first", staged["sha256"])
        with self.assertRaises(ValueError):
            self.client.send("first", staged["sha256"])

    def test_fail_closed_gates(self):
        changes = {
            "history_complete": False,
            "suppressed": True,
            "opted_out": True,
            "budget_available": False,
            "cooldown_clear": False,
            "authorized": False,
            "agent_operated": False,
            "verified_at": "2020-01-01T00:00:00+00:00",
            "identity": "wrong",
            "skill_id": "wrong",
            "organic_outreach": True,
        }
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                a = approval()
                a[key] = value
                validate(card(BASE), policy(), a)

    def test_version_interface_and_auth(self):
        for change in (
            {"protocolVersion": "1.0"},
            {"preferredTransport": "HTTP+JSON"},
            {"defaultInputModes": []},
        ):
            c = card(BASE)
            c.update(change)
            with self.assertRaises(ValueError):
                validate(c, policy(), approval())
        c = card(BASE)
        c.update(
            security=[{"bearer": []}],
            securitySchemes={"bearer": {"type": "http", "scheme": "bearer"}},
        )
        with self.assertRaises(ValueError):
            validate(c, policy(), approval())
        self.assertEqual(validate(c, policy(), approval(), "secret"), BASE + "/a2a")

    def test_task_context_mismatch_stays_uncertain(self):
        def corrupt(task):
            task["contextId"] = "other"
            return task

        self.mutate = corrupt
        with self.assertRaises(ValueError):
            self.first()
        self.assertEqual(self.client.attempt("first")["state"], "uncertain")

    def test_server_replay_cannot_cross_protocol(self):
        self.first()
        message = json.loads(self.client.attempt("first")["payload"])["message"]
        with self.assertRaises(ValueError):
            self.peer.send(message, "webmcp")

    def test_ambiguous_outbound_rejected(self):
        def corrupt(task):
            task["history"].append(task["history"][0])
            return task

        self.mutate = corrupt
        with self.assertRaises(ValueError):
            self.first()
        self.assertEqual(self.client.attempt("first")["state"], "uncertain")

    def test_string_authorization_and_naive_timestamp_rejected(self):
        for change in ({"authorized": "false"}, {"verified_at": "2026-09-23T10:00:00"}):
            a = approval()
            a.update(change)
            with self.assertRaises(ValueError):
                validate(card(BASE), policy(), a)

    def test_outbound_context_mismatch_rejected(self):
        def corrupt(task):
            task["history"][0]["contextId"] = "wrong"
            return task

        self.mutate = corrupt
        with self.assertRaises(ValueError):
            self.first()

    def test_owner_approval_missing(self):
        p = policy()
        p["owner_approval_required"] = "unknown"
        with self.assertRaises(ValueError):
            validate(card(BASE), p, approval())


if __name__ == "__main__":
    unittest.main()
