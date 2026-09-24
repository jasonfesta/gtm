"""MCP identity writes against an isolated SQLite fixture only."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from crm.cli import initialize
from crm.mcp_server import Server


class AgentIdentityMCPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "crm.sqlite3"
        initialize(self.db).close()
        self.server = Server(self.db)
        self.server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.args = dict(
            provider="letta",
            provider_id="agent-57ce3ea1-72ad-43e5-a444-7e3724f706e8",
            identity_url="https://app.letta.com/agentfiles/agent-57ce3ea1-72ad-43e5-a444-7e3724f706e8",
            canonical_name="Example",
            evidence="Provider readback confirms immutable identity",
            reviewer="test",
        )

    def call(self, **changes):
        return self.server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "crm_agent_upsert", "arguments": dict(self.args, **changes)},
            }
        )

    def data(self, response):
        self.assertNotIn("error", response)
        self.assertFalse(response["result"].get("isError"), response)
        return json.loads(response["result"]["content"][0]["text"])

    def test_create_and_replay_preserve_private_and_policy_facts(self):
        first = self.data(self.call())
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE agents SET confidence=95,status='inactive',notes='keep' WHERE agent_id=?",
                (first["agent_id"],),
            )
            conn.execute(
                "UPDATE relationship_profiles SET owner_approval_required='yes',evidence='keep evidence' WHERE profile_id=?",
                (first["profile_id"],),
            )
            before = conn.execute("SELECT * FROM agents").fetchall()
            profiles = conn.execute("SELECT * FROM relationship_profiles").fetchall()
        replay = self.data(self.call(canonical_name="Different name", evidence="New observation"))
        self.assertFalse(replay["created"])
        self.assertEqual(first["agent_id"], replay["agent_id"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(before, conn.execute("SELECT * FROM agents").fetchall())
            self.assertEqual(
                profiles, conn.execute("SELECT * FROM relationship_profiles").fetchall()
            )

    def test_identity_tools_do_not_initialize_private_copy_cache(self):
        with patch("crm.mcp_server.CopyBuilder", side_effect=RuntimeError("cache missing")):
            self.setUp_server_without_copy()
            self.data(self.call())

    def setUp_server_without_copy(self):
        self.server = Server(self.db)
        self.server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def test_name_only_does_not_merge(self):
        first = self.data(self.call())
        second = self.data(
            self.call(
                provider="moltbook",
                provider_id="3e57a33e-93e3-4a95-a2f5-64dc2ef2f328",
                identity_url="https://www.moltbook.com/u/example",
            )
        )
        self.assertNotEqual(first["agent_id"], second["agent_id"])

    def test_exact_existing_and_ambiguous_matches(self):
        first = self.data(self.call())
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO agents(agent_id,canonical_name,normalized_name,website_url) VALUES('other','Example','example',?)",
                (self.args["identity_url"],),
            )
        self.assertTrue(self.call()["result"]["isError"])
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM relationship_profiles WHERE agent_id=?", (first["agent_id"],))
            conn.execute("DELETE FROM agents WHERE agent_id=?", (first["agent_id"],))
        self.assertTrue(self.call()["result"]["isError"])

    def test_required_evidence_invalid_ids_and_channel_urls_rejected(self):
        for changes in (
            {"evidence": " "},
            {"reviewer": ""},
            {"provider_id": "Example"},
            {"identity_url": "https://discord.com/channels/1/2"},
            {"provider": "unknown"},
        ):
            result = self.call(**changes)
            self.assertTrue("error" in result or result["result"].get("isError"), result)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM agents").fetchone()[0], 0)

    def test_moltbook_rename_requires_explicit_reconciliation(self):
        args = dict(
            provider="moltbook",
            provider_id="3e57a33e-93e3-4a95-a2f5-64dc2ef2f328",
            identity_url="https://www.moltbook.com/u/example",
        )
        self.data(self.call(**args))
        args["identity_url"] = "https://www.moltbook.com/u/renamed"
        self.assertTrue(self.call(**args)["result"]["isError"])

    def test_recycled_handle_with_different_provider_uuid_rejected(self):
        args = dict(
            provider="moltbook",
            provider_id="3e57a33e-93e3-4a95-a2f5-64dc2ef2f328",
            identity_url="https://www.moltbook.com/u/example",
        )
        self.data(self.call(**args))
        # Simulate remote readback without private evidence, then a fresh process.
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE relationship_profiles SET evidence='' ")
        self.setUp_server_without_copy()
        self.data(self.call(**args))
        args["provider_id"] = "4e57a33e-93e3-4a95-a2f5-64dc2ef2f328"
        self.assertTrue(self.call(**args)["result"]["isError"])

    def test_contact_source_url_collision_is_held(self):
        from crm.relationships import Relationships

        first = self.data(self.call())
        identity_url = "https://www.moltbook.com/u/another"
        Relationships(self.db).save_contact(
            entity_type="agent",
            entity_id=first["agent_id"],
            channel="moltbook",
            address="https://www.moltbook.com/u/example",
            availability="unknown",
            source_url=identity_url,
            reviewer="test",
        )
        result = self.call(
            provider="moltbook",
            provider_id="3e57a33e-93e3-4a95-a2f5-64dc2ef2f328",
            identity_url=identity_url,
        )
        self.assertTrue(result["result"]["isError"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM agents").fetchone()[0], 1)

    def eligibility(self, entity_id, contact_id):
        return self.data(
            self.server.dispatch(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "crm_route_eligibility",
                        "arguments": {
                            "entity_type": "agent",
                            "entity_id": entity_id,
                            "contact_id": contact_id,
                        },
                    },
                }
            )
        )

    def test_route_eligibility_active_suppression_and_body_free_history(self):
        from crm.relationships import Relationships

        agent = self.data(self.call())
        service = Relationships(self.db)
        contact = service.save_contact(
            entity_type="agent",
            entity_id=agent["agent_id"],
            channel="moltbook",
            address="https://www.moltbook.com/u/example",
            availability="available",
            last_verified_at="2026-01-01T00:00:00Z",
            source_url=self.args["identity_url"],
            reviewer="test",
        )
        route = contact["contact_id"]
        service.record(
            entity_type="agent",
            entity_id=agent["agent_id"],
            channel="moltbook",
            kind="outbound",
            account_key="test",
            provider_id="message1",
            occurred_at="2026-01-01T00:00:00Z",
            observed_at="2026-01-01T00:01:00Z",
            evidence="private body must not return",
            reviewer="test",
        )
        clear = self.eligibility(agent["agent_id"], route)
        self.assertTrue(clear["suppression_clear"])
        self.assertTrue(clear["crm_history_complete"])
        self.assertFalse(clear["history_complete"])
        self.assertTrue(
            clear["channel_history"]["last_outbound_at"].startswith("2026-01-01T00:00:00")
        )
        self.assertNotIn("private body", json.dumps(clear))
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO relationship_contact_suppressions(suppression_id,contact_id,reason,is_active,effective_at) VALUES('s',?,'do_not_contact',1,'2026-01-01T00:00:00Z')",
                (route,),
            )
        held = self.eligibility(agent["agent_id"], route)
        self.assertTrue(held["suppressed"])
        self.assertEqual(held["active_suppressions"][0]["scope"], "contact")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE relationship_contact_suppressions SET is_active=0")
        self.assertTrue(self.eligibility(agent["agent_id"], route)["suppression_clear"])
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "INSERT INTO companies(company_id,canonical_name,normalized_name) VALUES('company','Example','example')"
            )
            conn.execute(
                "UPDATE agents SET company_id='company' WHERE agent_id=?", (agent["agent_id"],)
            )
            conn.execute(
                "INSERT INTO suppressions(suppression_id,company_id,reason,is_active,effective_at) VALUES('company-block','company','do_not_contact',1,'2026-01-01T00:00:00Z')"
            )
        company_held = self.eligibility(agent["agent_id"], route)
        self.assertTrue(company_held["suppressed"])
        self.assertEqual(company_held["active_suppressions"][0]["scope"], "company")
        missing = self.eligibility("different-agent", route)
        self.assertFalse(missing["contact_exists"])
        self.assertFalse(missing["suppression_context_complete"])
        self.assertIsNone(missing["suppression_clear"])
