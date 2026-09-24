"""Add only the human/agent relationship tables to an existing CRM namespace."""

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

from crm.database import ROOT, configured

TABLES = ("relationship_profiles", "relationship_contacts", "relationship_events")
MARKER = "human_agent_relationships_v1"
VIEW_MARKER = "relationship_contact_history_v2"


def view_statements():
    return [
        """CREATE OR REPLACE FUNCTION crm_relationship_timestamp(value text) RETURNS timestamptz
        LANGUAGE plpgsql STABLE AS $$ BEGIN
            IF value IS NULL OR value !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN RETURN NULL; END IF;
            RETURN value::timestamptz;
        EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL;
        END $$""",
        (ROOT / "sql/relationships_view.postgres.sql").read_text(),
    ]


def statements():
    local = sqlite3.connect(":memory:")
    local.executescript((ROOT / "sql/relationships_schema.sql").read_text())
    ddl = [
        row[0]
        for row in local.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','index') "
            "AND tbl_name IN (?,?,?) AND sql IS NOT NULL "
            "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END,rowid",
            TABLES,
        )
    ]
    local.close()
    return ddl + [
        "CREATE TRIGGER relationship_events_immutable BEFORE UPDATE OR DELETE ON relationship_events FOR EACH ROW EXECUTE FUNCTION crm_immutable()"
    ]


def apply(dsn, operator_role):
    import psycopg
    from psycopg import sql

    with psycopg.connect(
        dsn,
        connect_timeout=15,
        options="-c search_path=crm_gtm,pg_catalog -c statement_timeout=30000",
    ) as conn:
        conn.isolation_level = psycopg.IsolationLevel.SERIALIZABLE
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('crm_gtm.relationships.v1'))")
        version = conn.execute(
            "SELECT value FROM crm_backend_meta WHERE key='schema_version'"
        ).fetchone()
        if not version or version[0] != "1":
            raise ValueError("expected existing CRM schema version 1")
        prior = conn.execute(
            "SELECT value FROM crm_backend_meta WHERE key=%s", (MARKER,)
        ).fetchone()
        if prior:
            updated = conn.execute(
                "SELECT value FROM crm_backend_meta WHERE key=%s", (VIEW_MARKER,)
            ).fetchone()
            if updated:
                return {"migration": VIEW_MARKER, "applied": False, "already_applied": True}
            for statement in view_statements():
                conn.execute(statement)
            conn.execute("INSERT INTO crm_backend_meta(key,value) VALUES(%s,'1')", (VIEW_MARKER,))
            return {"migration": VIEW_MARKER, "applied": True, "existing_records_changed": False}
        existing = [
            name
            for name in TABLES
            if conn.execute("SELECT to_regclass(%s)", ("crm_gtm." + name,)).fetchone()[0]
        ]
        if existing:
            raise ValueError("unmarked relationship tables exist; reconcile before migration")
        before = {
            name: conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))
            ).fetchone()[0]
            for name in (
                "people",
                "agents",
                "contact_points",
                "outreach_events",
                "x_engagement_events",
            )
        }
        for statement in statements():
            conn.execute(statement)
        for statement in view_statements():
            conn.execute(statement)
        conn.execute(
            sql.SQL("GRANT SELECT ON v_relationship_crm TO {}").format(
                sql.Identifier(operator_role)
            )
        )
        for name in TABLES:
            conn.execute(sql.SQL("REVOKE ALL ON {} FROM PUBLIC").format(sql.Identifier(name)))
            conn.execute(
                sql.SQL("GRANT SELECT,INSERT,UPDATE ON {} TO {}").format(
                    sql.Identifier(name), sql.Identifier(operator_role)
                )
            )
        conn.execute("INSERT INTO crm_backend_meta(key,value) VALUES(%s,%s)", (MARKER, "1"))
        conn.execute("INSERT INTO crm_backend_meta(key,value) VALUES(%s,%s)", (VIEW_MARKER, "1"))
        after = {
            name: conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(name))
            ).fetchone()[0]
            for name in before
        }
        if before != after:
            raise RuntimeError("existing CRM counts changed during migration")
    return {"migration": MARKER, "applied": True, "tables": list(TABLES), "existing_counts": after}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.apply:
        print("\n\n".join(statements() + view_statements()))
        return
    if not args.credential_file or not args.output:
        parser.error("--apply requires --credential-file and --output")
    if args.output.exists():
        raise ValueError("use a new migration receipt path")
    config = configured()
    if not config:
        raise ValueError("configured shared CRM required")
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
    print(json.dumps(report))


if __name__ == "__main__":
    main()
