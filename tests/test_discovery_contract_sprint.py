"""Synthetic discovery contract regressions; never live/provider outcome evidence.

Run against a producer checkout by putting that checkout first on PYTHONPATH and
running this file by absolute path from outside either repository.
"""

import json
import sqlite3
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path

from gtm_agent_runtime import agent_discovery as discovery
from gtm_agent_runtime import portkey
from gtm_agent_runtime.posthog import read_handoff

STAMP = "2026-01-01T00:00:00Z"
SOURCE = "https://example.test/agent"


def decision(**extra):
    return {"kind": "agent", "audience": "research_agents", "capabilities": {}, **extra}


def record(index="github:example/agent", **extra):
    return {
        "index_id": index,
        "name": "Example Agent",
        "source_url": SOURCE,
        "website_url": "https://github.com/example/agent",
        **extra,
    }


def contacts(route):
    changes, review = discovery.process([record()], [], lambda _: decision(routes=[route]))
    if review:
        return []
    return [
        op["payload"]
        for op in discovery.crm_operations(changes)
        if op["action"] == "contact_upsert"
    ]


def route(**extra):
    return {
        "channel": "a2a",
        "address": "https://example.test/a2a",
        "source_url": SOURCE,
        "verification_status": "published",
        "verified_at": STAMP,
        **extra,
    }


class DiscoveryContractSprintTests(unittest.TestCase):
    def test_public_projection_excludes_private_crm_and_nested_author_fields(self):
        projected = portkey.public_record(
            record(
                contacts=[{"address": "private@example.test"}],
                evidence={"private_message": "synthetic private text"},
                owner_developer_evidence={
                    "github_login": "public-builder",
                    "email": "private@example.test",
                    "notes": "private",
                },
            )
        )
        self.assertNotIn("contacts", projected)
        self.assertNotIn("evidence", projected)
        self.assertEqual(projected["owner_developer_evidence"], {"github_login": "public-builder"})

    def test_sqlite_handoff_replay_and_unknown_preserve_capability(self):
        from gtm_agent_runtime.crm import apply_handoff, initialize

        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "contract.sqlite3"
            initialize(db).close()
            for run_number, capability in enumerate([True, "unknown", "unknown"]):
                changes, _ = discovery.process(
                    [record()], [], lambda _: decision(capabilities={"can_connect_mcp": capability})
                )
                receipt = apply_handoff(
                    {
                        "handoff_id": f"synthetic-contract-{min(run_number, 1)}",
                        "source_agent": "agent discovery agent",
                        "operations": discovery.crm_operations(changes),
                    },
                    database=db,
                )
                self.assertEqual(receipt["status"], "completed", receipt)
            with sqlite3.connect(db) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM agents").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute(
                        "SELECT can_connect_mcp FROM relationship_profiles"
                    ).fetchone()[0],
                    "yes",
                )

    def test_capabilities_are_crm_tristates(self):
        for raw, expected in [
            (True, "yes"),
            (False, "no"),
            (None, "unknown"),
            ("yes", "yes"),
            ("no", "no"),
            ("maybe", "unknown"),
        ]:
            with self.subTest(raw=raw):
                result = discovery.normalize_decision(
                    decision(capabilities={"can_connect_mcp": raw})
                )
                self.assertEqual(result["capabilities"]["can_connect_mcp"], expected)

    def test_owner_approval_tristates_with_evidence(self):
        for raw, expected in [(True, "yes"), (False, "no"), ("yes", "yes"), ("no", "no")]:
            with self.subTest(raw=raw):
                result = discovery.normalize_decision(
                    decision(
                        owner_approval_required=raw,
                        owner_approval_evidence="Explicit policy",
                        owner_approval_source_url=SOURCE,
                    )
                )
                self.assertEqual(result["capabilities"]["owner_approval_required"], expected)

    def test_unsourced_owner_approval_stays_unknown(self):
        result = discovery.normalize_decision(decision(owner_approval_required=False))
        self.assertEqual(result["capabilities"]["owner_approval_required"], "unknown")

    def test_contributor_is_not_owner(self):
        candidate = record(developer_activity_evidence={"github_login": "builder"})
        changes, _ = discovery.process(
            [candidate],
            [],
            lambda _: decision(
                owner_developer={
                    "name": "Builder",
                    "relationship": "contributor",
                    "source_url": SOURCE,
                    "evidence": "Latest commit author",
                }
            ),
        )
        self.assertNotIn(
            "agent_owner_link", [op["action"] for op in discovery.crm_operations(changes)]
        )
        self.assertEqual(discovery.metrics([candidate], changes, [])["human_links"], 0)

    def test_route_without_time_is_not_available(self):
        payload = contacts(route(verified_at=None))[0]
        self.assertEqual(payload["availability"], "unknown")
        self.assertFalse(payload.get("verified_at") or payload.get("last_verified_at"))

    def test_route_future_time_is_not_verified(self):
        self.assertFalse(discovery.verified_route(route(verified_at="2999-01-01T00:00:00Z")))

    def test_provider_or_channel_does_not_imply_agent_operation(self):
        payload = contacts(
            route(channel="agent_email", address="agent@example.test", provider="AgentMail")
        )[0]
        self.assertIsNone(payload.get("agent_operated"))

    def test_canonical_agent_operation_evidence_is_preserved(self):
        payload = contacts(
            route(
                agent_operated=True,
                agent_operated_evidence_url=SOURCE,
                agent_operated_verified_at=STAMP,
            )
        )[0]
        self.assertIs(payload.get("agent_operated"), True)
        self.assertEqual(payload["agent_operated_evidence_url"], SOURCE)
        self.assertEqual(payload["agent_operated_verified_at"], STAMP)

    def test_integer_agent_operation_is_held(self):
        payloads = contacts(
            route(
                agent_operated=1,
                agent_operated_evidence="Explicit statement",
                agent_operated_source_url=SOURCE,
            )
        )
        self.assertTrue(all(p.get("agent_operated") is None for p in payloads))

    def test_invalid_agent_operation_proof_is_held(self):
        for proof, stamp in [
            ("http://example.test/proof", STAMP),
            (SOURCE, "2999-01-01T00:00:00Z"),
            (SOURCE, None),
        ]:
            with self.subTest(proof=proof, stamp=stamp):
                payloads = contacts(
                    route(
                        verified_at=stamp,
                        agent_operated=True,
                        agent_operated_evidence="Explicit statement",
                        agent_operated_source_url=proof,
                        agent_operated_verified_at=stamp,
                    )
                )
                self.assertTrue(all(p.get("agent_operated") is None for p in payloads))

    def test_same_name_different_identity_never_matches(self):
        existing = {
            "agent_id": "existing",
            "name": "Example Agent",
            "index_id": "github:other/agent",
            "website_url": "https://other.test",
        }
        self.assertIsNone(discovery.match_agent(record(), [existing]))

    def test_repository_identity_matches_across_listing_ids(self):
        existing = {
            "agent_id": "existing",
            "canonical_name": "Old Name",
            "website_url": "https://github.com/example/agent",
        }
        candidate = record(
            "hermes:listing",
            name="New Name",
            website_url="https://new.test",
            repository_url="https://github.com/example/agent",
        )
        self.assertEqual(
            (discovery.match_agent(candidate, [existing]) or {}).get("agent_id"), "existing"
        )

    def test_synthetic_400_cohort_preserves_existing_id_on_rename(self):
        cohort = [
            {
                "agent_id": f"existing-{i}",
                "canonical_name": f"Agent {i}",
                "website_url": f"https://github.com/example/repo-{i}",
            }
            for i in range(400)
        ]
        candidate = record(
            "github:example/repo-399",
            name="Renamed Agent",
            website_url="https://github.com/example/repo-399/",
        )
        changes, _ = discovery.process([candidate], cohort, lambda _: decision())
        self.assertEqual(changes[0]["agent_id"], "existing-399")
        self.assertEqual(changes[0]["action"], "update")

    def test_same_repository_in_one_batch_has_one_agent_identity(self):
        changes, _ = discovery.process(
            [record("directory:one"), record("directory:two")], [], lambda _: decision()
        )
        self.assertEqual(len({change["agent_id"] for change in changes}), 1)

    def test_posthog_envelope_and_replay_uuid(self):
        with tempfile.TemporaryDirectory() as folder:
            config = {"state_dir": folder}
            first = discovery.run(config, records=[record()], classifier=lambda _: decision())
            payload = json.loads(Path(first["posthog_handoff"]).read_text())
            events = read_handoff(Path(first["posthog_handoff"]))
            second = discovery.run(config, records=[record()], classifier=lambda _: decision())
            repeated = read_handoff(Path(second["posthog_handoff"]))
        self.assertEqual(payload.get("schema_version"), 1)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(str(uuid.UUID(event["uuid"])), event["uuid"])
        self.assertEqual(event["uuid"], repeated[0]["uuid"])
        self.assertIsNotNone(
            datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).utcoffset()
        )
        self.assertEqual(event["properties"]["run_id"], first["run_id"])
        self.assertTrue(event["properties"]["distinct_id"])
        self.assertNotIn("query_verified", event["properties"])


if __name__ == "__main__":
    unittest.main()
