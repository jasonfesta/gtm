import sqlite3
import tempfile
import unittest
from pathlib import Path

from crm import database
from crm.cli import initialize
from crm.relationships import Relationships
from crm.route_evidence_migration import apply_sqlite

AT = "2026-09-20T12:00:00Z"


class RouteEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "test.db"
        with initialize(self.db) as c:
            c.execute(
                "INSERT INTO agents(agent_id,canonical_name,normalized_name) VALUES('a','Agent','agent')"
            )
        self.crm = Relationships(self.db)

    def save(self, **kw):
        args = dict(
            entity_type="agent",
            entity_id="a",
            channel="agent_email",
            address="a@example.test",
            availability="unknown",
            source_url="https://example.test",
            reviewer="test",
        )
        args.update(kw)
        return self.crm.save_contact(**args)

    def test_evidence_and_unknown_policy_preserved(self):
        self.save(
            agent_operated=True,
            agent_operated_evidence_url="https://example.test/proof",
            agent_operated_verified_at=AT,
        )
        self.save()
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute("SELECT agent_operated FROM relationship_contacts").fetchone()[0], 1
            )
            self.assertEqual(
                c.execute("SELECT owner_approval_required FROM relationship_profiles").fetchone()[
                    0
                ],
                "unknown",
            )
        self.save(address="unknown@example.test", provider="AgentMail")
        with sqlite3.connect(self.db) as c:
            self.assertIsNone(
                c.execute(
                    "SELECT agent_operated FROM relationship_contacts WHERE address='unknown@example.test'"
                ).fetchone()[0]
            )

    def test_strict_evidence(self):
        valid = dict(
            agent_operated=True,
            agent_operated_evidence_url="https://example.test/proof",
            agent_operated_verified_at=AT,
        )
        for change in (
            {"agent_operated": 1},
            {"agent_operated": "true"},
            {"agent_operated_evidence_url": "http://example.test"},
            {"agent_operated_verified_at": "2099-01-01T00:00:00Z"},
            {"agent_operated_verified_at": "2020-01-01"},
            {"entity_type": "human"},
            {"agent_operated": None},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.save(**(valid | change))
        self.save(**(valid | {"agent_operated": False}))

    def test_channels_and_discord_address(self):
        self.save(channel="webmcp", address="http://127.0.0.1:9000")
        self.save(channel="discord", address="https://discord.com/channels/123/456")
        for address in (
            "https://discord.com/channels/@me/456",
            "https://evil.test/channels/123/456",
            "https://discord.com/channels/123/456/789",
        ):
            with self.assertRaises(ValueError):
                self.save(channel="discord", address=address)

    def test_old_cache_replay_preserves_facts_and_triggers(self):
        legacy = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(legacy) as c:
            for name in database.SCHEMAS:
                schema = (database.ROOT / "sql" / name).read_text()
                if name == "relationships_schema.sql":
                    schema = schema.split("-- Relationship routes")[0]
                    schema = schema.replace(",'webmcp','discord'", "")
                    schema = "\n".join(
                        line for line in schema.splitlines() if "agent_operated" not in line
                    )
                c.executescript(schema)
            c.execute(
                "INSERT INTO agents(agent_id,canonical_name,normalized_name) VALUES('a','Agent','agent')"
            )
            c.execute(
                "INSERT INTO relationship_profiles(profile_id,agent_id,evidence,reviewer,updated_at) VALUES('agent:a','a','private proof','test',?)",
                (AT,),
            )
            c.execute(
                "INSERT INTO relationship_contacts(contact_id,profile_id,channel,source_url,updated_at) VALUES('r','agent:a','a2a','proof',?)",
                (AT,),
            )
            c.execute(
                "INSERT INTO relationship_events(event_id,profile_id,channel,kind,account_key,provider_id,observed_at,evidence,reviewer) VALUES('e','agent:a','a2a','reply','test','1',?,'private history','test')",
                (AT,),
            )
            c.execute(
                "INSERT INTO companies(company_id,canonical_name,normalized_name) VALUES('c','Company','company')"
            )
            c.execute(
                "INSERT INTO suppressions(suppression_id,company_id,reason,effective_at) VALUES('s','c','opt_out',?)",
                (AT,),
            )
            c.execute(
                "CREATE VIEW route_view AS SELECT contact_id,channel FROM relationship_contacts"
            )
            c.execute(
                "CREATE TRIGGER route_guard BEFORE UPDATE ON relationship_contacts WHEN NEW.address='forbidden' BEGIN SELECT RAISE(ABORT,'private guard'); END"
            )
            c.commit()
            c.execute("PRAGMA foreign_keys=ON")
            before = {
                t: c.execute("SELECT * FROM " + t).fetchall()
                for t in ("relationship_profiles", "relationship_events", "suppressions")
            }
            apply_sqlite(c)
            apply_sqlite(c)
            self.assertEqual(c.execute("SELECT * FROM route_view").fetchall(), [("r", "a2a")])
            with self.assertRaisesRegex(sqlite3.IntegrityError, "private guard"):
                c.execute("UPDATE relationship_contacts SET address='forbidden'")
            for t, rows in before.items():
                self.assertEqual(c.execute("SELECT * FROM " + t).fetchall(), rows)
            self.assertEqual(c.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertIsNone(
                c.execute("SELECT agent_operated FROM relationship_contacts").fetchone()[0]
            )
            c.execute("UPDATE relationship_contacts SET channel='webmcp' WHERE contact_id='r'")
            c.execute(
                "INSERT INTO relationship_contact_suppressions VALUES('rs','r',NULL,'opt_out',1,?,NULL)",
                (AT,),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("UPDATE relationship_events SET channel='discord'")
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_migration_rejects_active_transaction(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE agents SET canonical_name='Uncommitted' WHERE agent_id='a'")
            with self.assertRaisesRegex(ValueError, "active transaction"):
                apply_sqlite(c)
            self.assertTrue(c.in_transaction)

    def test_unrecognized_constraint_rolls_back(self):
        with sqlite3.connect(":memory:") as c:
            c.execute(
                "CREATE TABLE relationship_contacts(contact_id TEXT PRIMARY KEY, channel TEXT CHECK(length(channel)>0))"
            )
            with self.assertRaisesRegex(ValueError, "unrecognized channel"):
                apply_sqlite(c)
            self.assertNotIn(
                "agent_operated",
                [r[1] for r in c.execute("PRAGMA table_info(relationship_contacts)")],
            )

    def test_private_cache_refresh_with_current_metadata(self):
        import shutil
        from contextlib import contextmanager
        from unittest.mock import patch

        root = Path(self.temp.name) / "private"
        (root / "data").mkdir(parents=True)
        shutil.copytree(database.ROOT / "sql", root / "sql")
        with (
            sqlite3.connect(self.db) as source,
            sqlite3.connect(root / "data/private-crm-cache.sqlite3") as target,
        ):
            source.backup(target)

        class EmptyRemote:
            raw = None

            def execute(self, *args):
                return self

            def fetchall(self):
                return []

        @contextmanager
        def remote(*args):
            obj = EmptyRemote()
            obj.raw = obj
            yield obj

        with (
            patch.object(database, "ROOT", root),
            patch.object(database, "configured", return_value={"backend": "postgresql"}),
            patch.object(database, "connect", remote),
        ):
            with database.private_connection(self.db) as cache:
                self.assertIn(
                    "agent_operated",
                    [r[1] for r in cache.execute("PRAGMA table_info(relationship_contacts)")],
                )
                self.assertEqual(cache.execute("SELECT count(*) FROM agents").fetchone()[0], 1)
