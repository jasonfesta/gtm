import json
import tempfile
import unittest
from pathlib import Path

from crm.human_reply_agent import HumanReplyAgent

AT = "2026-09-22T16:00:00+00:00"


def conversation(**changes):
    row = {
        "channel": "x",
        "sender_account": "jasonfesta",
        "parent_id": "123",
        "parent_url": "https://x.com/person/status/123",
        "conversation_id": "100",
        "author_ref": "person",
        "body": "I need a better way to search across my agent tools.",
        "context": "Public thread about agent search.",
        "target_human_id": "human_1",
        "evidence_ref": "evidence/run-1.md",
    }
    row.update(changes)
    return row


class HumanReplyAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.agent = HumanReplyAgent(root / "state.sqlite3", root / "handoffs")

    @staticmethod
    def complete(_prompt):
        return {
            "text": json.dumps(
                {
                    "action": "reply_with_query",
                    "text": "try this in darwin: find an agent tool that can search this stack",
                }
            )
        }

    def test_prepare_reuses_parent_id_as_duplicate_key(self):
        first = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        second = self.agent.prepare(conversation(body="changed"), complete=self.complete)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])

    def test_confirmed_result_writes_crm_and_posthog_handoffs(self):
        prepared = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        result = self.agent.record(
            {
                "idempotency_key": prepared["idempotency_key"],
                "provider_status": "confirmed",
                "attempted_at": AT,
                "provider_message_id": "456",
                "permalink": "https://x.com/jasonfesta/status/456",
            }
        )
        operation = result["crm_handoff"]["operations"][0]
        self.assertEqual(operation["action"], "event_record")
        self.assertEqual(operation["payload"]["provider_status"], "confirmed")
        self.assertTrue(Path(result["crm_path"]).is_file())
        self.assertTrue(Path(result["posthog_path"]).is_file())
        self.assertTrue(result["crm_path"].endswith(".crm.json"))
        self.assertTrue(result["posthog_path"].endswith(".posthog.json"))
        event = result["posthog_handoff"]["events"][0]
        self.assertEqual(event["event"], "gtm.message_sent")
        self.assertNotIn("outbound_text", event["properties"])
        receipt = json.loads(Path(result["run_receipt_path"]).read_text())
        self.assertEqual(receipt["state"], "confirmed")

    def test_failed_result_writes_only_crm_handoff(self):
        prepared = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        result = self.agent.record(
            {
                "idempotency_key": prepared["idempotency_key"],
                "provider_status": "failed",
                "attempted_at": AT,
                "error_code": "composer_unavailable",
            }
        )
        self.assertIsNone(result["posthog_handoff"])
        payload = result["crm_handoff"]["operations"][0]["payload"]
        self.assertEqual(payload["error_code"], "composer_unavailable")

    def test_conflicting_provider_result_is_rejected(self):
        prepared = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        base = {
            "idempotency_key": prepared["idempotency_key"],
            "provider_status": "uncertain",
            "attempted_at": AT,
        }
        self.agent.record(base)
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.agent.record(
                {
                    **base,
                    "provider_status": "failed",
                    "error_code": "not_sent",
                }
            )

    def test_uncertain_result_can_be_confirmed_by_later_readback(self):
        prepared = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        base = {
            "idempotency_key": prepared["idempotency_key"],
            "attempted_at": AT,
        }
        uncertain = self.agent.record({**base, "provider_status": "uncertain"})
        confirmed = self.agent.record(
            {
                **base,
                "provider_status": "confirmed",
                "provider_message_id": "456",
                "permalink": "https://x.com/jasonfesta/status/456",
            }
        )
        self.assertNotEqual(
            uncertain["crm_handoff"]["handoff_id"],
            confirmed["crm_handoff"]["handoff_id"],
        )
        self.assertIsNone(uncertain["posthog_handoff"])
        self.assertEqual(confirmed["posthog_handoff"]["events"][0]["event"], "gtm.message_sent")
        self.assertEqual(
            json.loads(Path(confirmed["run_receipt_path"]).read_text())["state"],
            "confirmed",
        )

    def test_existing_timeline_post_maps_into_x_prepare(self):
        result = self.agent.prepare_x_post(
            {
                "id": "123",
                "handle": "person",
                "url": "https://x.com/person/status/123",
                "text": "I need better agent search.",
                "lane": "Agent tool",
                "context": "The parent post describes a slow executor v2.",
                "fingerprint": "abc",
                "sender_account": "jasonfesta",
                "run_id": "run-1",
            },
            complete=self.complete,
            created_at=AT,
        )
        self.assertEqual(result["conversation"]["channel"], "x")
        self.assertEqual(result["conversation"]["evidence_ref"], "x-post:abc")
        self.assertEqual(
            result["conversation"]["context"], "The parent post describes a slow executor v2."
        )

    def test_non_x_conversation_is_not_enabled_yet(self):
        with self.assertRaisesRegex(ValueError, "X only"):
            self.agent.prepare(conversation(channel="reddit"), complete=self.complete)

    def test_no_reply_stops_without_provider_result(self):
        result = self.agent.prepare(
            conversation(),
            complete=lambda _prompt: {"text": '{"action":"no_reply","text":""}'},
            created_at=AT,
        )
        self.assertEqual(result["state"], "no_reply")
        receipt = json.loads(Path(result["run_receipt_path"]).read_text())
        self.assertEqual(receipt["state"], "no_reply")
        self.assertEqual(self.agent.status(), {"states": {"no_reply": 1}, "scheduled": False})

    def test_operator_can_skip_an_unhelpful_prepared_reply(self):
        prepared = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        skipped = self.agent.skip(prepared["idempotency_key"])
        self.assertEqual(skipped["state"], "no_reply")
        self.assertEqual(
            json.loads(Path(skipped["run_receipt_path"]).read_text())["decision"],
            "no_reply",
        )
        self.assertEqual(self.agent.skip(prepared["idempotency_key"]), skipped)
        with self.assertRaisesRegex(ValueError, "no-reply"):
            self.agent.record(
                {
                    "idempotency_key": prepared["idempotency_key"],
                    "provider_status": "confirmed",
                    "attempted_at": AT,
                    "provider_message_id": "456",
                    "permalink": "https://x.com/jasonfesta/status/456",
                }
            )

    def test_operator_revision_is_the_text_in_crm_handoff(self):
        prepared = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        revised = self.agent.revise(
            prepared["idempotency_key"],
            "Darwin could help find relevant agent tools for this workflow.",
        )
        self.assertEqual(revised["state"], "prepared")
        result = self.agent.record(
            {
                "idempotency_key": prepared["idempotency_key"],
                "provider_status": "confirmed",
                "attempted_at": AT,
                "provider_message_id": "456",
                "permalink": "https://x.com/jasonfesta/status/456",
            }
        )
        self.assertEqual(
            result["crm_handoff"]["operations"][0]["payload"]["outbound_text"],
            revised["decision"]["text"],
        )

    def test_email_path_requires_configured_public_address(self):
        def email_result(_prompt):
            return {"text": '{"action":"reply_to_email","text":"email us at hello@example.com"}'}

        with self.assertRaisesRegex(ValueError, "not configured"):
            self.agent.prepare(conversation(), complete=email_result, created_at=AT)
        self.agent.public_email = "hello@example.com"
        result = self.agent.prepare(conversation(), complete=email_result, created_at=AT)
        self.assertEqual(result["decision"]["action"], "reply_to_email")

    def test_manual_inbound_response_creates_linked_distinct_handoffs(self):
        prepared = self.agent.prepare(conversation(), complete=self.complete, created_at=AT)
        key = prepared["idempotency_key"]
        observation = {
            "idempotency_key": key,
            "original_provider_message_id": "456",
            "inbound_message_id": "789",
            "permalink": "https://x.com/person/status/789",
            "text": "The server docs will be published next week.",
            "observed_at": "2026-09-22T17:00:00+00:00",
        }
        with self.assertRaisesRegex(ValueError, "confirmed outbound"):
            self.agent.record_inbound(observation)
        self.agent.record(
            {
                "idempotency_key": key,
                "provider_status": "confirmed",
                "attempted_at": AT,
                "provider_message_id": "456",
                "permalink": "https://x.com/jasonfesta/status/456",
            }
        )
        with self.assertRaisesRegex(ValueError, "must link"):
            self.agent.record_inbound({**observation, "original_provider_message_id": "wrong"})
        result = self.agent.record_inbound(observation)
        payload = result["crm_handoff"]["operations"][0]["payload"]
        self.assertEqual(payload["original_provider_message_id"], "456")
        self.assertEqual(payload["inbound_text"], observation["text"])
        self.assertEqual(result["posthog_handoff"]["events"][0]["event"], "gtm.reply_received")
        self.assertNotIn("inbound_text", json.dumps(result["posthog_handoff"]))
        self.assertTrue(Path(result["crm_path"]).is_file())
        self.assertTrue(Path(result["posthog_path"]).is_file())
        self.assertTrue(Path(result["run_receipt_path"]).is_file())
        replay = self.agent.record_inbound(observation)
        self.assertTrue(replay["duplicate"])
        self.assertEqual(result["crm_path"], replay["crm_path"])
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.agent.record_inbound({**observation, "text": "Changed"})

    def test_prompt_uses_topics_without_making_them_reply_triggers(self):
        prompts = []

        def complete(prompt):
            prompts.append(prompt)
            return {"text": '{"action":"no_reply","text":""}'}

        self.agent.prepare(conversation(), complete=complete, created_at=AT)
        self.assertIn("OpenAI Agents SDK", prompts[0])
        self.assertIn("not automatic reply triggers", prompts[0])
        self.assertNotIn("reply_to_email", prompts[0])


if __name__ == "__main__":
    unittest.main()
