import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gtm_agent_runtime.agent_reply import JsonHttp
from gtm_agent_runtime.agent_reply_compose import PortkeyComposer
from gtm_agent_runtime.agent_reply_engage import engage_once
from gtm_agent_runtime.agent_reply_scan import Reader


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class AgentReplyEngageTests(unittest.TestCase):
    def test_unrelated_conversations_produce_no_reply_or_handoffs(self):
        snapshot = [{"agent_id": "agent_1", "website_url": "https://github.com/acme/researcher"}]
        config = {"github": {"enabled": True, "sender_account": "jason-darwin"}}
        reader = Reader(
            lambda *_a, **_k: Response(
                [
                    {
                        "number": 4,
                        "html_url": "https://github.com/acme/researcher/issues/4",
                        "title": "Unrelated build failure",
                    }
                ]
            )
        )
        composer = PortkeyComposer(
            {"model": "configured-model"},
            lambda *_a, **_k: Response(
                {"choices": [{"message": {"content": '{"candidate_index": null}'}}]}
            ),
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"PORTKEY_API_KEY": "pk"}),
        ):
            result = engage_once(
                snapshot, config, {"version": "v1"}, Path(tmp), reader=reader, composer=composer
            )
            self.assertEqual(result["status"], "no_relevant_conversation")
            self.assertFalse((Path(tmp) / "ops-agents").exists())

    def test_one_manual_cycle_scans_composes_sends_and_hands_off(self):
        snapshot = [
            {
                "agent_id": "agent_1",
                "name": "Researcher",
                "website_url": "https://github.com/acme/researcher",
                "description": "Finds reliable sources",
                "audience": "research_agents",
            }
        ]
        config = {
            "github": {"enabled": True, "sender_account": "darwin", "max_repos": 1},
            "portkey": {"model": "configured-model"},
        }

        def read_opener(*_, **__):
            return Response(
                [
                    {
                        "number": 4,
                        "html_url": "https://github.com/acme/researcher/issues/4",
                        "title": "Find source-backed tools",
                        "body": "Which capability can do this?",
                    }
                ]
            )

        def compose_opener(*_, **__):
            return Response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "candidate_index": 0,
                                        "response_form": "query",
                                        "outbound_text": "Try this Darwin Search query: find a source-backed research capability.",
                                        "resource_version": None,
                                    }
                                )
                            }
                        }
                    ]
                }
            )

        def send_opener(*_, **__):
            return Response(
                {
                    "id": 44,
                    "html_url": "https://github.com/acme/researcher/issues/4#issuecomment-44",
                }
            )

        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.dict(os.environ, {"PORTKEY_API_KEY": "pk", "GITHUB_TOKEN": "gh"}),
        ):
            result = engage_once(
                snapshot,
                config,
                {"version": "v1"},
                Path(tmp),
                reader=Reader(read_opener),
                composer=PortkeyComposer(config["portkey"], compose_opener),
                sender_http=JsonHttp(send_opener),
            )
            self.assertTrue(result["recorded"])
            self.assertEqual(result["provider_message_id"], "44")
            self.assertTrue(Path(result["crm_handoff"]).is_file())
            analytics = json.loads(Path(result["posthog_handoff"]).read_text())
            self.assertEqual(analytics["events"][0]["event"], "gtm.agent_public_reply")


if __name__ == "__main__":
    unittest.main()
