"""Explicit additive migration for route attestations and route suppressions."""

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from crm.database import ROOT, configured

MARKER = "relationship_route_evidence_v1"
COLUMNS = {
    "agent_operated": "INTEGER CHECK(agent_operated IN (0,1))",
    "agent_operated_evidence_url": "TEXT",
    "agent_operated_verified_at": "TEXT",
}


def suppression_statements():
    with sqlite3.connect(":memory:") as conn:
        conn.executescript((ROOT / "sql/relationships_schema.sql").read_text())
        return [
            row[0]
            for row in conn.execute(
                "SELECT sql FROM sqlite_master WHERE tbl_name='relationship_contact_suppressions' "
                "AND sql IS NOT NULL ORDER BY type DESC"
            )
        ]


def apply_sqlite(conn):
    """Atomic upgrade preserving rows, foreign keys, indexes and history triggers.

    SQLite cannot alter CHECK constraints. Rebuild only the two affected tables,
    with FK enforcement temporarily disabled outside the transaction, then verify.
    Caller must not have an active transaction; this function owns its transaction.
    """
    if conn.in_transaction:
        raise ValueError("route migration requires a connection without an active transaction")
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    legacy_alter = conn.execute("PRAGMA legacy_alter_table").fetchone()[0]
    conn.execute("PRAGMA legacy_alter_table=ON")
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        names = {row[1] for row in conn.execute("PRAGMA table_info(relationship_contacts)")}
        if names:
            for name, definition in COLUMNS.items():
                if name not in names:
                    conn.execute(
                        f"ALTER TABLE relationship_contacts ADD COLUMN {name} {definition}"
                    )
            for table in ("relationship_contacts", "relationship_events"):
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not row:
                    continue
                original = row[0]
                upgraded = re.sub(
                    r"CHECK\s*\(channel IN \(([^)]*)\)\)",
                    lambda m: (
                        "CHECK(channel IN ("
                        + m[1]
                        + "".join(
                            ", '" + c + "'"
                            for c in ("webmcp", "discord")
                            if "'" + c + "'" not in m[1]
                        )
                        + "))"
                    ),
                    original,
                    flags=re.I,
                )
                check = re.search(r"CHECK\s*\(channel IN \(([^)]*)\)\)", upgraded, re.I)
                if not check or not all("'" + c + "'" in check[1] for c in ("webmcp", "discord")):
                    raise ValueError("unrecognized channel constraint; migration rolled back")
                if upgraded == original:
                    continue
                dependent = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
                    (table,),
                ).fetchall()
                temporary = table + "_route_upgrade"
                create = upgraded.replace(table, temporary, 1)
                conn.execute(create)
                columns = ",".join(
                    '"' + r[1] + '"' for r in conn.execute(f"PRAGMA table_info({table})")
                )
                conn.execute(f"INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {table}")
                conn.execute(f"DROP TABLE {table}")
                conn.execute(f"ALTER TABLE {temporary} RENAME TO {table}")
                for item in dependent:
                    conn.execute(item[0])
            for statement in suppression_statements():
                conn.execute(
                    statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ").replace(
                        "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "
                    )
                )
            if conn.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("foreign key violations; migration rolled back")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.execute(f"PRAGMA foreign_keys={fk}")
        conn.execute(f"PRAGMA legacy_alter_table={legacy_alter}")


def apply(dsn, operator_role):
    import psycopg
    from psycopg import sql

    with psycopg.connect(
        dsn,
        connect_timeout=15,
        options="-c search_path=crm_gtm,pg_catalog -c statement_timeout=30000",
    ) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (MARKER,))
        version = conn.execute(
            "SELECT value FROM crm_backend_meta WHERE key='schema_version'"
        ).fetchone()
        if not version or version[0] != "1":
            raise ValueError("expected existing CRM schema version 1")
        conn.execute(
            "LOCK TABLE relationship_contacts, relationship_events, suppressions IN SHARE ROW EXCLUSIVE MODE"
        )

        def facts_digest():
            digest = hashlib.sha256()
            for table, key in (
                ("relationship_contacts", "contact_id"),
                ("relationship_events", "event_id"),
                ("suppressions", "suppression_id"),
            ):
                for row in conn.execute(
                    sql.SQL(
                        "SELECT to_jsonb(t) - 'agent_operated' - 'agent_operated_evidence_url' - 'agent_operated_verified_at' FROM {} t ORDER BY {}"
                    ).format(sql.Identifier(table), sql.Identifier(key))
                ):
                    digest.update(json.dumps(row[0], sort_keys=True).encode())
            return digest.hexdigest()

        original_digest = facts_digest()
        before = conn.execute("SELECT count(*) FROM relationship_contacts").fetchone()[0]
        for name, definition in COLUMNS.items():
            conn.execute(
                f"ALTER TABLE relationship_contacts ADD COLUMN IF NOT EXISTS {name} {definition}"
            )
        for statement in suppression_statements():
            conn.execute(
                statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ").replace(
                    "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "
                )
            )
        for table in ("relationship_contacts", "relationship_events"):
            constraints = conn.execute(
                "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid=%s::regclass AND contype='c'",
                (table,),
            ).fetchall()
            matched = False
            for name, definition in constraints:
                # Widen the existing channel-only constraint, preserving all other checks.
                if re.match(r"CHECK \(\(channel = ANY", definition):
                    matched = True
                    if "'webmcp'" in definition and "'discord'" in definition:
                        continue
                    updated = definition.replace("])", ", 'webmcp'::text, 'discord'::text])")
                    if updated == definition:
                        raise ValueError("unrecognized channel constraint; migration rolled back")
                    conn.execute(
                        sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                            sql.Identifier(table), sql.Identifier(name)
                        )
                    )
                    conn.execute(
                        sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} ").format(
                            sql.Identifier(table), sql.Identifier(name)
                        )
                        + sql.SQL(updated)
                    )
            if not matched:
                raise ValueError("unrecognized channel constraint; migration rolled back")
            definitions = conn.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='c'",
                (table,),
            ).fetchall()
            if not any(
                re.match(r"CHECK \(\(channel = ANY", r[0])
                and all("'" + c + "'" in r[0] for c in ("webmcp", "discord"))
                for r in definitions
            ):
                raise ValueError("channel migration verification failed")
        conn.execute("REVOKE ALL ON relationship_contact_suppressions FROM PUBLIC")
        conn.execute(
            sql.SQL("GRANT SELECT,INSERT,UPDATE ON relationship_contact_suppressions TO {}").format(
                sql.Identifier(operator_role)
            )
        )
        conn.execute(
            "INSERT INTO crm_backend_meta(key,value) VALUES(%s,'1') ON CONFLICT(key) DO NOTHING",
            (MARKER,),
        )
        after = conn.execute("SELECT count(*) FROM relationship_contacts").fetchone()[0]
        if before != after or original_digest != facts_digest():
            raise RuntimeError("route counts changed during migration")
    return {
        "migration": MARKER,
        "route_count_before": before,
        "route_count_after": after,
        "existing_facts_changed": False,
        "legacy_facts_sha256": original_digest,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite", type=Path)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("use a new receipt path")
    if args.sqlite:
        if not args.sqlite.exists():
            parser.error("SQLite database must exist")
        with sqlite3.connect(args.sqlite) as conn:
            apply_sqlite(conn)
        report = {"migration": MARKER, "sqlite": str(args.sqlite)}
    else:
        config = configured()
        if not config or not args.credential_file:
            parser.error("configured shared CRM and explicit migration credential required")
        dsn = args.credential_file.read_text().strip()
        target = urlsplit(dsn)
        if (
            target.hostname != config["expected_host"]
            or target.path.lstrip("/") != config["expected_database"]
        ):
            raise ValueError("migration target differs from configured CRM")
        report = apply(dsn, config["operator_role"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.chmod(0o600)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
