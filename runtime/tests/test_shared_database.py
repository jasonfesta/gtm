"""Offline translation checks and opt-in loopback PostgreSQL integration tests."""

import json
import os
import shutil
import sqlite3
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

import test_hydration_import as hydration

from crm import database
from crm.migrate import migrate


class TranslationTests(unittest.TestCase):
    def test_private_values_removed_before_network_binding(self):
        sql, params, *_ = database.translate(
            "INSERT OR IGNORE INTO companies(company_id,canonical_name,normalized_name,notes) VALUES(?,?,?,?)",
            ("a", "Example", "example", "PRIVATE MAILBOX BODY"),
        )
        self.assertNotIn("PRIVATE MAILBOX BODY", str(params))
        self.assertIn("ON CONFLICT DO NOTHING", sql)
        self.assertIsNone(params["p3"])

    def test_literal_question_marks_and_exact_url_are_not_rewritten(self):
        sql, params, *_ = database.translate(
            "SELECT '?' AS marker,url FROM sources WHERE url=? AND url LIKE '%Example%'",
            ("https://Example.com/Case?x=A",),
        )
        self.assertEqual(params["p0"], "https://Example.com/Case?x=A")
        self.assertIn("'?'", sql)
        self.assertIn("%%Example%%", sql)

    def test_other_tables_and_destructive_operations_fail_closed(self):
        for query in (
            "SELECT * FROM public.existing",
            "DELETE FROM people",
            "DROP TABLE people",
            "SELECT * FROM email_preparations",
        ):
            with self.assertRaises(ValueError):
                database.translate(query)


@unittest.skipUnless(
    os.environ.get("CRM_TEST_POSTGRES_URL"), "requires disposable loopback PostgreSQL"
)
class SharedDatabaseTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql

        base = os.environ["CRM_TEST_POSTGRES_URL"]
        parts = urlsplit(base)
        if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("Tests refuse non-loopback PostgreSQL")
        self.name = "crm_adapter_test_" + uuid.uuid4().hex
        self.base = urlunsplit(parts._replace(path="/postgres"))
        self.dsn = urlunsplit(parts._replace(path="/" + self.name))
        with psycopg.connect(self.base, autocommit=True) as c:
            c.execute(
                sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(
                    sql.Identifier(self.name)
                )
            )
        self.addCleanup(self.drop_database)
        self.fixture = hydration.HydrationImportTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.freeze()
        self.root = self.fixture.run
        shutil.copytree(database.ROOT / "sql", self.root / "sql")
        with sqlite3.connect(self.fixture.db) as local:
            for name in database.SCHEMAS:
                local.executescript((self.root / "sql" / name).read_text())
        with psycopg.connect(self.dsn) as c:
            c.execute("CREATE TABLE public.existing(id integer PRIMARY KEY)")
            c.execute("INSERT INTO public.existing VALUES(42)")
        self.report = migrate(self.dsn, self.fixture.db)
        (self.root / "accounts").mkdir()
        (self.root / "data").mkdir()
        config = self.root / "accounts/database.json"
        config.write_text(
            json.dumps(
                {
                    "backend": "postgresql",
                    "schema": "crm_gtm",
                    "credential_file": "accounts/test.url",
                }
            )
        )
        (self.root / "accounts/test.url").write_text(self.dsn)
        with (
            sqlite3.connect(self.fixture.db) as source,
            sqlite3.connect(self.root / "data/private-crm-cache.sqlite3") as target,
        ):
            source.backup(target)
        for key, value in (("ROOT", self.root), ("CANONICAL", self.fixture.db), ("CONFIG", config)):
            p = patch.object(database, key, value)
            p.start()
            self.addCleanup(p.stop)

    def drop_database(self):
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.base, autocommit=True) as c:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(self.name)))

    def test_hydrate_writes_shared_database_and_retains_local_evidence(self):
        result = hydration.MODULE.import_people(self.fixture.db, self.root, apply=True)
        self.assertEqual(result["added"]["people"], 1)
        (self.root / "people-import-result.json").write_text(json.dumps(result))
        with database.connect(self.fixture.db) as remote:
            row = remote.execute(
                "SELECT full_name,current_role,notes FROM people WHERE person_id=?", ("p",)
            ).fetchone()
            self.assertEqual(row["full_name"], "Test Builder")
            self.assertEqual(row["current_role"], "Software engineer")
            self.assertIsNone(row["notes"])
            self.assertEqual(
                remote.raw.execute("SELECT id FROM public.existing").fetchone()["id"], 42
            )
        with sqlite3.connect(self.fixture.db) as legacy:
            self.assertEqual(legacy.execute("SELECT count(*) FROM people").fetchone()[0], 0)
        with database.private_connection(self.fixture.db) as local:
            self.assertEqual(local.execute("SELECT count(*) FROM people").fetchone()[0], 1)
            self.assertIsNotNone(
                local.execute("SELECT notes FROM people WHERE person_id='p'").fetchone()[0]
            )
        from crm.copy_builder import CopyBuilder

        self.assertEqual(
            CopyBuilder(self.fixture.db).context("a", "p")["person"]["full_name"], "Test Builder"
        )
        self.assertEqual(
            hydration.MODULE.import_people(self.fixture.db, self.root, apply=True)["status"],
            "already_applied",
        )

    def test_rollback_and_immutable_tag_history(self):
        hydration.MODULE.import_people(self.fixture.db, self.root, apply=True)
        c = database.connect(self.fixture.db)
        try:
            result = c.execute(
                "INSERT INTO person_tag_events(person_id,tag_id,action,evidence,reviewer) VALUES(?,?,?,?,?)",
                ("p", "founder", "add", "PRIVATE REASON", "test"),
            )
            self.assertIsInstance(result.lastrowid, int)
            c.rollback()
            self.assertEqual(c.execute("SELECT count(*) FROM person_tag_events").fetchone()[0], 0)
            c.execute(
                "INSERT INTO person_tag_events(person_id,tag_id,action,evidence,reviewer) VALUES(?,?,?,?,?)",
                ("p", "founder", "add", "PRIVATE REASON", "test"),
            )
            c.commit()
            with self.assertRaises(Exception):
                c.execute("UPDATE person_tag_events SET action=?", ("remove",))
            c.rollback()
            self.assertEqual(
                c.execute(
                    "SELECT action FROM current_person_tags WHERE person_id=?", ("p",)
                ).fetchone()[0],
                "add",
            )
        finally:
            c.close()

    def test_relationship_columns_and_engagement_on_postgres(self):
        from crm.relationships import Relationships

        with database.connect(self.fixture.db) as conn:
            conn.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES('rel_p','Person','person')"
            )
        crm = Relationships(self.fixture.db)
        crm.classify("human", "rel_p", "personal_agent_owners", "PRIVATE evidence", "test")
        crm.save_contact(
            "human",
            "rel_p",
            "github",
            "https://github.com/example",
            "available",
            "https://example.test/proof",
            "test",
            last_verified_at="2026-09-20T12:00:00Z",
        )
        args = dict(
            entity_type="human",
            entity_id="rel_p",
            channel="x",
            kind="like",
            account_key="operator",
            provider_id="like1",
            observed_at="2026-09-21T12:00:00Z",
            occurred_at="2026-09-20T12:00:00Z",
            evidence="PRIVATE receipt",
            reviewer="test",
            target_is_ours=True,
            target_url="https://x.com/operator/status/1",
        )
        self.assertTrue(crm.record(**args)["created"])
        self.assertFalse(crm.record(**args)["created"])
        row = crm.list("human", "rel_p")[0]
        self.assertIsNotNone(row["last_like_at"])
        self.assertIsNone(row["last_reply_at"])
        with database.connect(self.fixture.db) as conn:
            view = conn.execute(
                "SELECT * FROM v_relationship_crm WHERE entity_id=?", ("rel_p",)
            ).fetchone()
            self.assertEqual(view["audience"], row["audience"])
            self.assertEqual(view["github_url"], row["github_url"])
            self.assertIsNone(view["last_reply_at"])
            self.assertEqual(view["contact_channels"], ["github"])
            self.assertIsNotNone(view["last_like_at"])
            self.assertEqual(len(view["contact_history"]), 1)
            self.assertEqual(len(view["last_contact"]), 1)
            self.assertEqual(
                view["last_contact"][0]["event_id"], row["last_contact"][0]["event_id"]
            )
            self.assertEqual(view["last_contact"][0]["time_basis"], "occurred")
        with database.private_connection(self.fixture.db) as cache:
            self.assertEqual(
                cache.execute("SELECT count(*) FROM relationship_events").fetchone()[0], 1
            )

    def test_additive_relationship_migration_and_retry(self):
        import psycopg

        from crm.relationship_migration import apply

        with psycopg.connect(self.dsn) as raw:
            for table in ("relationship_events", "relationship_contacts", "relationship_profiles"):
                raw.execute("DROP TABLE crm_gtm." + table + " CASCADE")
            operator = raw.execute("SELECT current_user").fetchone()[0]
        report = apply(self.dsn, operator)
        self.assertTrue(report["applied"])
        self.assertTrue(apply(self.dsn, operator)["already_applied"])
        with database.connect(self.fixture.db) as conn:
            self.assertIsNotNone(
                conn.execute("SELECT * FROM v_relationship_crm LIMIT 1").fetchone()
            )

    def test_x_watcher_retries_shared_private_receipts(self):
        from test_x_reply_notifications import XNotificationWatcherTests

        fixture = XNotificationWatcherTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        with database.connect(self.fixture.db) as conn:
            conn.execute(
                "INSERT INTO people(person_id,full_name,normalized_name) VALUES('p1','Person','person')"
            )
            conn.execute(
                "INSERT INTO contact_points(contact_id,person_id,contact_type,value,normalized_value,first_seen_at,verification_status) VALUES('xrel','p1','x','https://x.com/them','them','2026-09-20T12:00:00Z','confirmed')"
            )

        def factory():
            return database.connect(self.fixture.db)

        for _ in range(2):
            result = fixture.watcher.ingest_x_notifications(
                fixture.capture(), expected_account="operator", shared_factory=factory
            )
            self.assertEqual(result["shared_confirmed"], 2)
        from crm.relationships import Relationships

        row = Relationships(self.fixture.db).list("human", "p1")[0]
        self.assertIsNotNone(row["last_like_at"])
        self.assertIsNotNone(row["last_reply_at"])

    def test_repeat_migration_refuses_existing_namespace(self):
        with self.assertRaisesRegex(ValueError, "already exists"):
            migrate(self.dsn, self.fixture.db)
        self.assertTrue(all(r["matches"] for r in self.report["verified"].values()))

    def test_private_cache_cannot_write_shared_tables(self):
        with database.private_connection(self.fixture.db) as local:
            with self.assertRaises(sqlite3.DatabaseError):
                local.execute("UPDATE agents SET canonical_name='WRONG'")
        with database.connect(self.fixture.db) as remote:
            self.assertEqual(
                remote.execute("SELECT canonical_name FROM agents WHERE agent_id='a'").fetchone()[
                    0
                ],
                "Example",
            )

    def test_configured_failure_never_uses_legacy_sqlite(self):
        (self.root / "accounts/test.url").write_text(
            "postgresql://gtm@127.0.0.1:1/missing?connect_timeout=1"
        )
        with self.assertRaises(Exception):
            database.connect(self.fixture.db)

    def test_concurrent_updates_do_not_silently_overwrite(self):
        import psycopg

        a, b = database.connect(self.fixture.db), database.connect(self.fixture.db)
        try:
            a.execute("SELECT canonical_name FROM agents WHERE agent_id='a'").fetchone()
            b.execute("SELECT canonical_name FROM agents WHERE agent_id='a'").fetchone()
            a.execute("UPDATE agents SET canonical_name=? WHERE agent_id=?", ("First", "a"))
            a.commit()
            with self.assertRaises(psycopg.errors.SerializationFailure):
                b.execute("UPDATE agents SET canonical_name=? WHERE agent_id=?", ("Second", "a"))
            b.rollback()
            self.assertEqual(
                b.execute("SELECT canonical_name FROM agents WHERE agent_id='a'").fetchone()[0],
                "First",
            )
        finally:
            a.close()
            b.close()
