import base64
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

from crm.agent_dm import confirmed_handoffs, plan
from crm.agent_dm_draft import draft_candidate
from crm.agent_dm_intake import assemble
from crm.agent_dm_manual import AgentMail, ManualOutbox, _write_new, main, message_payload


def candidate():
    return {
        "target_agent_id": "agent-1",
        "agent_name": "Research Agent",
        "agent_description": "Researches available agents and their public capabilities.",
        "our_reply_text": "A Darwin query might help compare the available tools.",
        "target_is_ours": True,
        "reaction": {
            "reaction_id": "like-1",
            "reaction_type": "like",
            "channel": "github",
            "sender_account": "darwin-agent",
            "target_message_id": "reply-1",
            "target_url": "https://github.com/example/repo/issues/1#issuecomment-1",
            "occurred_at": "2026-09-20T12:00:00Z",
            "evidence": "provider readback",
        },
        "profile": {"owner_approval_required": "no"},
        "contacts": [
            {
                "contact_id": "contact-1",
                "channel": "agent_email",
                "agent_operated": True,
                "agent_operated_evidence_url": "https://example.test/agent-card",
                "agent_operated_verified_at": "2026-09-19T12:00:00Z",
                "address": "target@example.test",
                "availability": "available",
                "suppressed": False,
                "active_suppressions": [],
                "provider": "agentmail",
                "source_url": "https://example.test/agent-card",
                "last_verified_at": "2026-09-19T12:00:00Z",
            }
        ],
        "history": [],
        "history_truncated": False,
        "suppression_context_complete": True,
        "active_suppressions": [],
        "draft": {
            "subject": "Compare available agents",
            "query": "Which agents can compare research tools?",
            "body": "Could this Darwin query help your research workflow?",
            "critique": "Removed unsupported claims.",
            "reviewer": "agent dm agent",
            "model_route": "portkey",
            "revised": True,
        },
        "expose_resource": True,
    }


def prepared():
    snapshot = {
        "generated_at": "2026-09-21T12:00:00Z",
        "resource": {
            "title": "Darwin guide",
            "version": "v1",
            "sha256": hashlib.sha256(b"guide").hexdigest(),
            "url": "https://example.test/skills.md",
        },
        "candidates": [candidate()],
    }
    return plan(snapshot)["candidates"][0]


class FakeAgentMail:
    def __init__(self, *, fail_get=False):
        self.calls = []
        self.fail_get = fail_get

    def get_inbox(self, inbox_id):
        self.calls.append(("get_inbox", inbox_id))
        return {"inbox_id": inbox_id, "email": "sender@example.test"}

    def send(self, inbox_id, payload, key):
        self.calls.append(("send", inbox_id, payload, key))
        return {"message_id": "message-1", "thread_id": "thread-1"}

    def get_message(self, inbox_id, message_id):
        self.calls.append(("get_message", inbox_id, message_id))
        if self.fail_get:
            raise RuntimeError("readback unavailable")
        return {
            "inbox_id": inbox_id,
            "message_id": message_id,
            "thread_id": "thread-1",
            "timestamp": "2026-09-21T12:00:00Z",
            "from": "sender@example.test",
            "to": ["target@example.test"],
            "subject": "Compare available agents",
            "text": "Could this Darwin query help your research workflow?"
            "\n\nDarwin guide (v1): https://example.test/skills.md",
        }


class ManualAgentDMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        clock = patch("crm.agent_dm_manual.now", return_value="2026-09-21T12:00:00Z")
        self.clock = clock.start()
        self.addCleanup(clock.stop)
        self.outbox = ManualOutbox(Path(self.temp.name) / "outbox.sqlite3")

    def stage(self):
        item = prepared()
        return self.outbox.stage(item, "sender@example.test", "sender@example.test")

    def test_intake_preserves_missing_history_and_safety_context(self):
        item = candidate()
        record = {
            "agent_id": "agent-1",
            "owner_approval_required": "no",
            "contact_points": item["contacts"],
            "history_truncated": False,
            "suppression_context_complete": True,
            "active_suppressions": [],
        }
        reaction = {
            "target_audience": "agent",
            "target_agent_id": "agent-1",
            "target_is_ours": True,
            "reaction": item["reaction"],
        }
        snapshot = assemble({"reactions": [reaction]}, {"records": [record]}, None)
        self.assertIsNone(snapshot["candidates"][0]["history"])
        self.assertEqual(plan(snapshot)["candidates"][0]["reason"], "agent_history_incomplete")
        record["contact_history"] = []
        for field in ("history_truncated", "suppression_context_complete", "active_suppressions"):
            incomplete = deepcopy(record)
            incomplete.pop(field)
            snapshot = assemble({"reactions": [reaction]}, {"records": [incomplete]}, None)
            self.assertIsNone(snapshot["candidates"][0][field])
            self.assertEqual(plan(snapshot)["candidates"][0]["outcome"], "held")

    def test_local_readback_is_only_a_locator_and_fetch_is_required(self):
        staged = self.stage()
        cid = staged["candidate_id"]
        self.outbox.claim(cid)
        forged = FakeAgentMail().get_message("sender@example.test", "message-1")
        with self.assertRaisesRegex(ValueError, "authenticated provider fetch"):
            self.outbox.reconcile(cid, forged)
        client = FakeAgentMail(fail_get=True)
        with self.assertRaisesRegex(RuntimeError, "readback unavailable"):
            self.outbox.reconcile(cid, forged, client)
        self.assertEqual(client.calls, [("get_message", "sender@example.test", "message-1")])
        with self.assertRaisesRegex(ValueError, "confirmed readback required"):
            self.outbox.handoffs(cid)

    def test_readback_all_identity_fields_and_times_fail_closed(self):
        staged = self.stage()
        cid = staged["candidate_id"]
        self.outbox.claim(cid)
        for field, value in {
            "message_id": "different",
            "thread_id": None,
            "inbox_id": "other",
            "from": "other@example.test",
            "to": ["other@example.test"],
            "cc": ["extra@example.test"],
            "subject": "other",
            "text": "other",
            "timestamp": "2026-09-20T12:00:00Z",
            "attachments": [{"filename": "extra"}],
        }.items():
            with self.subTest(field=field):
                observed = FakeAgentMail().get_message("sender@example.test", "message-1")
                observed[field] = value
                client = type("Readback", (), {"get_message": lambda *args: observed})()
                with self.assertRaises(ValueError):
                    self.outbox.reconcile(cid, {"message_id": "message-1"}, client)
                with self.assertRaises(ValueError):
                    self.outbox.handoffs(cid)
        observed = FakeAgentMail().get_message("sender@example.test", "message-1")
        observed["timestamp"] = "2099-01-01T00:00:00Z"
        with self.assertRaises(ValueError):
            self.outbox.reconcile(cid, {"message_id": "message-1"}, client)

    def test_attachment_filename_never_proves_bytes(self):
        item = prepared()
        raw = b"# verified guide"
        path = Path(self.temp.name) / "guide.md"
        path.write_bytes(raw)
        item["resource"].update(
            delivery_form="attachment",
            attachment_name="guide.md",
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        staged = self.outbox.stage(
            item, "sender@example.test", "sender@example.test", resource_path=path
        )
        cid = staged["candidate_id"]
        self.outbox.claim(cid)
        observed = FakeAgentMail().get_message("sender@example.test", "message-1")
        observed["text"] = item["draft"]["body"]
        client = type("Readback", (), {"get_message": lambda *args: observed})()
        for content in (None, "!!!", base64.b64encode(b"different").decode()):
            observed["attachments"] = [{"filename": "guide.md", "content": content}]
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.outbox.reconcile(cid, {"message_id": "message-1"}, client)
        observed["attachments"][0]["content"] = base64.b64encode(raw).decode()
        handoffs = self.outbox.reconcile(cid, {"message_id": "message-1"}, client)
        operation = handoffs["crm_request"]["operations"][0]
        self.assertEqual(operation["payload"]["conversation_id"], "thread-1")
        self.assertEqual(operation["metadata"]["text"], item["draft"]["body"])
        self.assertNotIn("text", handoffs["posthog_request"]["events"][0]["properties"])

    def test_legacy_uncertain_and_crashed_sending_states_never_resend(self):
        staged = self.stage()
        self.outbox.claim(staged["candidate_id"])
        for state in ("sending", "uncertain"):
            with self.outbox.connect() as db:
                db.execute("DROP TABLE IF EXISTS agent_dm_jobs")
                db.execute("UPDATE agent_dm_jobs_v2 SET state=?", (state,))
                db.execute("CREATE TABLE agent_dm_jobs AS SELECT * FROM agent_dm_jobs_v2")
                db.execute("DELETE FROM agent_dm_jobs_v2")
            reopened = ManualOutbox(self.outbox.path)
            client = FakeAgentMail()
            with self.assertRaisesRegex(ValueError, "not ready"):
                reopened.send_one(
                    staged["candidate_id"], client, expected_sha256=staged["payload_sha256"]
                )
            self.assertEqual(reopened.get(staged["candidate_id"])["state"], state)
            self.assertEqual(client.calls, [])

    def test_partial_export_recovers_without_rewriting_first_handoff(self):
        staged = self.stage()
        cid = staged["candidate_id"]
        self.outbox.send_one(cid, FakeAgentMail(), expected_sha256=staged["payload_sha256"])
        folder = Path(self.temp.name) / "handoffs"
        args = [
            "--db",
            str(self.outbox.path),
            "export",
            "--candidate-id",
            cid,
            "--output-dir",
            str(folder),
        ]

        def partial(path, payload):
            if str(path).endswith(".posthog.json"):
                raise OSError("simulated second export failure")
            _write_new(path, payload)

        with patch("crm.agent_dm_manual._write_new", side_effect=partial):
            with self.assertRaises(OSError):
                main(args)
        crm_path = folder / (cid + ".crm.json")
        before = crm_path.stat().st_mtime_ns
        with contextlib.redirect_stdout(io.StringIO()):
            main(args)
            main(args)
        self.assertEqual(crm_path.stat().st_mtime_ns, before)
        for path in folder.glob("*.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(list(folder.glob("*.json"))), 2)

    def test_interrupted_export_never_publishes_partial_file(self):
        path = Path(self.temp.name) / "handoff.json"
        with patch("crm.agent_dm_manual.os.fsync", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                _write_new(path, {"sensitive": "body"})
        self.assertFalse(path.exists())
        _write_new(path, {"sensitive": "body"})
        with self.assertRaises(FileExistsError):
            _write_new(path, {"sensitive": "different"})

    def test_explicit_send_readback_and_separate_handoffs(self):
        staged = self.stage()
        self.assertEqual(staged["state"], "ready")
        client = FakeAgentMail()
        preview = self.outbox.preview(staged["candidate_id"])
        self.assertIn("Darwin guide (v1)", preview["body"])
        with self.assertRaisesRegex(ValueError, "reviewed payload hash required"):
            self.outbox.send_one(staged["candidate_id"], client)
        with self.assertRaisesRegex(ValueError, "hash does not match"):
            self.outbox.send_one(staged["candidate_id"], client, expected_sha256="0" * 64)
        self.assertEqual(client.calls, [])
        handoffs = self.outbox.send_one(
            staged["candidate_id"], client, expected_sha256=staged["payload_sha256"]
        )
        self.assertEqual([call[0] for call in client.calls], ["get_inbox", "send", "get_message"])
        self.assertEqual(self.outbox.get(staged["candidate_id"])["state"], "confirmed")
        self.assertEqual(handoffs["crm_request"]["destination_agent"], "ops agents")
        self.assertEqual(handoffs["posthog_request"]["destination_agent"], "ops agents")
        self.assertEqual(self.outbox.handoffs(staged["candidate_id"]), handoffs)
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.outbox.send_one(
                staged["candidate_id"], client, expected_sha256=staged["payload_sha256"]
            )

    def test_uncertain_send_is_not_retried_and_can_be_read_back(self):
        staged = self.stage()
        client = FakeAgentMail(fail_get=True)
        with self.assertRaisesRegex(RuntimeError, "readback unavailable"):
            self.outbox.send_one(
                staged["candidate_id"], client, expected_sha256=staged["payload_sha256"]
            )
        self.assertEqual(self.outbox.get(staged["candidate_id"])["state"], "uncertain")
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.outbox.send_one(
                staged["candidate_id"], client, expected_sha256=staged["payload_sha256"]
            )
        client.fail_get = False
        self.outbox.readback(staged["candidate_id"], client)
        self.assertEqual(self.outbox.get(staged["candidate_id"])["state"], "confirmed")
        self.assertEqual(len([call for call in client.calls if call[0] == "send"]), 1)

    def test_conflicting_stage_and_second_agent_message_are_rejected(self):
        item = prepared()
        first = self.outbox.stage(item, "sender@example.test", "sender@example.test")
        self.assertEqual(
            self.outbox.stage(item, "sender@example.test", "sender@example.test"), first
        )
        item["draft"]["body"] = "Different content"
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.outbox.stage(item, "sender@example.test", "sender@example.test")
        second = prepared()
        second["candidate_id"] = "agent_dm_candidate_second"
        with self.assertRaisesRegex(ValueError, "already has"):
            self.outbox.stage(second, "sender@example.test", "sender@example.test")

    def test_legacy_outbox_row_is_preserved_when_opening_v2(self):
        staged = self.stage()
        with self.outbox.connect() as db:
            db.execute("CREATE TABLE agent_dm_jobs AS SELECT * FROM agent_dm_jobs_v2")
            db.execute("DELETE FROM agent_dm_jobs_v2")
        reopened = ManualOutbox(self.outbox.path)
        self.assertEqual(reopened.get(staged["candidate_id"])["state"], "ready")

    def test_readback_identity_mismatch_does_not_confirm(self):
        staged = self.stage()
        self.outbox.claim(staged["candidate_id"])
        observed = FakeAgentMail().get_message("sender@example.test", "message-1")
        observed["to"] = ["owner@example.test"]
        with self.assertRaisesRegex(ValueError, "recipient mismatch"):
            self.outbox.reconcile(
                staged["candidate_id"],
                {"message_id": "message-1"},
                type("Readback", (), {"get_message": lambda *args: observed})(),
            )
        self.assertEqual(self.outbox.get(staged["candidate_id"])["state"], "sending")

    def test_readback_rejects_unexpected_extra_body(self):
        staged = self.stage()
        self.outbox.claim(staged["candidate_id"])
        observed = FakeAgentMail().get_message("sender@example.test", "message-1")
        observed["text"] += "\n\nUnreviewed extra content"
        with self.assertRaisesRegex(ValueError, "body mismatch"):
            self.outbox.reconcile(
                staged["candidate_id"],
                {"message_id": "message-1"},
                type("Readback", (), {"get_message": lambda *args: observed})(),
            )

    def test_markdown_link_is_in_actual_payload(self):
        payload = message_payload(prepared())
        self.assertIn("https://example.test/skills.md", payload["text"])
        self.assertEqual(payload["to"], ["target@example.test"])

    def test_real_markdown_attachment_has_verified_hash(self):
        root = Path(__file__).resolve().parents[2]
        path = root / "agents/resources/darwin-skills.v1.md"
        resource = json.loads((root / "agents/resources/darwin-skills.v1.json").read_text())
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), resource["sha256"])
        item = prepared()
        item["resource"] = {**resource, "delivery_form": "attachment"}
        payload = message_payload(item, path)
        self.assertEqual(payload["attachments"][0]["content_type"], "text/markdown")

    def test_agentmail_send_uses_stable_idempotency_header(self):
        requests = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return b'{"message_id":"message-1","thread_id":"thread-1"}'

        def opener(request: Request, timeout):
            requests.append(request)
            return Response()

        client = AgentMail(key="test-only-key", opener=opener)
        result = client.send(
            "sender@example.test",
            {"to": ["target@example.test"], "subject": "S", "text": "B"},
            "agent-dm-candidate-1",
        )
        self.assertEqual(result["message_id"], "message-1")
        self.assertEqual(requests[0].get_header("Idempotency-key"), "agent-dm-candidate-1")
        self.assertTrue(requests[0].full_url.endswith("/messages/send"))
        client.reply(
            "sender@example.test",
            "<inbound-1@example.test>",
            {"to": ["target@example.test"], "text": "Reply"},
            "agent-dm-follow-up-1",
        )
        self.assertEqual(requests[1].get_header("Idempotency-key"), "agent-dm-follow-up-1")
        self.assertIn("/messages/%3Cinbound-1%40example.test%3E/reply", requests[1].full_url)

    def test_cold_intro_is_prepared_without_fabricated_reaction(self):
        item = candidate()
        item["contacts"][0]["provider"] = "recipient_mail_host"
        item.pop("reaction")
        item.pop("our_reply_text")
        item["kind"] = "cold_intro"
        item["trigger"] = {
            "source_id": "index-agent-1",
            "source_url": "https://example.test/agent-card",
            "sender_account": "sender@example.test",
            "occurred_at": "2026-09-21T12:00:00Z",
            "evidence": "bounded Ops agent record",
        }
        outcome = plan({"generated_at": "2026-09-21T12:10:00Z", "candidates": [item]})[
            "candidates"
        ][0]
        self.assertEqual(outcome["outcome"], "prepared")
        self.assertEqual(outcome["kind"], "cold_intro")
        self.assertEqual(outcome["route"]["transport_provider"], "agentmail")
        self.assertEqual(outcome["route"]["provider"], "recipient_mail_host")
        self.assertNotIn("reaction", outcome)
        staged = self.outbox.stage(outcome, "sender@example.test", "sender@example.test")
        self.assertEqual(staged["state"], "ready")
        handoffs = confirmed_handoffs(
            outcome,
            {
                "status": "confirmed",
                "channel": "agent_email",
                "provider": "agentmail",
                "recipient": "target@example.test",
                "provider_id": "cold-message-1",
                "sent_at": "2026-09-21T12:30:00Z",
                "observed_at": "2026-09-21T12:31:00Z",
                "evidence": "AgentMail get_message:cold-message-1",
            },
        )
        self.assertEqual(
            handoffs["crm_request"]["operations"][0]["payload"]["target_agent_id"],
            "agent-1",
        )

    def test_follow_up_requires_readback_of_exact_inbound_message(self):
        first = self.stage()
        self.outbox.send_one(
            first["candidate_id"],
            FakeAgentMail(),
            expected_sha256=first["payload_sha256"],
        )
        item = deepcopy(candidate())
        item.pop("reaction")
        item.pop("our_reply_text")
        item["kind"] = "follow_up"
        item["trigger"] = {
            "source_id": "inbound-1",
            "source_url": (
                "https://api.agentmail.to/v0/inboxes/sender%40example.test/messages/inbound-1"
            ),
            "sender_account": "sender@example.test",
            "occurred_at": "2026-09-21T13:00:00Z",
            "evidence": "AgentMail get_message",
            "inbound_message_id": "inbound-1",
            "inbound_thread_id": "thread-1",
        }
        item["inbound_from"] = "target@example.test"
        item["inbound_text"] = "Yes, which research tools do you cover?"
        item["history"] = [
            {"kind": "outbound", "provider_id": "message-1", "occurred_at": "2026-09-21T12:30:00Z"},
            {"kind": "reply", "provider_id": "inbound-1", "occurred_at": "2026-09-21T13:00:00Z"},
        ]
        outcome = plan({"generated_at": "2026-09-21T13:10:00Z", "candidates": [item]})[
            "candidates"
        ][0]
        self.assertEqual(outcome["outcome"], "prepared")
        staged = self.outbox.stage(outcome, "sender@example.test", "sender@example.test")
        self.assertEqual(staged["reply_to_message_id"], "inbound-1")

        class ReplyClient(FakeAgentMail):
            def get_message(self, inbox_id, message_id):
                self.calls.append(("get_message", inbox_id, message_id))
                if message_id == "inbound-1":
                    return {
                        "inbox_id": inbox_id,
                        "message_id": "inbound-1",
                        "thread_id": "thread-1",
                        "timestamp": "2026-09-21T13:00:00Z",
                        "from": "target@example.test",
                        "to": ["sender@example.test"],
                        "text": "Yes, which research tools do you cover?",
                    }
                return {
                    "inbox_id": inbox_id,
                    "message_id": "follow-1",
                    "thread_id": "thread-1",
                    "in_reply_to": "inbound-1",
                    "timestamp": "2026-09-21T14:00:00Z",
                    "from": "sender@example.test",
                    "to": ["target@example.test"],
                    "subject": "Re: Compare available agents",
                    "text": "Could this Darwin query help your research workflow?",
                }

            def reply(self, inbox_id, message_id, payload, key):
                self.calls.append(("reply", inbox_id, message_id, payload, key))
                return {"message_id": "follow-1", "thread_id": "thread-1"}

        self.clock.return_value = "2026-09-21T14:00:00Z"
        client = ReplyClient()
        handoffs = self.outbox.send_one(
            staged["candidate_id"], client, expected_sha256=staged["payload_sha256"]
        )
        self.assertEqual(
            [call[0] for call in client.calls], ["get_inbox", "get_message", "reply", "get_message"]
        )
        self.assertEqual(
            handoffs["posthog_request"]["events"][0]["properties"]["candidate_kind"],
            "follow_up",
        )
        second = deepcopy(outcome)
        second["candidate_id"] = "agent_dm_candidate_duplicate_follow_up"
        with self.assertRaisesRegex(ValueError, "already has a staged follow-up"):
            self.outbox.stage(second, "sender@example.test", "sender@example.test")

    def test_portkey_proposal_then_critical_revision(self):
        responses = iter(
            [
                {
                    "text": json.dumps(
                        {"query": "Q1", "subject": "S1", "body": "B1", "reason": "fit"}
                    )
                },
                {
                    "text": json.dumps(
                        {
                            "critique": "Avoid assumption",
                            "query": "Q2",
                            "subject": "S2",
                            "body": "B2",
                        }
                    )
                },
            ]
        )
        prompts = []

        def complete(prompt, **kwargs):
            prompts.append(prompt)
            return next(responses)

        draft = draft_candidate(candidate(), complete_fn=complete)
        self.assertEqual(draft["body"], "B2")
        self.assertEqual(draft["model_route"], "portkey")
        self.assertEqual(len(prompts), 2)
        self.assertIn("Critique", prompts[1])

    def test_reaction_export_joins_only_agent_crm_record(self):
        item = candidate()
        reaction = {
            "target_audience": "agent",
            "target_agent_id": "agent-1",
            "target_actor_reference": "github:agent-account",
            "target_is_ours": True,
            "our_reply_text": item["our_reply_text"],
            "reaction": item["reaction"],
        }
        crm = {
            "entity_type": "agent",
            "request_id": "snapshot-1",
            "records": [
                {
                    "agent_id": "agent-1",
                    "name": item["agent_name"],
                    "description": item["agent_description"],
                    "contact_points": item["contacts"],
                    "contact_history": item["history"],
                    "history_truncated": False,
                    "suppression_context_complete": True,
                    "active_suppressions": [],
                    "owner_person_id": "owner-1",
                }
            ],
        }
        snapshot = assemble(
            {"export_id": "reactions-1", "reactions": [reaction]},
            crm,
            None,
            generated_at="2026-09-21T12:00:00Z",
        )
        self.assertEqual(snapshot["candidates"][0]["target_agent_id"], "agent-1")
        self.assertNotIn("owner_person_id", snapshot["candidates"][0])
        reaction["target_audience"] = "human"
        with self.assertRaisesRegex(ValueError, "agent-facing"):
            assemble({"reactions": [reaction]}, crm, None, generated_at="2026-09-21T12:00:00Z")

    def test_outreach_export_joins_cold_agent_and_rejects_human(self):
        item = candidate()
        crm_contact = deepcopy(item["contacts"][0])
        for field in (
            "agent_operated",
            "agent_operated_evidence_url",
            "agent_operated_verified_at",
        ):
            crm_contact.pop(field)
        crm = {
            "entity_type": "agent",
            "records": [
                {
                    "entity_id": "agent-1",
                    "name": item["agent_name"],
                    "description": item["agent_description"],
                    "owner_approval_required": "no",
                    "contact_points": [crm_contact],
                    "contact_history": [],
                    "history_truncated": False,
                    "suppression_context_complete": True,
                    "active_suppressions": [],
                }
            ],
        }
        outreach = {
            "candidates": [
                {
                    "kind": "cold_intro",
                    "target_audience": "agent",
                    "target_agent_id": "agent-1",
                    "sender_account": "sender@example.test",
                    "agent_route_attestations": [
                        {
                            "contact_id": "contact-1",
                            "address": "target@example.test",
                            "agent_operated": True,
                            "evidence_url": "https://example.test/agent-card",
                            "verified_at": "2026-09-21T12:00:00Z",
                        }
                    ],
                    "source": {
                        "source_id": "index-1",
                        "source_url": "https://example.test/agent-card",
                        "occurred_at": "2026-09-21T12:00:00Z",
                        "evidence": "verified public agent card",
                    },
                }
            ]
        }
        snapshot = assemble(
            {"reactions": []},
            crm,
            None,
            outreach=outreach,
            generated_at="2026-09-21T12:10:00Z",
        )
        self.assertEqual(plan(snapshot)["summary"]["prepared"], 0)
        self.assertEqual(
            plan(snapshot)["candidates"][0]["reason"], "reviewed_portkey_draft_missing"
        )
        outreach["candidates"][0]["agent_route_attestations"][0]["address"] = "owner@example.test"
        with self.assertRaisesRegex(ValueError, "does not match CRM contact"):
            assemble({"reactions": []}, crm, None, outreach=outreach)
        outreach["candidates"][0]["agent_route_attestations"][0]["address"] = "target@example.test"
        outreach["candidates"][0]["target_audience"] = "human"
        with self.assertRaisesRegex(ValueError, "target an agent"):
            assemble({"reactions": []}, crm, None, outreach=outreach)


if __name__ == "__main__":
    unittest.main()
