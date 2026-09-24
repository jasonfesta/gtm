import tempfile
import unittest
from pathlib import Path

from crm.human_dm_agent import HumanDMLedger, inbound_handoff, inbound_record, run_batch


def candidate(number, kind):
    return {
        "candidate_id": f"candidate-{number}",
        "idempotency_key": f"human-dm-{number}",
        "target_human_id": f"human-{number}",
        "candidate_kind": kind,
        "channel": "x",
        "recipient": f"builder{number}",
        "sender_account": "jasonfesta",
        "context": f"context {number}",
        "crm_category": "solo_developer",
    }


class HumanDMAgentTests(unittest.TestCase):
    def test_completed_run_and_cursor_are_committed_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = HumanDMLedger(Path(directory) / "human-dm.sqlite3")
            handoff = run_batch(
                {"run_id": "run-ledger", "items": [candidate(1, "warm")]},
                draft=lambda _: "hello",
                send=lambda *_: {
                    "provider_status": "confirmed",
                    "provider_message_id": "message-1",
                },
                completed_at="2026-09-22T15:01:00Z",
            )
            ledger.commit("jasonfesta:x-dm", {"after": "message-1"}, handoff)
            self.assertEqual(ledger.cursor("jasonfesta:x-dm"), {"after": "message-1"})
            self.assertEqual(ledger.completed("run-ledger"), handoff)
            ledger.commit("jasonfesta:x-dm", {"after": "message-1"}, handoff)
            changed = dict(handoff, completed_at="2026-09-22T15:02:00Z")
            with self.assertRaisesRegex(ValueError, "cannot be changed"):
                ledger.commit("jasonfesta:x-dm", {"after": "message-2"}, changed)
            self.assertEqual(ledger.cursor("jasonfesta:x-dm"), {"after": "message-1"})

    def test_warm_candidates_are_sent_first_and_handoff_is_normalized(self):
        sent = []

        def draft(item):
            return "message " + item["candidate_id"]

        def send(item, body):
            sent.append((item["candidate_id"], body))
            return {
                "provider_status": "confirmed",
                "provider_message_id": "message-" + item["candidate_id"],
                "conversation_id": "conversation-1",
                "permalink": "https://x.com/messages/conversation-1",
                "attempted_at": "2026-09-22T15:00:00Z",
            }

        result = run_batch(
            {
                "run_id": "run-1",
                "items": [candidate(1, "cold"), candidate(2, "warm"), candidate(3, "cold")],
            },
            draft=draft,
            send=send,
            completed_at="2026-09-22T15:01:00Z",
        )

        self.assertEqual([row[0] for row in sent], ["candidate-2", "candidate-1", "candidate-3"])
        records = [operation["payload"] for operation in result["operations"]]
        self.assertTrue(
            all(operation["action"] == "event_record" for operation in result["operations"])
        )
        self.assertEqual(records[0]["candidate_kind"], "warm")
        self.assertEqual(records[0]["provider_status"], "confirmed")
        self.assertEqual(records[0]["target_human_id"], "human-2")
        self.assertEqual(
            result["posthog_notice"],
            {
                "source_agent": "human_dm_agent",
                "run_id": "run-1",
                "crm_handoff_id": result["handoff_id"],
                "status": "crm_handoff_ready",
            },
        )

    def test_uncertain_send_is_recorded_and_batch_continues(self):
        calls = []

        def send(item, _body):
            calls.append(item["candidate_id"])
            if item["candidate_id"] == "candidate-1":
                raise TimeoutError("provider timeout")
            return {"provider_status": "confirmed", "provider_message_id": "message-2"}

        result = run_batch(
            {"run_id": "run-2", "items": [candidate(1, "warm"), candidate(2, "cold")]},
            draft=lambda item: "message for " + item["candidate_id"],
            send=send,
        )

        self.assertEqual(calls, ["candidate-1", "candidate-2"])
        self.assertEqual(
            [row["payload"]["provider_status"] for row in result["operations"]],
            ["uncertain", "confirmed"],
        )
        self.assertEqual(result["operations"][0]["payload"]["error_code"], "TimeoutError")

    def test_missing_provider_receipt_is_not_treated_as_sent(self):
        result = run_batch(
            {"run_id": "run-no-receipt", "items": [candidate(1, "warm")]},
            draft=lambda _: "hello",
            send=lambda *_: {},
        )
        self.assertEqual(result["operations"][0]["payload"]["provider_status"], "uncertain")

    def test_inbound_record_links_to_the_confirmed_outbound(self):
        outbound = run_batch(
            {"run_id": "run-3", "items": [candidate(3, "warm")]},
            draft=lambda _: "hello",
            send=lambda *_: {
                "provider_status": "confirmed",
                "provider_message_id": "outbound-3",
                "conversation_id": "conversation-3",
            },
        )["operations"][0]["payload"]
        inbound = inbound_record(
            outbound,
            {
                "inbound_message_id": "inbound-3",
                "inbound_text": "thanks",
                "observed_at": "2026-09-22T15:02:00Z",
            },
        )
        self.assertEqual(inbound["in_reply_to_provider_message_id"], "outbound-3")
        self.assertEqual(inbound["conversation_id"], "conversation-3")

    def test_new_request_is_handed_to_crm_without_prior_outbound(self):
        message = {
            "sender_account": "jasonfesta",
            "sender_handle": "nereasolenne",
            "conversation_id": "18643124-3377958053",
            "conversation_url": "https://x.com/i/chat/requests/18643124-3377958053",
            "inbound_message_id": "message-123",
            "inbound_text": "Would you like to collaborate together?",
            "observed_at": "2026-09-22T14:49:00-04:00",
            "occurred_at": "2026-09-22T10:29:00-04:00",
        }
        handoff = inbound_handoff("manual-2026-09-22", [message])
        record = handoff["operations"][0]["payload"]
        self.assertEqual(handoff["source_agent"], "human_dm_agent")
        self.assertEqual(record["inbound_text"], message["inbound_text"])
        self.assertEqual(record["sender_handle"], "nereasolenne")
        self.assertEqual(record["in_reply_to_provider_message_id"], "")
        self.assertEqual(record["occurred_at"], message["occurred_at"])


if __name__ == "__main__":
    unittest.main()
