import json
import unittest

from gtm_agent_runtime.agent_reply_scan import Reader, github_routes, scan_once


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class AgentReplyScanTests(unittest.TestCase):
    def test_scan_accepts_crm_snapshot_envelope(self):
        snapshot = {
            "schema_version": 1,
            "entity_type": "agent",
            "records": [
                {
                    "agent_id": "agent-1",
                    "name": "Example",
                    "website_url": "https://github.com/acme/researcher",
                }
            ],
        }
        config = {
            "github": {
                "enabled": True,
                "sender_account": "darwin-agent",
                "conversations_per_repo": 1,
            }
        }
        reader = Reader(
            lambda *_a, **_k: Response(
                [
                    {
                        "number": 4,
                        "html_url": "https://github.com/acme/researcher/issues/4",
                        "title": "Need a capability",
                    }
                ]
            )
        )
        candidates = scan_once(snapshot, config, reader)
        self.assertEqual(candidates[0]["target_agent_id"], "agent-1")

    def test_github_routes_use_existing_agent_websites(self):
        snapshot = [
            {
                "agent_id": "agent_1",
                "name": "Researcher",
                "description": "Research agent",
                "website_url": "https://github.com/acme/researcher",
                "audience": "research_agents",
            }
        ]
        self.assertEqual(
            github_routes(snapshot)[0],
            {
                "target_agent_id": "agent_1",
                "target_reference": "Researcher",
                "crm_category": "research_agents",
                "description": "Research agent",
                "owner": "acme",
                "repo": "researcher",
            },
        )

    def test_scan_reads_github_moltbook_and_discord(self):
        snapshot = [
            {
                "agent_id": "agent_1",
                "name": "Researcher",
                "website_url": "https://github.com/acme/researcher",
            }
        ]

        def opener(request, timeout):
            if "/repos/acme/researcher/issues" in request.full_url:
                return Response(
                    [
                        {
                            "number": 7,
                            "html_url": "https://github.com/acme/researcher/issues/7",
                            "title": "Need better search",
                            "body": "How can this agent find sources?",
                        }
                    ]
                )
            if "moltbook" in request.full_url:
                return Response(
                    {
                        "posts": [
                            {
                                "id": "p1",
                                "title": "Research task",
                                "content": "Looking for sources",
                                "author": {"name": "MoltAgent"},
                            }
                        ]
                    }
                )
            return Response(
                [{"id": "m1", "content": "Can an agent find this?", "author": {"id": "a1"}}]
            )

        config = {
            "github": {"enabled": True, "sender_account": "gh", "max_repos": 1},
            "moltbook": {"enabled": True, "sender_account": "molt", "limit": 1},
            "discord": {
                "enabled": True,
                "sender_account": "bot",
                "channels": [{"guild_id": "g1", "channel_id": "c1", "limit": 1}],
            },
        }
        candidates = scan_once(snapshot, config, Reader(opener))
        self.assertEqual([row["channel"] for row in candidates], ["moltbook", "github", "discord"])
        self.assertEqual(candidates[1]["target_agent_id"], "agent_1")
        self.assertIn("Need better search", candidates[1]["conversation_text"])


if __name__ == "__main__":
    unittest.main()
