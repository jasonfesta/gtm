import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gtm_agent_runtime.crm import apply_handoff, export_snapshot, initialize


class CrmHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "crm.sqlite3"
        initialize(self.db).close()

    def tearDown(self):
        self.temp.cleanup()

    def handoff(self):
        return {
            "handoff_id": "human-discovery-2026-09-22-1",
            "source_agent": "human discovery agent",
            "operations": [
                {
                    "action": "company_upsert",
                    "payload": {"company_id": "c1", "canonical_name": "Example"},
                },
                {
                    "action": "human_upsert",
                    "payload": {
                        "person_id": "p1",
                        "full_name": "Example Person",
                        "primary_company_id": "c1",
                    },
                },
                {
                    "action": "relationship_classify",
                    "payload": {
                        "entity_type": "human",
                        "entity_id": "p1",
                        "audience": "assistant_developers",
                        "evidence": "https://example.test/profile",
                        "reviewer": "human discovery agent",
                    },
                },
                {
                    "action": "contact_upsert",
                    "payload": {
                        "entity_type": "human",
                        "entity_id": "p1",
                        "channel": "x",
                        "address": "https://x.com/example",
                        "availability": "available",
                        "source_url": "https://x.com/example",
                        "reviewer": "human discovery agent",
                        "last_verified_at": "2026-09-22T12:00:00Z",
                    },
                },
            ],
        }

    def agent_route_handoff(self, **facts):
        return {
            "handoff_id": "route-test",
            "source_agent": "agent discovery agent",
            "operations": [
                {
                    "action": "agent_upsert",
                    "payload": {"agent_id": "a1", "canonical_name": "Test Agent"},
                },
                {
                    "action": "contact_upsert",
                    "payload": {
                        "agent_id": "a1",
                        "channel": "a2a",
                        "address": "https://example.test/a2a",
                        "source_url": "https://example.test/docs",
                        "verification_status": "published",
                        "verified_at": "2026-09-22T12:00:00Z",
                        **facts,
                    },
                },
            ],
        }

    def test_route_evidence_roundtrip_and_unknown_preserved(self):
        handoff = self.agent_route_handoff()
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        record = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        self.assertIsNone(record["contact_points"][0]["agent_operated"])
        self.assertEqual(record["owner_approval_required"], "unknown")
        handoff = self.agent_route_handoff(
            agent_operated=True, agent_operated_source_url="https://example.test/proof"
        )
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        apply_handoff(self.agent_route_handoff(), database=self.db)
        route = export_snapshot(database=self.db, entity_type="agent")["records"][0][
            "contact_points"
        ][0]
        self.assertIs(route["agent_operated"], True)
        self.assertEqual(route["agent_operated_evidence_url"], "https://example.test/proof")
        self.assertTrue(route["agent_operated_verified_at"])

    def test_invalid_agent_route_evidence_rolls_back(self):
        for facts in (
            {"agent_operated": "true"},
            {"agent_operated": True},
            {"agent_operated": True, "agent_operated_source_url": "http://example.test"},
            {
                "agent_operated": False,
                "agent_operated_source_url": "https://example.test",
                "agent_operated_verified_at": "2999-01-01T00:00:00Z",
            },
        ):
            result = apply_handoff(self.agent_route_handoff(**facts), database=self.db)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(result["rolled_back"])
        self.assertEqual(export_snapshot(database=self.db, entity_type="agent")["count"], 0)

    def test_agent_route_suppression_scope_replay_release(self):
        apply_handoff(self.agent_route_handoff(), database=self.db)
        record = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        contact_id = record["contact_points"][0]["contact_id"]
        payload = {
            "contact_id": contact_id,
            "reason": "do_not_contact",
            "effective_at": "2026-09-22T12:00:00Z",
            "channel": "a2a",
        }
        handoff = {
            "handoff_id": "suppress-route",
            "source_agent": "ops agents",
            "operations": [{"action": "suppression_set", "payload": payload}],
        }
        for _ in range(2):
            self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        record = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        self.assertTrue(record["suppression_context_complete"])
        self.assertTrue(record["contact_points"][0]["suppressed"])
        self.assertEqual(len(record["active_suppressions"]), 1)
        payload["is_active"] = False
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        record = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        self.assertFalse(record["contact_points"][0]["suppressed"])
        payload["channel"] = "agent_email"
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "failed")

    def test_relationship_human_suppression_maps_to_legacy(self):
        apply_handoff(self.handoff(), database=self.db)
        contact = export_snapshot(database=self.db, entity_type="human")["records"][0][
            "contact_points"
        ][0]
        handoff = {
            "handoff_id": "legacy-map",
            "source_agent": "ops agents",
            "operations": [
                {
                    "action": "suppression_set",
                    "payload": {
                        "contact_id": contact["contact_id"],
                        "reason": "opt_out",
                        "effective_at": "2026-09-22T12:00:00Z",
                    },
                }
            ],
        }
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT contact_id FROM suppressions").fetchone()[0],
                contact["legacy_contact_id"],
            )
        self.assertTrue(
            export_snapshot(database=self.db, entity_type="human")["records"][0]["contact_points"][
                0
            ]["suppressed"]
        )

    def suppress(self, **payload):
        return apply_handoff(
            {
                "handoff_id": "scope-test",
                "source_agent": "ops agents",
                "operations": [
                    {
                        "action": "suppression_set",
                        "payload": {
                            "reason": "do_not_contact",
                            "effective_at": "2026-09-22T12:00:00Z",
                            **payload,
                        },
                    }
                ],
            },
            database=self.db,
        )

    def test_suppression_ids_cannot_cross_storage_scopes(self):
        apply_handoff(self.handoff(), database=self.db)
        apply_handoff(self.agent_route_handoff(), database=self.db)
        route = export_snapshot(database=self.db, entity_type="agent")["records"][0][
            "contact_points"
        ][0]
        self.assertEqual(
            self.suppress(person_id="p1", suppression_id="person-scope")["status"], "completed"
        )
        self.assertEqual(
            self.suppress(contact_id=route["contact_id"], suppression_id="person-scope")["status"],
            "failed",
        )
        self.assertEqual(
            self.suppress(contact_id=route["contact_id"], suppression_id="route-scope")["status"],
            "completed",
        )
        self.assertEqual(
            self.suppress(person_id="p1", suppression_id="route-scope")["status"], "failed"
        )
        self.assertEqual(
            self.suppress(company_id="c1", suppression_id="person-scope")["status"], "failed"
        )
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM suppressions").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT count(*) FROM relationship_contact_suppressions").fetchone()[
                    0
                ],
                1,
            )

    def test_missing_contact_and_legacy_channel_mismatch_fail(self):
        apply_handoff(self.handoff(), database=self.db)
        route = export_snapshot(database=self.db, entity_type="human")["records"][0][
            "contact_points"
        ][0]
        for payload in (
            {"contact_id": "missing"},
            {"contact_id": route["legacy_contact_id"], "channel": "email"},
            {"contact_id": route["contact_id"], "channel": "email"},
            {"contact_id": route["contact_id"], "person_id": "p1"},
        ):
            with self.subTest(payload=payload):
                result = self.suppress(**payload)
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["rolled_back"])
        self.assertEqual(
            self.suppress(contact_id=route["legacy_contact_id"], channel="x")["status"], "completed"
        )
        self.assertEqual(
            self.suppress(contact_id=route["contact_id"], channel="x")["status"], "completed"
        )
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM suppressions").fetchone()[0], 1)

    def test_person_and_company_channel_scope_preserves_unrelated_routes(self):
        handoff = self.handoff()
        handoff["operations"].append(
            {
                "action": "contact_upsert",
                "payload": {
                    "entity_type": "human",
                    "entity_id": "p1",
                    "channel": "email",
                    "address": "test@example.test",
                    "source_url": "https://example.test",
                    "availability": "unknown",
                    "reviewer": "ops agents",
                },
            }
        )
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        self.assertEqual(self.suppress(person_id="p1", channel="email")["status"], "completed")
        record = export_snapshot(database=self.db, entity_type="human")["records"][0]
        routes = {route["channel"]: route for route in record["contact_points"]}
        self.assertTrue(routes["email"]["suppressed"])
        self.assertFalse(routes["x"]["suppressed"])
        self.assertEqual(len(record["active_suppressions"]), 1)
        self.assertEqual(self.suppress(company_id="c1")["status"], "completed")
        record = export_snapshot(database=self.db, entity_type="human")["records"][0]
        self.assertTrue(all(route["suppressed"] for route in record["contact_points"]))
        self.assertEqual(self.suppress(company_id="c1", is_active=False)["status"], "completed")
        record = export_snapshot(database=self.db, entity_type="human")["records"][0]
        self.assertFalse(
            next(route for route in record["contact_points"] if route["channel"] == "x")[
                "suppressed"
            ]
        )

    def test_route_suppression_does_not_block_sibling_route(self):
        handoff = self.agent_route_handoff()
        other = dict(handoff["operations"][1]["payload"], address="https://other.example.test/a2a")
        handoff["operations"].append({"action": "contact_upsert", "payload": other})
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        routes = export_snapshot(database=self.db, entity_type="agent")["records"][0][
            "contact_points"
        ]
        self.assertEqual(len(routes), 2)
        self.assertEqual(self.suppress(contact_id=routes[0]["contact_id"])["status"], "completed")
        record = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        self.assertEqual(sum(route["suppressed"] for route in record["contact_points"]), 1)
        self.assertEqual(len(record["active_suppressions"]), 1)
        self.assertTrue(record["suppression_context_complete"])
        self.assertFalse(record["history_truncated"])
        self.assertTrue(all(route["agent_operated"] is None for route in record["contact_points"]))

    def test_suppression_release_and_insert_roll_back_on_later_failure(self):
        apply_handoff(self.handoff(), database=self.db)
        apply_handoff(self.agent_route_handoff(), database=self.db)
        route = export_snapshot(database=self.db, entity_type="agent")["records"][0][
            "contact_points"
        ][0]
        self.suppress(contact_id=route["contact_id"])
        payload = {"reason": "do_not_contact", "effective_at": "2026-09-22T12:00:00Z"}
        result = apply_handoff(
            {
                "handoff_id": "rollback",
                "source_agent": "ops agents",
                "operations": [
                    {
                        "action": "suppression_set",
                        "payload": dict(payload, contact_id=route["contact_id"], is_active=False),
                    },
                    {"action": "suppression_set", "payload": dict(payload, person_id="p1")},
                    {"action": "unknown", "payload": {}},
                ],
            },
            database=self.db,
        )
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["completed_operations"], 0)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT is_active FROM relationship_contact_suppressions").fetchone()[
                    0
                ],
                1,
            )
            self.assertEqual(conn.execute("SELECT count(*) FROM suppressions").fetchone()[0], 0)

    def test_null_scope_fields_replay_and_unoverlaid_legacy_route(self):
        apply_handoff(self.handoff(), database=self.db)
        contact = export_snapshot(database=self.db, entity_type="human")["records"][0][
            "contact_points"
        ][0]
        first = self.suppress(contact_id=contact["contact_id"])
        replay = self.suppress(
            contact_id=contact["legacy_contact_id"], person_id=None, company_id=None, channel=None
        )
        self.assertEqual(first["status"], "completed")
        self.assertEqual(replay["status"], "completed")
        self.assertEqual(
            first["results"][0]["result"]["suppression_id"],
            replay["results"][0]["result"]["suppression_id"],
        )
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "DELETE FROM relationship_contacts WHERE contact_id=?", (contact["contact_id"],)
            )
        legacy = export_snapshot(database=self.db, entity_type="human")["records"][0][
            "contact_points"
        ][0]
        self.assertEqual(legacy["contact_id"], contact["legacy_contact_id"])
        self.assertTrue(legacy["suppressed"])
        self.assertEqual(legacy["active_suppressions"][0]["storage"], "suppressions")

    def test_missing_suppression_storage_never_exports_clear_context(self):
        apply_handoff(self.agent_route_handoff(), database=self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE relationship_contact_suppressions")
        with self.assertRaises(sqlite3.OperationalError):
            export_snapshot(database=self.db, entity_type="agent")

    def test_contact_id_namespace_collision_does_not_leak_suppression(self):
        apply_handoff(self.handoff(), database=self.db)
        apply_handoff(self.agent_route_handoff(), database=self.db)
        human_route = export_snapshot(database=self.db, entity_type="human")["records"][0][
            "contact_points"
        ][0]
        legacy_id = human_route["legacy_contact_id"]
        self.suppress(contact_id=legacy_id, channel="x")
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE relationship_contacts SET contact_id=? WHERE channel='a2a'", (legacy_id,)
            )
        agent = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        self.assertEqual(agent["active_suppressions"], [])
        self.assertFalse(agent["contact_points"][0]["suppressed"])
        result = self.suppress(contact_id=legacy_id)
        self.assertEqual(result["status"], "failed")
        self.assertIn("ambiguous contact_id", result["error"])

    def test_controlled_protocol_events_cannot_enter_production_ledger(self):
        for placement in ("handoff", "operation", "payload", "properties"):
            with self.subTest(placement=placement):
                handoff = self.agent_route_handoff()
                event = {
                    "action": "event_record",
                    "payload": {
                        "agent_id": "a1",
                        "channel": "a2a",
                        "event_type": "protocol_test",
                        "provider_status": "confirmed",
                        "provider_message_id": "controlled-1",
                        "sender_account": "fixture",
                        "occurred_at": "2026-09-22T12:00:00Z",
                    },
                }
                handoff["operations"].append(event)
                properties = event["payload"].setdefault("properties", {})
                {
                    "handoff": handoff,
                    "operation": event,
                    "payload": event["payload"],
                    "properties": properties,
                }[placement]["controlled_test"] = True
                result = apply_handoff(handoff, database=self.db)
                self.assertEqual(result["status"], "failed")
                self.assertIn("controlled_test", result["error"])
                self.assertTrue(result["rolled_back"])
                self.assertEqual(result["completed_operations"], 0)
                with sqlite3.connect(self.db) as conn:
                    for table in ("agents", "relationship_contacts", "relationship_events"):
                        self.assertEqual(
                            conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0
                        )

    def test_complete_handoff_and_replay(self):
        first = apply_handoff(self.handoff(), database=self.db)
        second = apply_handoff(self.handoff(), database=self.db)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(first["handoff_sha256"], second["handoff_sha256"])
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM relationship_contacts").fetchone()[0],
                1,
            )

    def test_dry_run_does_not_write(self):
        result = apply_handoff(self.handoff(), database=self.db, dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM people").fetchone()[0], 0)

    def test_late_failure_rolls_back_every_operation_and_retry_succeeds(self):
        handoff = self.handoff()
        handoff["operations"].append({"action": "unknown", "payload": {}})
        result = apply_handoff(handoff, database=self.db)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_operation"], 4)
        self.assertEqual(result["completed_operations"], 0)
        self.assertTrue(result["rolled_back"])
        self.assertEqual(result["results"], [])
        with sqlite3.connect(self.db) as connection:
            for table in (
                "companies",
                "people",
                "relationship_profiles",
                "relationship_contacts",
                "contact_points",
            ):
                self.assertEqual(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 0
                )
        handoff["operations"].pop()
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")
        self.assertEqual(apply_handoff(handoff, database=self.db)["status"], "completed")

    def test_commit_error_is_uncertain_not_safe_to_replay(self):
        from contextlib import contextmanager

        @contextmanager
        def uncertain_commit(_):
            yield
            raise RuntimeError("commit outcome needs reconciliation")

        with patch("gtm_agent_runtime.crm.transaction", uncertain_commit):
            result = apply_handoff(self.handoff(), database=self.db)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["rolled_back"])
        self.assertIsNone(result["completed_operations"])

    def test_unknown_action_returns_small_failure_receipt(self):
        handoff = self.handoff()
        handoff["operations"] = [{"action": "unknown", "payload": {}}]
        result = apply_handoff(handoff, database=self.db)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_operation"], 0)
        self.assertNotIn("Traceback", json.dumps(result))

    def test_human_discovery_native_handoff(self):
        handoff = {
            "handoff": "human_discovery_to_crm",
            "run_id": "discovery-1",
            "observed_at": "2026-09-22T12:00:00Z",
            "records": [
                {
                    "identity_key": "profile:example",
                    "name": "Example Builder",
                    "profile_url": "https://x.com/example",
                    "audience": "assistant developer",
                    "darwin_relevance": "Building an assistant.",
                    "sources": [
                        {
                            "source": "x",
                            "profile_url": "https://x.com/example",
                            "source_url": "https://x.com/example/status/1",
                        }
                    ],
                }
            ],
            "cursors": {"x": "cursor-1"},
        }
        result = apply_handoff(handoff, database=self.db)
        self.assertEqual(result["status"], "completed")
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            audience = connection.execute("SELECT audience FROM relationship_profiles").fetchone()[
                0
            ]
            self.assertEqual(audience, "assistant_developers")

    def test_compact_outcome_handoff_records_only_confirmed_event(self):
        apply_handoff(
            {
                "handoff_id": "setup",
                "source_agent": "agent discovery agent",
                "operations": [
                    {
                        "action": "agent_upsert",
                        "payload": {"agent_id": "a1", "canonical_name": "Agent One"},
                    }
                ],
            },
            database=self.db,
        )
        base = {
            "idempotency_key": "reply-1",
            "channel": "moltbook",
            "sender_account": "darwin-agent",
            "target_agent_id": "a1",
            "action_type": "public_reply",
            "attempted_at": "2026-09-22T12:00:00Z",
        }
        failed = apply_handoff(
            {
                "handoff_id": "reply-failed",
                "source_agent": "agent reply agent",
                "operations": [
                    {"action": "event_record", "payload": {**base, "provider_status": "failed"}}
                ],
            },
            database=self.db,
        )
        confirmed = apply_handoff(
            {
                "handoff_id": "reply-sent",
                "source_agent": "agent reply agent",
                "operations": [
                    {
                        "action": "event_record",
                        "payload": {
                            **base,
                            "provider_status": "sent",
                            "provider_message_id": "message-1",
                            "permalink": "https://www.moltbook.com/post/1",
                        },
                    }
                ],
            },
            database=self.db,
        )
        self.assertFalse(failed["results"][0]["result"]["recorded"])
        self.assertEqual(confirmed["status"], "completed")
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM relationship_events").fetchone()[0], 1
            )

    def test_new_human_discovery_operation_shape(self):
        result = apply_handoff(
            {
                "handoff_id": "human-discovery-2",
                "source_agent": "human discovery agent",
                "operations": [
                    {
                        "action": "human_upsert",
                        "payload": {
                            "identity_key": "profile:linkedin:example",
                            "name": "LinkedIn Builder",
                            "profile_url": "https://www.linkedin.com/in/example/",
                            "audience": "solo developer",
                            "darwin_relevance": "Building independently.",
                            "sources": [
                                {
                                    "source": "linkedin",
                                    "profile_url": "https://www.linkedin.com/in/example/",
                                    "source_url": "https://www.linkedin.com/posts/example-1",
                                }
                            ],
                        },
                    }
                ],
            },
            database=self.db,
        )
        self.assertEqual(result["status"], "completed")
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT count(*) FROM relationship_contacts").fetchone()[0],
                1,
            )

    def test_agent_discovery_and_email_shapes(self):
        discovery = {
            "handoff_id": "agent-discovery-1",
            "source_agent": "agent discovery agent",
            "operations": [
                {
                    "action": "agent_upsert",
                    "payload": {
                        "agent_id": "a1",
                        "name": "Research Agent",
                        "kind": "agent",
                        "description": "Researches questions",
                        "website_url": "https://agent.example",
                    },
                },
                {
                    "action": "relationship_classify",
                    "payload": {
                        "agent_id": "a1",
                        "audience": "research_agents",
                        "capabilities": {"can_receive_requests": "yes"},
                        "source_url": "https://index.example/a1",
                    },
                },
                {
                    "action": "contact_upsert",
                    "payload": {
                        "agent_id": "a1",
                        "channel": "agent_email",
                        "address": "agent@example.com",
                        "verification_status": "published",
                        "verified_at": "2026-09-22T12:00:00Z",
                        "source_url": "https://agent.example/contact",
                    },
                },
                {
                    "action": "agent_owner_link",
                    "payload": {
                        "agent_id": "a1",
                        "name": "Agent Builder",
                        "source_url": "https://agent.example/about",
                    },
                },
            ],
        }
        self.assertEqual(apply_handoff(discovery, database=self.db)["status"], "completed")
        with sqlite3.connect(self.db) as connection:
            owner = connection.execute(
                "SELECT owner_person_id FROM relationship_profiles WHERE agent_id='a1'"
            ).fetchone()[0]
        email = {
            "handoff_id": "email-1",
            "source_agent": "email agent",
            "operations": [
                {
                    "action": "event_record",
                    "payload": {
                        "idempotency_key": "email-event-1",
                        "channel": "email",
                        "provider": "smartlead",
                        "person_id": owner,
                        "sender_account": "sender@example.com",
                        "state": "delivered",
                        "occurred_at": "2026-09-22T13:00:00Z",
                    },
                },
                {
                    "action": "event_record",
                    "payload": {
                        "idempotency_key": "email-event-2",
                        "channel": "email",
                        "provider": "smartlead",
                        "person_id": owner,
                        "sender_account": "sender@example.com",
                        "state": "opted_out",
                        "occurred_at": "2026-09-22T14:00:00Z",
                    },
                },
            ],
        }
        receipt = apply_handoff(email, database=self.db)
        self.assertEqual(receipt["status"], "completed")
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(
                connection.execute("SELECT count(*) FROM outreach_events").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM suppressions").fetchone()[0], 1
            )

    def test_snapshot_is_bounded_and_exposes_routes_and_suppressions(self):
        apply_handoff(self.handoff(), database=self.db)
        apply_handoff(
            {
                "handoff_id": "suppression-1",
                "source_agent": "email agent",
                "operations": [
                    {
                        "action": "suppression_set",
                        "payload": {
                            "person_id": "p1",
                            "channel": "email",
                            "reason": "opt_out",
                            "effective_at": "2026-09-22T12:00:00Z",
                        },
                    }
                ],
            },
            database=self.db,
        )
        snapshot = export_snapshot(database=self.db, entity_type="human", limit=1)
        self.assertEqual(snapshot["count"], 1)
        self.assertEqual(snapshot["records"][0]["entity_id"], "p1")
        self.assertEqual(snapshot["records"][0]["x_url"], "https://x.com/example")
        self.assertEqual(snapshot["records"][0]["active_suppressions"][0]["reason"], "opt_out")

    def test_agent_snapshot_includes_discovery_fields(self):
        apply_handoff(
            {
                "handoff_id": "agent-snapshot-setup",
                "source_agent": "agent discovery agent",
                "operations": [
                    {
                        "action": "agent_upsert",
                        "payload": {
                            "agent_id": "a1",
                            "canonical_name": "Agent One",
                            "description": "Research helper",
                            "website_url": "https://agent.example",
                            "status": "active",
                        },
                    }
                ],
            },
            database=self.db,
        )
        row = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        self.assertEqual(row["description"], "Research helper")
        self.assertEqual(row["website_url"], "https://agent.example")
        self.assertEqual(row["status"], "active")

    def test_company_and_legacy_contact_suppressions_reach_exact_routes(self):
        apply_handoff(self.handoff(), database=self.db)
        with sqlite3.connect(self.db) as connection:
            contact_id = connection.execute("SELECT contact_id FROM contact_points").fetchone()[0]
        receipt = apply_handoff(
            {
                "handoff_id": "scoped-suppressions",
                "source_agent": "ops agents",
                "operations": [
                    {
                        "action": "agent_upsert",
                        "payload": {
                            "agent_id": "a1",
                            "canonical_name": "Agent",
                            "company_id": "c1",
                        },
                    },
                    {
                        "action": "suppression_set",
                        "payload": {
                            "company_id": "c1",
                            "channel": "email",
                            "reason": "opt_out",
                            "effective_at": "2026-09-22T12:00:00Z",
                        },
                    },
                    {
                        "action": "suppression_set",
                        "payload": {
                            "contact_id": contact_id,
                            "channel": "x",
                            "reason": "opt_out",
                            "effective_at": "2026-09-22T12:00:00Z",
                        },
                    },
                ],
            },
            database=self.db,
        )
        self.assertEqual(receipt["status"], "completed")
        human = export_snapshot(database=self.db, entity_type="human")["records"][0]
        self.assertEqual(len(human["active_suppressions"]), 2)
        self.assertTrue(human["contact_points"][0]["suppressed"])
        self.assertEqual(len(human["contact_points"][0]["active_suppressions"]), 1)
        agent = export_snapshot(database=self.db, entity_type="agent")["records"][0]
        self.assertEqual(len(agent["active_suppressions"]), 1)
        self.assertTrue(agent["suppression_context_complete"])

    def test_reaction_event_keeps_exact_reply_and_resource_version(self):
        apply_handoff(
            {
                "handoff_id": "reaction-setup",
                "source_agent": "agent discovery agent",
                "operations": [
                    {
                        "action": "agent_upsert",
                        "payload": {"agent_id": "a1", "canonical_name": "Agent One"},
                    }
                ],
            },
            database=self.db,
        )
        receipt = apply_handoff(
            {
                "handoff_id": "reaction-1",
                "source_agent": "agent reply agent",
                "operations": [
                    {
                        "action": "event_record",
                        "payload": {
                            "reaction_id": "reaction-42",
                            "reaction_type": "like",
                            "provider_status": "observed",
                            "target_agent_id": "a1",
                            "channel": "github",
                            "sender_account": "darwin-agent",
                            "reacted_to_provider_message_id": "reply-7",
                            "reacted_to_permalink": "https://github.com/org/repo/issues/1#comment-7",
                            "permalink": "https://github.com/org/repo/issues/1#event-42",
                            "resource_version": "darwin.md@v3",
                            "occurred_at": "2026-09-22T12:00:00Z",
                            "evidence": "provider reaction receipt",
                        },
                    }
                ],
            },
            database=self.db,
        )
        self.assertEqual(receipt["status"], "completed")
        with sqlite3.connect(self.db) as connection:
            row = connection.execute(
                "SELECT kind,target_url,external_reference,evidence FROM relationship_events"
            ).fetchone()
        self.assertEqual(row[0], "like")
        self.assertEqual(row[1], "https://github.com/org/repo/issues/1#comment-7")
        self.assertEqual(row[2], "reply-7")
        self.assertIn("resource_version=darwin.md@v3", row[3])

    def test_email_sent_is_recorded_separately_from_delivered(self):
        apply_handoff(
            {
                "handoff_id": "email-person",
                "source_agent": "human discovery agent",
                "operations": [
                    {
                        "action": "human_upsert",
                        "payload": {"person_id": "p1", "full_name": "Example Person"},
                    }
                ],
            },
            database=self.db,
        )
        receipt = apply_handoff(
            {
                "handoff_id": "email-sent",
                "source_agent": "email agent",
                "operations": [
                    {
                        "action": "event_record",
                        "payload": {
                            "channel": "email",
                            "state": "sent",
                            "person_id": "p1",
                            "sender_account": "sender@example.com",
                            "provider_message_id": "smartlead-message-1",
                            "occurred_at": "2026-09-22T12:00:00Z",
                        },
                    }
                ],
            },
            database=self.db,
        )
        self.assertEqual(receipt["results"][0]["result"]["outcome"], "sent")

    def test_x_chat_receipt_appears_as_dm_history(self):
        apply_handoff(
            {
                "handoff_id": "dm-person",
                "source_agent": "human discovery agent",
                "operations": [
                    {
                        "action": "human_upsert",
                        "payload": {"person_id": "p1", "full_name": "Example Person"},
                    }
                ],
            },
            database=self.db,
        )
        receipt = apply_handoff(
            {
                "handoff_id": "dm-sent",
                "source_agent": "human dm agent",
                "operations": [
                    {
                        "action": "event_record",
                        "payload": {
                            "target_human_id": "p1",
                            "channel": "x",
                            "action_type": "dm",
                            "sender_account": "@jasonfesta",
                            "provider_status": "sent",
                            "provider_message_id": "message-1",
                            "attempted_at": "2026-09-22T14:29:00-04:00",
                            "conversation_id": "conversation-1",
                            "permalink": "https://x.com/i/chat/conversation-1",
                        },
                    }
                ],
            },
            database=self.db,
        )
        self.assertEqual(receipt["status"], "completed")
        history = export_snapshot(database=self.db, entity_type="human", entity_id="p1")["records"][
            0
        ]["contact_history"]
        self.assertEqual(history[0]["surface"], "dm")
        self.assertEqual(history[0]["kind"], "outbound")

    def test_snapshot_corrects_older_crm_x_chat_surface(self):
        old_view = [
            {
                "entity_id": "p1",
                "contact_history": [
                    {
                        "surface": "public",
                        "interaction_url": "https://x.com/i/chat/conversation-1",
                    }
                ],
            }
        ]
        with patch("gtm_agent_runtime.crm.Relationships.list", return_value=old_view):
            row = export_snapshot(database=self.db, entity_type="human")["records"][0]
        self.assertEqual(row["contact_history"][0]["surface"], "dm")


if __name__ == "__main__":
    unittest.main()
