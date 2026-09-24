"""Shared PostgreSQL CRM; private copy/evidence remain in a local SQLite cache.

Only the canonical path opts into the configured backend. Explicit fixture/backup
paths remain SQLite. A configured remote failure never falls back to SQLite.
"""

import argparse
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data/crm.sqlite3"
CONFIG = ROOT / "accounts/database.json"
PRIVATE_TABLES = {
    "copy_revisions",
    "email_reviews",
    "email_preparations",
    "draft_messages",
    "message_templates",
}
PRIVATE_COLUMNS = {
    "notes",
    "excerpt",
    "archived_path",
    "raw_record_json",
    "checkpoint_json",
    "error_message",
    "decision_json",
    "manifest_json",
    "before_json",
    "after_json",
    "evidence_json",
    "reversible_snapshot_json",
    "evidence",
    "external_reference",
}
IDENTITIES = {
    "route_reviews": "review_id",
    "route_review_changes": "change_id",
    "person_tag_events": "event_id",
}
APPEND_ONLY = {
    "relationship_events",
    "route_review_runs",
    "route_reviews",
    "channel_reviews",
    "route_review_changes",
    "person_tag_events",
}
SCHEMAS = (
    "schema.sql",
    "copy_schema.sql",
    "email_schema.sql",
    "route_review_schema.sql",
    "tags_schema.sql",
    "relationships_schema.sql",
)


def schema_connection():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    for name in SCHEMAS:
        c.executescript((ROOT / "sql" / name).read_text())
    return c


def metadata():
    with schema_connection() as c:
        return {
            r["name"]: {
                "sql": r["sql"],
                "columns": [dict(x) for x in c.execute('PRAGMA table_info("' + r["name"] + '")')],
            }
            for r in c.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }


def shared_tables():
    return {k: v for k, v in metadata().items() if k not in PRIVATE_TABLES}


def configured(path=CANONICAL):
    if Path(path).resolve() != CANONICAL.resolve() or not CONFIG.exists():
        return None
    result = json.loads(CONFIG.read_text())
    if result.get("backend") != "postgresql" or result.get("schema") != "crm_gtm":
        raise RuntimeError("Invalid CRM backend configuration; no local fallback")
    return result


def public_value(table, column, value):
    """Remove private text before binding any values to a network request."""
    if column not in PRIVATE_COLUMNS:
        return value
    if column == "decision_json" and table == "route_reviews":
        d = json.loads(value or "{}")
        keys = (
            "person_id",
            "qualification",
            "role_status",
            "outreach_score",
            "score_status",
            "readiness",
        )
        return json.dumps(
            {**{k: d[k] for k in keys if k in d}, "private_detail_available": False}, sort_keys=True
        )
    if column.endswith("_json"):
        return "{}" if value is not None else None
    if column == "evidence":
        return "Evidence retained privately by the recording operator"
    return None


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Result:
    def __init__(self, rows=(), rowcount=0, lastrowid=None, columns=()):
        self.rows = iter(rows)
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.description = [(name,) for name in columns]

    def fetchone(self):
        return next(self.rows, None)

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return self.rows


def named_sql(query, params):
    # Tokenizer positions keep literal question marks/URLs unchanged.
    from sqlglot import Tokenizer, tokens

    positions = [
        t.start
        for t in Tokenizer(dialect="sqlite").tokenize(query)
        if t.token_type == tokens.TokenType.PLACEHOLDER
    ]
    if len(positions) != len(params):
        raise ValueError("CRM SQL parameter count mismatch")
    for i, pos in reversed(list(enumerate(positions))):
        query = query[:pos] + ":p" + str(i) + query[pos + 1 :]
    return query, {"p" + str(i): value for i, value in enumerate(params)}


def translate(query, params=()):
    import sqlglot
    from sqlglot import exp

    query = query.replace("strftime('%Y-%m-%dT%H:%M:%fZ', 'now')", "crm_utc_now()")
    query = query.replace("strftime('%Y-%m-%dT%H:%M:%fZ','now')", "crm_utc_now()")
    query = re.sub(r"(?<![\w\"'])current_role(?![\w\"'])", '"current_role"', query)
    query, bindings = named_sql(query, params)
    tree = sqlglot.parse_one(query, read="sqlite")
    if not isinstance(tree, (exp.Select, exp.Union, exp.Insert, exp.Update, exp.Delete)):
        raise ValueError("Unsupported shared CRM SQL operation")
    allowed = set(shared_tables()) | {
        "latest_route_reviews",
        "current_person_tags",
        "v_contact_crm",
        "v_relationship_crm",
        "v_review_queue",
    }
    for table in tree.find_all(exp.Table):
        if table.db or table.catalog or table.name not in allowed:
            raise ValueError(
                "Query outside shared CRM tables; use private_connection for private copy"
            )
    if isinstance(tree, exp.Delete):
        raise ValueError("Shared CRM deletions require an explicit migration; history is retained")
    target = None
    private_values = {}
    if isinstance(tree, exp.Insert):
        target = tree.this.this.name if isinstance(tree.this, exp.Schema) else tree.this.name
        columns = (
            [x.name for x in tree.this.expressions]
            if isinstance(tree.this, exp.Schema)
            else [c["name"] for c in shared_tables()[target]["columns"]]
        )
        if not isinstance(tree.expression, exp.Values):
            raise ValueError("Only explicit reviewed VALUES inserts are supported")
        for row in tree.expression.expressions:
            if len(row.expressions) != len(columns):
                raise ValueError("Insert column count mismatch")
            for column, value in zip(columns, row.expressions):
                if column in PRIVATE_COLUMNS:
                    if isinstance(value, exp.Placeholder):
                        private_values[column] = bindings[value.name]
                        bindings[value.name] = public_value(target, column, bindings[value.name])
                    elif isinstance(value, (exp.Literal, exp.Null)):
                        private_values[column] = (
                            value.this if isinstance(value, exp.Literal) else None
                        )
                        clean = public_value(
                            target, column, value.this if isinstance(value, exp.Literal) else None
                        )
                        value.replace(exp.Null() if clean is None else exp.Literal.string(clean))
                    else:
                        raise ValueError("Private field must be a literal or bound value")
        if tree.args.get("alternative") == "IGNORE":
            tree.set("alternative", None)
            tree.set("conflict", exp.OnConflict(action=exp.Var(this="DO NOTHING")))
    elif isinstance(tree, exp.Update):
        target = tree.this.name
        for assignment in tree.expressions:
            column, value = assignment.this.name, assignment.expression
            if column in PRIVATE_COLUMNS:
                if not isinstance(value, exp.Placeholder):
                    raise ValueError("Private update must use a bound value")
                private_values[column] = bindings[value.name]
                bindings[value.name] = public_value(target, column, bindings[value.name])
    # Conflict UPDATE clauses may only refer to already-sanitized excluded values.
    if isinstance(tree, exp.Insert) and tree.args.get("conflict"):
        for assignment in tree.args["conflict"].expressions:
            if assignment.this.name in PRIVATE_COLUMNS and not isinstance(
                assignment.expression, exp.Column
            ):
                raise ValueError("Unsupported private upsert expression")
    identity = IDENTITIES.get(target) if isinstance(tree, exp.Insert) else None
    if isinstance(tree, (exp.Insert, exp.Update)) and not tree.args.get("returning"):
        tree.set("returning", exp.Returning(expressions=[exp.Star()]))
    sql = tree.sql(dialect="postgres", unsupported_level=sqlglot.ErrorLevel.RAISE)
    sql = re.sub(r"%%\((p\d+)\)s", r"%(\1)s", sql.replace("%", "%%"))
    used = {p.name for p in tree.find_all(exp.Placeholder)}
    return sql, {k: v for k, v in bindings.items() if k in used}, identity, target, private_values


class PostgresConnection:
    row_factory = None

    def __init__(self, dsn, *, journal=True):
        import psycopg
        from psycopg.rows import dict_row

        self.raw = psycopg.connect(
            dsn,
            connect_timeout=15,
            row_factory=dict_row,
            options="-c search_path=crm_gtm,pg_catalog -c statement_timeout=30000",
        )
        self.raw.isolation_level = psycopg.IsolationLevel.SERIALIZABLE
        marker = self.raw.execute(
            "SELECT value FROM crm_gtm.crm_backend_meta WHERE key='schema_version'"
        ).fetchone()
        if not marker or marker["value"] != "1":
            self.raw.close()
            raise RuntimeError("Wrong or uninitialized shared CRM database")
        self.total_changes = 0
        self.journal = journal
        self.operations = []
        self.private_updates = []

    def execute(self, query, params=()):
        import psycopg

        normalized = " ".join(query.strip().rstrip(";").split()).lower()
        if normalized in (
            "begin immediate",
            "begin",
            "pragma foreign_keys=on",
            "pragma foreign_keys = on",
            "pragma busy_timeout = 5000",
        ):
            return Result()
        if normalized in ("pragma integrity_check", "pragma quick_check"):
            self.raw.execute("SELECT 1 FROM crm_gtm.crm_backend_meta")
            return Result([Row(integrity_check="ok")])
        if normalized == "pragma foreign_key_check":
            # PostgreSQL enforces the migrated validated FK constraints on every write.
            return Result()
        if "sqlite_master" in normalized:
            match = re.search(r"name\s*=\s*'([a-z_]+)'", normalized)
            if not match:
                raise ValueError("Unsupported schema introspection")
            exists = self.raw.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS present", ("crm_gtm." + match[1],)
            ).fetchone()["present"]
            return Result([Row(present=1)] if exists else [])
        sql, values, identity, target, private_values = translate(query, params)
        write = normalized.startswith(("insert", "update", "delete"))
        if write and self.journal:
            # Preserve exact private authored values locally; never upload this journal.
            self.operations.append({"sql": query, "parameters": list(params)})
        try:
            cursor = self.raw.execute(sql, values)
        except psycopg.IntegrityError as exc:
            raise sqlite3.IntegrityError(
                "Shared CRM constraint rejected write: " + type(exc).__name__
            ) from None
        rows = [Row(r) for r in cursor.fetchall()] if cursor.description else []
        if private_values:
            keys = [c["name"] for c in shared_tables()[target]["columns"] if c["pk"]]
            for row in rows:
                self.private_updates.append(
                    {
                        "table": target,
                        "keys": {k: row[k] for k in keys},
                        "values": private_values.copy(),
                    }
                )
        self.total_changes += max(cursor.rowcount, 0) if write else 0
        return Result(
            rows,
            cursor.rowcount,
            rows[0][identity] if identity and rows else None,
            [d.name for d in cursor.description or []],
        )

    def executemany(self, query, rows):
        for params in rows:
            self.execute(query, params)

    def executescript(self, text):
        allowed = {(ROOT / "sql" / name).read_text() for name in SCHEMAS}
        if text not in allowed:
            raise ValueError("Runtime schema changes are not shared CRM setup")
        # Schema is installed once by the explicit migration, never by an operator.

    def commit(self):
        receipt = None
        if self.operations:
            folder = ROOT / "logs/shared-crm-operations"
            folder.mkdir(parents=True, exist_ok=True)
            receipt = folder / (str(uuid.uuid4()) + ".json")
            fd = os.open(receipt, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {
                        "state": "commit_pending",
                        "operations": self.operations,
                        "private_updates": self.private_updates,
                    },
                    stream,
                )
            self.raw.execute(
                "INSERT INTO crm_gtm.crm_commits(commit_id,operator,operation_count) VALUES(%s,current_user,%s)",
                (receipt.stem, len(self.operations)),
            )
        try:
            self.raw.commit()
        except Exception:
            raise RuntimeError(
                "Shared commit outcome needs reconciliation; do not replay blindly"
            ) from None
        if receipt:
            data = json.loads(receipt.read_text())
            data["state"] = "confirmed"
            receipt.write_text(json.dumps(data))
        self.operations = []
        self.private_updates = []

    def rollback(self):
        self.raw.rollback()
        self.operations = []
        self.private_updates = []

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, typ, value, traceback):
        # Match sqlite3: a transaction context does not close the connection.
        self.rollback() if typ else self.commit()

    def __del__(self):
        if hasattr(self, "raw"):
            self.raw.close()

    def backup(self, destination):
        export_sqlite(self, destination)


_active_transaction = ContextVar("crm_active_transaction", default=None)


class _TransactionConnection:
    """Borrow a connection without allowing helpers to commit the outer unit."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        if sql.strip().upper() in ("BEGIN", "BEGIN IMMEDIATE"):
            return self.connection.execute("SELECT 1")
        return self.connection.execute(sql, parameters)

    def __enter__(self):
        return self

    def __exit__(self, typ, value, traceback):
        return False


@contextmanager
def transaction(path=CANONICAL):
    """One all-or-nothing unit across CRM helpers using this database path."""
    if _active_transaction.get() is not None:
        raise RuntimeError("nested CRM transaction is not supported")
    connection = connect(path)
    token = _active_transaction.set((Path(path).resolve(), _TransactionConnection(connection)))
    try:
        if isinstance(connection, sqlite3.Connection):
            connection.execute("BEGIN IMMEDIATE")
        with connection:
            yield
    finally:
        _active_transaction.reset(token)
        connection.close()


def connect(path=CANONICAL):
    active = _active_transaction.get()
    if active is not None and Path(path).resolve() == active[0]:
        return active[1]
    config = configured(path)
    if config:
        credential = ROOT / config["credential_file"]
        dsn = credential.read_text().strip()
        target = urlsplit(dsn)
        for setting, observed in (
            ("expected_host", target.hostname),
            ("expected_database", target.path.lstrip("/")),
            ("operator_role", target.username),
        ):
            if setting in config and config[setting] != observed:
                raise RuntimeError(
                    "CRM connection identity differs from the verified cutover; no fallback"
                )
        return PostgresConnection(dsn)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def export_sqlite(connection, destination):
    """Consistent sanitized shared snapshot; private ledgers need their own backup."""
    destination.row_factory = sqlite3.Row
    for name in SCHEMAS:
        destination.executescript((ROOT / "sql" / name).read_text())
    triggers = destination.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    destination.execute("PRAGMA foreign_keys=OFF")
    for row in triggers:
        destination.execute('DROP TRIGGER "' + row["name"] + '"')
    for table in shared_tables():
        rows = connection.raw.execute('SELECT * FROM crm_gtm."' + table + '"').fetchall()
        destination.execute('DELETE FROM "' + table + '"')
        if rows:
            # A live additive migration can temporarily be ahead of the portable
            # SQLite schema. Preserve every column the snapshot understands and
            # skip newer columns instead of making the entire safety backup fail.
            local_columns = {
                row["name"] for row in destination.execute('PRAGMA table_info("' + table + '")')
            }
            columns = [column for column in rows[0] if column in local_columns]
            destination.executemany(
                'INSERT INTO "'
                + table
                + '" ('
                + ",".join('"' + c + '"' for c in columns)
                + ") VALUES ("
                + ",".join("?" for _ in columns)
                + ")",
                [tuple(r[c] for c in columns) for r in rows],
            )
    for row in triggers:
        destination.execute(row["sql"])
    destination.commit()
    destination.execute("PRAGMA foreign_keys=ON")


def private_connection(path=CANONICAL, *, readonly=False):
    """Refresh shared facts before local-only copy/history operations.

    Public rows in this cache are never an offline authority and cannot be written
    by callers. Existing private columns are retained on matching immutable IDs.
    """
    if not configured(path):
        c = sqlite3.connect(
            Path(path).resolve().as_uri() + ("?mode=ro" if readonly else ""), uri=True, timeout=30
        )
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c
    cache = ROOT / "data/private-crm-cache.sqlite3"
    if not cache.exists():
        raise RuntimeError("Private CRM cache missing; restore the operator's private backup")
    cache.chmod(0o600)
    with connect(path) as remote:
        local = sqlite3.connect(cache, timeout=30)
        local.row_factory = sqlite3.Row
        # Additive tables must exist before refreshing an older private cache.
        local.executescript((ROOT / "sql/relationships_schema.sql").read_text())
        from crm.route_evidence_migration import apply_sqlite

        apply_sqlite(local)
        local.commit()
        local.execute("PRAGMA foreign_keys=OFF")
        local.execute("BEGIN IMMEDIATE")
        triggers = local.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        try:
            for trigger in triggers:
                local.execute('DROP TRIGGER "' + trigger["name"] + '"')
            for table, spec in shared_tables().items():
                keys = [
                    c["name"] for c in sorted(spec["columns"], key=lambda c: c["pk"]) if c["pk"]
                ]
                for row in remote.raw.execute('SELECT * FROM crm_gtm."' + table + '"').fetchall():
                    where = " AND ".join('"' + key + '"=?' for key in keys)
                    old = local.execute(
                        'SELECT * FROM "' + table + '" WHERE ' + where, [row[key] for key in keys]
                    ).fetchone()
                    if old:
                        for column in PRIVATE_COLUMNS.intersection(row):
                            row[column] = old[column]
                    columns = list(row)
                    local.execute(
                        'INSERT OR REPLACE INTO "'
                        + table
                        + '" ('
                        + ",".join('"' + c + '"' for c in columns)
                        + ") VALUES ("
                        + ",".join("?" for _ in columns)
                        + ")",
                        [row[c] for c in columns],
                    )
            folder = ROOT / "logs/shared-crm-operations"
            for journal in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime_ns):
                entry = json.loads(journal.read_text())
                if entry["state"] == "commit_pending":
                    committed = remote.raw.execute(
                        "SELECT 1 FROM crm_gtm.crm_commits WHERE commit_id=%s", (journal.stem,)
                    ).fetchone()
                    if not committed:
                        # No replay: preserve the uncertain record for operator review.
                        continue
                    entry["state"] = "confirmed"
                    journal.write_text(json.dumps(entry))
                for change in entry.get("private_updates", []):
                    table, keys, values = change["table"], change["keys"], change["values"]
                    if table not in shared_tables() or not set(values) <= PRIVATE_COLUMNS:
                        raise ValueError("Invalid private overlay journal")
                    local.execute(
                        'UPDATE "'
                        + table
                        + '" SET '
                        + ",".join('"' + k + '"=?' for k in values)
                        + " WHERE "
                        + " AND ".join('"' + k + '"=?' for k in keys),
                        list(values.values()) + list(keys.values()),
                    )
            for trigger in triggers:
                local.execute(trigger["sql"])
            if local.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Private cache references require reconciliation")
            local.commit()
        except Exception:
            local.rollback()
            local.close()
            raise
    local.execute("PRAGMA foreign_keys=ON")

    def authorize(action, arg1, arg2, database, trigger):
        if (
            action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE)
            and arg1 not in PRIVATE_TABLES
            and arg1 not in ("sqlite_master", "sqlite_temp_master")
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    if readonly:
        local.execute("PRAGMA query_only=ON")
    else:
        local.set_authorizer(authorize)
    return local


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init-private-cache", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if not configured():
        raise RuntimeError("Shared CRM is not configured; run the documented connection setup")
    with connect() as shared:
        identity = shared.raw.execute(
            "SELECT current_database() AS database,current_user AS operator"
        ).fetchone()
        if args.init_private_cache:
            target = ROOT / "data/private-crm-cache.sqlite3"
            if target.exists():
                raise RuntimeError("Private cache already exists; refusing to overwrite")
            target.parent.mkdir(parents=True, exist_ok=True)
            with schema_connection() as source, sqlite3.connect(target) as destination:
                source.backup(destination)
            target.chmod(0o600)
        if args.backup:
            if args.backup.exists():
                raise RuntimeError("Backup destination exists; refusing to overwrite")
            args.backup.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(args.backup) as target:
                shared.backup(target)
                if (
                    target.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                    or target.execute("PRAGMA foreign_key_check").fetchall()
                ):
                    raise RuntimeError("Shared snapshot failed integrity checks")
            args.backup.chmod(0o600)
        counts = {
            table: shared.execute("SELECT count(*) FROM " + table).fetchone()[0]
            for table in ("people", "companies", "agents", "contact_points", "sources")
        }
    if args.init_private_cache:
        with private_connection(readonly=True) as local:
            if local.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("New private cache failed validation")
    print(
        json.dumps(
            {
                "backend": "postgresql",
                "schema": "crm_gtm",
                **identity,
                "counts": counts,
                "sqlite_fallback": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
