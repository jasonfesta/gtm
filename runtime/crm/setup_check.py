"""Read-only checkout preflight. Never initializes databases or connects accounts."""

import argparse
import datetime as dt
import importlib.util
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def inspect_database(path, required_tables):
    if not path.is_file():
        return {
            "status": "missing",
            "next_action": "Restore the agreed private backup; do not seed a replacement.",
        }
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            conn.execute("PRAGMA query_only=ON")
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            foreign_key_errors = len(conn.execute("PRAGMA foreign_key_check").fetchall())
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            missing = sorted(set(required_tables) - tables)
        return {
            "status": "ok"
            if integrity == ["ok"] and not foreign_key_errors and not missing
            else "blocked",
            "integrity": integrity,
            "foreign_key_errors": foreign_key_errors,
            "missing_tables": missing,
        }
    except sqlite3.Error as exc:
        return {
            "status": "blocked",
            "error": str(exc),
            "next_action": "Preserve the file and inspect the backup; do not initialize over it.",
        }


def inspect_checkout(root):
    root = root.resolve()
    required = (
        "AGENTS.md",
        "agents/README.md",
        "Makefile",
        "pyproject.toml",
        "runtime/README.md",
        "docs/ACCOUNTS.md",
        "runtime/sql/schema.sql",
        "runtime/crm/mcp_server.py",
        "runtime/copy/copy.md",
    )
    missing = [name for name in required if not (root / name).is_file()]
    databases = {
        "crm": inspect_database(
            root / "runtime/data/crm.sqlite3", {"people", "contact_points", "sources"}
        ),
        "linkedin": inspect_database(
            root / "runtime/linkedin/data/linkedin.sqlite3", {"accounts", "messages"}
        ),
    }
    dependencies = {
        "python_3_12_or_newer": sys.version_info >= (3, 12),
        "ruff_in_current_environment": importlib.util.find_spec("ruff") is not None,
        "yaml_in_current_environment": importlib.util.find_spec("yaml") is not None,
        "node": shutil.which("node") is not None,
        "make": shutil.which("make") is not None,
    }
    shared = root / "runtime/accounts/database.json"
    if shared.exists():
        try:
            config = json.loads(shared.read_text())
            if config.get("backend") != "postgresql" or config.get("schema") != "crm_gtm":
                raise ValueError("invalid shared CRM configuration")
            credential = root / "runtime" / config["credential_file"]
            if not credential.is_file():
                raise ValueError("operator database credential file missing")
            databases["crm"] = {
                "status": "configured",
                "backend": "postgresql",
                "live_verified": False,
                "next_action": "Run make integrity to verify the configured shared database; no SQLite fallback.",
            }
            databases["private_crm_cache"] = inspect_database(
                root / "runtime/data/private-crm-cache.sqlite3",
                {"people", "copy_revisions", "email_preparations"},
            )
            dependencies["psycopg"] = importlib.util.find_spec("psycopg") is not None
            dependencies["sqlglot"] = importlib.util.find_spec("sqlglot") is not None
        except (OSError, ValueError, KeyError) as exc:
            databases["crm"] = {"status": "blocked", "error": str(exc)}
    binding = root / "runtime/accounts/operator.json"
    account_status = (
        "owner_confirmation_and_live_reverification_required"
        if binding.is_file()
        else "setup_required"
    )
    stale_paths = []
    config_errors = []
    for folder in (root / "runtime/linkedin/accounts", root / "runtime/x/accounts"):
        for path in sorted(folder.glob("*.json")):
            try:
                config = json.loads(path.read_text())
                if not isinstance(config, dict):
                    raise ValueError("account configuration must be an object")
                for field in ("local_root", "database_path", "stop_file"):
                    value = config.get(field)
                    if value and Path(value).is_absolute():
                        try:
                            Path(value).resolve().relative_to(root)
                        except ValueError:
                            stale_paths.append(
                                {"file": str(path.relative_to(root)), "field": field}
                            )
            except (ValueError, OSError) as exc:
                config_errors.append({"file": str(path.relative_to(root)), "error": str(exc)})
    ready = (
        not missing
        and all(dependencies.values())
        and all(item["status"] in ("ok", "configured") for item in databases.values())
        and not stale_paths
        and not config_errors
    )
    return {
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "workspace_root": str(root),
        "recommended_folder_name": "gtm",
        "actual_folder_name": root.name,
        "missing_core_files": missing,
        "dependencies": dependencies,
        "databases": databases,
        "accounts": {
            "status": account_status,
            "stale_local_paths": stale_paths,
            "config_errors": config_errors,
        },
        "ready_for_root_check": ready,
        "ready_for_outreach": False,
        "limits": [
            "No credentials, live accounts, browser adapters, evidence completeness or backup provenance verified.",
            "Missing private data is a restore checkpoint, never permission to initialize or seed production records.",
            "Any imported operator binding needs intended-owner confirmation and live verification on this machine.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = inspect_checkout(ROOT)
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
    print(payload, end="")
    return 0 if report["ready_for_root_check"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
