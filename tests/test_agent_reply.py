import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from gtm_agent_runtime.agent_reply import (
    JsonHttp,
    idempotency_key,
    record_external,
    run_once,
    validate,
)


def item(channel="github"):
    base = {
        "channel": channel,
        "sender_account": "darwin-agent",
        "target_agent_id": "agent_1",
        "conversation_id": "conversation-1",
        "parent_url": "https://example.test/parent",
        "response_form": "query",
        "outbound_text": "Try this Darwin query.",
    }
    if channel == "github":
        base["provider"] = {"owner": "owner", "repo": "repo", "issue_number": "1"}
    elif channel == "discord":
        base["provider"] = {"guild_id": "1", "channel_id": "2", "message_id": "3"}
    else:
        base["provider"] = {"post_id": "post-1"}
    return base


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class AgentReplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name)

    def test_validation_is_small_and_explicit(self):
        self.assertEqual(validate(item())["channel"], "github")
        invalid = item()
        invalid.pop("target_agent_id")
        with self.assertRaisesRegex(ValueError, "target_agent_id"):
            validate(invalid)

    def test_key_is_stable(self):
        self.assertEqual(idempotency_key(item()), idempotency_key(dict(item())))
        revised = item()
        revised["outbound_text"] = "A different draft for the same conversation."
        self.assertEqual(idempotency_key(item()), idempotency_key(revised))

    def test_dry_run_writes_crm_handoff(self):
        result = run_once(item(), self.output, dry_run=True)
        crm = json.loads(Path(result["crm_handoff"]).read_text())
        self.assertEqual(crm["source_agent"], "agent reply agent")
        self.assertEqual(crm["operations"][0]["action"], "event_record")
        payload = crm["operations"][0]["payload"]
        self.assertEqual(payload["provider_status"], "failed")
        self.assertFalse(payload["recorded"])
        self.assertEqual(payload["error_code"], "dry_run_no_send")
        handoff = json.loads(Path(result["posthog_handoff"]).read_text())
        self.assertEqual(handoff["schema_version"], 1)
        self.assertEqual(handoff["run_id"], crm["handoff_id"])
        event = handoff["events"][0]
        self.assertEqual(event["event"], "gtm.agent_public_reply")
        self.assertFalse(event["properties"]["recorded"])
        self.assertTrue(event["properties"]["$geoip_disable"])
        self.assertFalse(event["properties"]["$process_person_profile"])
        self.assertNotIn("outbound_text", event["properties"])

    def test_existing_handoff_prevents_duplicate_send(self):
        first = run_once(item(), self.output, dry_run=True)
        second = run_once(item(), self.output, dry_run=True)
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])
        self.assertEqual(len(list((self.output / "ops-agents").glob("*.crm.json"))), 1)
        self.assertEqual(len(list((self.output / "ops-agents").glob("*.posthog.json"))), 1)

    def test_dry_run_can_be_followed_by_confirmed_connected_provider_receipt(self):
        dry_run = run_once(item(), self.output, dry_run=True)
        self.assertFalse(dry_run["recorded"])
        receipt = {
            "message_id": "123",
            "permalink": "https://github.com/owner/repo/issues/1#issuecomment-123",
        }
        confirmed = record_external(item(), self.output, receipt)
        self.assertTrue(confirmed["recorded"])
        self.assertEqual(confirmed["provider_message_id"], "123")
        replay = record_external(item(), self.output, receipt)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(list((self.output / "ops-agents").glob("*.crm.json"))), 1)

    def test_provider_readback_can_resolve_uncertain_send(self):
        def transport_error(*_a, **_k):
            raise urllib.error.URLError("connection dropped")

        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret"}):
            uncertain = run_once(item(), self.output, http=JsonHttp(transport_error))
        self.assertEqual(uncertain["provider_status"], "uncertain")
        confirmed = record_external(
            item(),
            self.output,
            {
                "message_id": "456",
                "permalink": "https://github.com/owner/repo/issues/1#issuecomment-456",
            },
        )
        self.assertEqual(confirmed["provider_status"], "confirmed")
        self.assertTrue(confirmed["recorded"])

    def test_github_send_records_receipt(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data)
            return Response({"id": 99, "html_url": "https://github.com/o/r/issues/1#x"})

        with patch.dict(os.environ, {"GITHUB_TOKEN": "secret"}):
            result = run_once(item(), self.output, http=JsonHttp(opener))
        self.assertEqual(result["provider_status"], "confirmed")
        self.assertTrue(result["recorded"])
        self.assertEqual(result["provider_message_id"], "99")
        self.assertEqual(seen["body"], {"body": "Try this Darwin query."})
        self.assertIn("/issues/1/comments", seen["url"])

    def moltbook_opener(self, comment, *, claimed=True, readback=None):
        def opener(request, timeout):
            if request.full_url.endswith("/agents/status"):
                return Response({"status": "claimed" if claimed else "pending_claim"})
            if request.get_method() == "POST":
                return Response({"comment": comment})
            return Response(
                {
                    "comments": readback
                    if readback is not None
                    else [{**comment, "content": item("moltbook")["outbound_text"]}]
                }
            )

        return opener

    def test_discord_send_records_permalink(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "secret"}):
            result = run_once(
                item("discord"),
                self.output,
                http=JsonHttp(
                    lambda *_a, **_k: Response(
                        {
                            "id": "9",
                            "channel_id": "2",
                            "content": item("discord")["outbound_text"],
                            "message_reference": {"message_id": "3"},
                        }
                    )
                ),
            )
        self.assertEqual(result["permalink"], "https://discord.com/channels/1/2/9")

    def test_moltbook_send_records_receipt(self):
        payload = {"comment": {"id": "c1", "url": "https://www.moltbook.com/comment/c1"}}
        with patch.dict(os.environ, {"MOLTBOOK_API_KEY": "secret"}):
            result = run_once(
                item("moltbook"),
                self.output,
                http=JsonHttp(self.moltbook_opener(payload["comment"])),
            )
        self.assertEqual(result["provider_message_id"], "c1")

    def test_moltbook_send_uses_private_credentials_file(self):
        credentials = self.output / "moltbook.json"
        credentials.write_text(json.dumps({"agent": {"api_key": "secret"}}))
        prepared = item("moltbook")
        prepared["provider"]["credentials_file"] = str(credentials)
        with patch.dict(os.environ, {}, clear=True):
            result = run_once(
                prepared,
                self.output,
                http=JsonHttp(self.moltbook_opener({"id": "c2"})),
            )
        self.assertEqual(result["provider_status"], "confirmed")
        self.assertEqual(result["provider_message_id"], "c2")

    def test_unclaimed_moltbook_never_posts(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.get_method())
            return Response({"status": "pending_claim"})

        with patch.dict(os.environ, {"MOLTBOOK_API_KEY": "secret"}):
            result = run_once(item("moltbook"), self.output, http=JsonHttp(opener))
        self.assertEqual(calls, ["GET"])
        self.assertEqual(result["error_code"], "moltbook_not_claimed")
        self.assertFalse(result["recorded"])

    def test_hidden_moltbook_comment_retains_receipt_without_resending(self):
        comment = {
            "id": "hidden",
            "verification_status": "pending",
            "verification": {"verification_code": "private-challenge"},
        }
        with patch.dict(os.environ, {"MOLTBOOK_API_KEY": "secret"}):
            result = run_once(
                item("moltbook"), self.output, http=JsonHttp(self.moltbook_opener(comment))
            )
            replay = run_once(
                item("moltbook"),
                self.output,
                http=JsonHttp(lambda *_a, **_k: self.fail("must not resend")),
            )
        self.assertEqual(result["provider_status"], "uncertain")
        self.assertEqual(result["provider_message_id"], "hidden")
        self.assertTrue(replay["replayed"])
        self.assertFalse(result["recorded"])
        for path in (result["crm_handoff"], result["posthog_handoff"]):
            self.assertNotIn("private-challenge", Path(path).read_text())

    def test_moltbook_nested_readback_verifies_exact_content(self):
        comment = {"id": "nested", "content": item("moltbook")["outbound_text"]}
        with patch.dict(os.environ, {"MOLTBOOK_API_KEY": "secret"}):
            result = run_once(
                item("moltbook"),
                self.output,
                http=JsonHttp(
                    self.moltbook_opener(comment, readback=[{"id": "root", "replies": [comment]}])
                ),
            )
        self.assertTrue(result["recorded"])

    def test_discord_readback_failure_is_uncertain_and_retains_id(self):
        calls = []

        def opener(request, timeout):
            calls.append(request.get_method())
            if request.get_method() == "POST":
                return Response({"id": "9"})
            raise urllib.error.HTTPError(
                request.full_url, 404, "missing", {}, io.BytesIO(b"missing")
            )

        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "secret"}):
            result = run_once(item("discord"), self.output, http=JsonHttp(opener))
        self.assertEqual(calls, ["POST", "GET"])
        self.assertEqual(result["provider_status"], "uncertain")
        self.assertEqual(result["provider_message_id"], "9")
        self.assertFalse(result["recorded"])

    def test_discord_wrong_text_or_parent_is_not_confirmed(self):
        for change in [
            {"content": "different"},
            {"message_reference": {"message_id": "wrong"}},
            {"channel_id": "wrong"},
            {"id": "wrong"},
        ]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:

                def opener(request, timeout):
                    if request.get_method() == "POST":
                        return Response({"id": "9"})
                    return Response(
                        {
                            "id": "9",
                            "channel_id": "2",
                            "content": item("discord")["outbound_text"],
                            "message_reference": {"message_id": "3"},
                            **change,
                        }
                    )

                with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "secret"}):
                    result = run_once(item("discord"), Path(tmp), http=JsonHttp(opener))
                self.assertEqual(result["provider_status"], "uncertain")
                self.assertFalse(result["recorded"])

    def test_missing_token_is_failed_not_a_crash(self):
        with patch.dict(os.environ, {}, clear=True):
            result = run_once(item(), self.output)
        self.assertEqual(result["provider_status"], "failed")
        self.assertEqual(result["error_code"], "missing_github_token")


if __name__ == "__main__":
    unittest.main()
