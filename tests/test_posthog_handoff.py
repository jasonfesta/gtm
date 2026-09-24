import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gtm_agent_runtime.cli import main
from gtm_agent_runtime.posthog import ProjectTokenPostHog, submit_handoff, submit_inbox


class FakePostHog:
    def __init__(self, fail=False):
        self.fail = fail
        self.events = []

    def verify_project(self):
        return None

    def capture_batch(self, events):
        if self.fail:
            raise RuntimeError("capture failed")
        self.events.extend(events)


class PostHogHandoffTests(unittest.TestCase):
    def test_project_token_captures_without_personal_api_key(self):
        with (
            patch.dict(
                "os.environ",
                {"POSTHOG_PROJECT_ID": "121185", "POSTHOG_PROJECT_TOKEN": "test-token"},
            ),
            patch("requests.post") as post,
        ):
            post.return_value.status_code = 200
            result = ProjectTokenPostHog().capture_batch([{"event": "example"}])
        self.assertEqual(result, {"http_status": 200})
        self.assertEqual(post.call_args.args[0], "https://us.i.posthog.com/batch/")
        self.assertEqual(post.call_args.kwargs["json"]["api_key"], "test-token")

    def test_manual_batch_moves_submitted_file(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder)
            event = {
                "event": "gtm.message_sent",
                "uuid": "00000000-0000-4000-8000-000000000001",
                "timestamp": "2026-09-22T12:00:00Z",
                "properties": {"distinct_id": "private-safe-key"},
            }
            source = inbox / "one.json"
            source.write_text(json.dumps({"events": [event]}))
            self.assertEqual(submit_inbox(inbox, dry_run=True)["status"], "dry_run")
            self.assertTrue(source.exists())
            client = FakePostHog()
            self.assertEqual(submit_inbox(inbox, client=client)["status"], "submitted")
            self.assertEqual(client.events, [event])
            self.assertFalse(source.exists())
            self.assertTrue((inbox / "submitted" / "one.json").exists())

    def test_failed_capture_keeps_handoff(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder)
            source = inbox / "one.json"
            source.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "event": "gtm.message_sent",
                                "uuid": "00000000-0000-4000-8000-000000000001",
                                "timestamp": "2026-09-22T12:00:00Z",
                                "properties": {"distinct_id": "private-safe-key"},
                            }
                        ]
                    }
                )
            )
            with self.assertRaises(RuntimeError):
                submit_inbox(inbox, client=FakePostHog(fail=True))
            self.assertTrue(source.exists())

    def test_single_handoff_leaves_other_files_queued(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder)
            first = inbox / "one.json"
            second = inbox / "two.json"
            event = {
                "event": "example",
                "uuid": "one",
                "timestamp": "2026-09-22T12:00:00Z",
                "properties": {"distinct_id": "safe"},
            }
            first.write_text(json.dumps({"events": [event]}))
            second.write_text(json.dumps({"events": [event]}))
            result = submit_handoff(first, client=FakePostHog())
            self.assertEqual(result["status"], "submitted")
            self.assertTrue((inbox / "submitted" / "one.json").exists())
            self.assertTrue(second.exists())

    def test_cli_returns_failed_receipt_when_credentials_are_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder)
            source = inbox / "one.json"
            source.write_text(
                json.dumps(
                    {
                        "events": [
                            {
                                "event": "example",
                                "uuid": "one",
                                "timestamp": "2026-09-22T12:00:00Z",
                                "properties": {"distinct_id": "safe"},
                            }
                        ]
                    }
                )
            )
            output = io.StringIO()
            with (
                patch(
                    "gtm_agent_runtime.posthog.submit_inbox",
                    side_effect=ValueError("PostHog credentials unavailable"),
                ),
                patch("sys.stdout", output),
            ):
                exit_code = main(
                    ["posthog", "agents/ops-agents/README.md", "--once", "--inbox", str(inbox)]
                )
            self.assertEqual(exit_code, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "failed")
            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
