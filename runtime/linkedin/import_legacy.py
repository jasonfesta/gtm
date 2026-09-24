#!/usr/bin/env python3
"""Idempotently reconcile the existing local LinkedIn reply and lead logs."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import store

DEFAULT_ROOT = Path("/Users/jasonfesta/.cursor/Daily Agents/linkedin_reply_bot")
NS = uuid.UUID("978dad67-3a57-430e-83a5-92cae71dfcca")


def stable(kind: str, value: str) -> str:
    return str(uuid.uuid5(NS, f"{kind}|{value}"))


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") + "/", "", "")
    )


def ensure_account(db, config: dict, now: str) -> None:
    identity = config["identity"]
    db.execute(
        """INSERT INTO accounts(account_id, public_identifier, profile_url, display_name, timezone,
                   status, first_detected_at, last_detected_at)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET public_identifier=excluded.public_identifier,
             profile_url=excluded.profile_url, display_name=excluded.display_name,
             timezone=excluded.timezone, last_detected_at=excluded.last_detected_at""",
        (
            config["account_id"],
            identity["linkedin_slug"],
            identity["profile_url"],
            identity["display_name"],
            config["timezone"],
            now,
            now,
        ),
    )
    db.execute(
        """INSERT OR IGNORE INTO account_policies(
               account_id, revision, mode, policy_json, reviewed_by, created_at)
           VALUES (?, 1, ?, ?, 'workspace owner', ?)""",
        (
            config["account_id"],
            config.get("mode", "preview"),
            json.dumps(config, sort_keys=True),
            now,
        ),
    )


def ensure_person(db, *, name: str, profile_url: str | None, now: str) -> tuple[str, str | None]:
    normalized = " ".join(name.lower().split())
    key = normalize_url(profile_url) if profile_url else normalized
    person_id = stable("person", key)
    status = "supported" if profile_url else "candidate"
    db.execute(
        """INSERT OR IGNORE INTO people(person_id, canonical_name, normalized_name, identity_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (person_id, name.strip(), normalized, status, now, now),
    )
    profile_id = None
    if profile_url:
        normalized_url = normalize_url(profile_url)
        slug = normalized_url.split("/in/", 1)[1].strip("/") if "/in/" in normalized_url else None
        profile_id = stable("profile", normalized_url)
        db.execute(
            """INSERT OR IGNORE INTO profiles(
                   profile_id, person_id, public_identifier, profile_url, normalized_profile_url,
                   display_name, identity_method, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, 'official_link', ?, ?)""",
            (profile_id, person_id, slug, profile_url, normalized_url, name.strip(), now, now),
        )
    return person_id, profile_id


def import_file(db, *, path: Path, config: dict) -> dict[str, int | str]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    scope = f"legacy-linkedin:{path.name}"
    prior = db.execute(
        "SELECT import_id, counts_json FROM import_sources WHERE source_sha256 = ? AND source_scope = ?",
        (digest, scope),
    ).fetchone()
    if prior:
        return {
            "source": path.name,
            "status": "already_imported",
            **json.loads(prior["counts_json"]),
        }
    now = datetime.now(timezone.utc).isoformat()
    import_id = stable("import", f"{digest}|{scope}")
    data = json.loads(raw)
    rows = data.get("replied", data.get("people", []))
    counts = {"rows": 0, "comments": 0, "reported_dms": 0}
    for row in rows:
        name = (row.get("name") or row.get("author") or "unknown").strip()
        profile_url = row.get("linkedin")
        person_id, profile_id = ensure_person(db, name=name, profile_url=profile_url, now=now)
        timestamp = row.get("ts") or now
        comment = (row.get("comment") or "").strip()
        post_key = (
            row.get("comment_url") or row.get("sig") or f"legacy:{path.name}:{counts['rows']}"
        )
        post_id = stable("post", post_key)
        db.execute(
            """INSERT OR IGNORE INTO posts(
                   post_id, author_profile_id, canonical_url, post_signature, body, body_sha256,
                   observed_at, post_kind, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'unknown', ?)""",
            (
                post_id,
                profile_id,
                row.get("comment_url"),
                post_key,
                None,
                None,
                timestamp,
                f"legacy source {path.name}",
            ),
        )
        if comment:
            kind = "hiring_comment" if row.get("bucket") == "hiring" else "casual_comment"
            message_id = stable(
                "message", f"{config['account_id']}|{path.name}|{post_key}|{comment}|{timestamp}"
            )
            db.execute(
                """INSERT OR IGNORE INTO messages(
                       message_id, account_id, person_id, post_id, direction, message_kind, body,
                       occurred_at, observed_at, source, metadata_json)
                   VALUES (?, ?, ?, ?, 'outbound', ?, ?, ?, ?, 'import', ?)""",
                (
                    message_id,
                    config["account_id"],
                    person_id,
                    post_id,
                    kind,
                    comment,
                    timestamp,
                    now,
                    json.dumps(
                        {"legacy_source": path.name, "via": row.get("via"), "reported_only": True},
                        sort_keys=True,
                    ),
                ),
            )
            db.execute(
                """INSERT OR IGNORE INTO message_status_events(
                       status_event_id, message_id, status, occurred_at, detail, source)
                   VALUES (?, ?, 'reported_sent', ?, 'legacy local log; publication not independently verified', 'import')""",
                (stable("status", message_id), message_id, timestamp),
            )
            counts["comments"] += 1
        if row.get("dm") is True:
            # The legacy file has no exact DM body. Preserve the fact as a reconciled action only.
            action_id = stable(
                "legacy-dm-action", f"{config['account_id']}|{person_id}|{timestamp}"
            )
            key = store.idempotency_key(
                account_id=config["account_id"],
                action_type="dm",
                target_key=person_id,
                policy_scope="legacy-reconciled",
            )
            db.execute(
                """INSERT OR IGNORE INTO action_intents(
                       action_id, idempotency_key, account_id, person_id, profile_id, action_type,
                       workflow, policy_scope, reserved_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, 'dm', 'lead', 'legacy-reconciled', ?, ?)""",
                (
                    action_id,
                    key,
                    config["account_id"],
                    person_id,
                    profile_id,
                    timestamp,
                    json.dumps(
                        {"legacy_source": path.name, "body_unavailable": True}, sort_keys=True
                    ),
                ),
            )
            db.execute(
                """INSERT OR IGNORE INTO action_events(event_id, action_id, state, occurred_at, metadata_json)
                   VALUES (?, ?, 'reconciled', ?, ?)""",
                (
                    stable("legacy-dm-event", action_id),
                    action_id,
                    timestamp,
                    json.dumps({"reported_sent": True, "body_unavailable": True}, sort_keys=True),
                ),
            )
            counts["reported_dms"] += 1
        counts["rows"] += 1
    db.execute(
        """INSERT INTO import_sources(
               import_id, account_id, source_kind, source_path, source_sha256, imported_at,
               source_scope, status, counts_json, notes)
           VALUES (?, ?, 'legacy_log', ?, ?, ?, ?, 'partial', ?, ?)""",
        (
            import_id,
            config["account_id"],
            str(path),
            digest,
            now,
            scope,
            json.dumps(counts, sort_keys=True),
            data.get("_note"),
        ),
    )
    db.execute(
        """INSERT INTO history_coverage(
               coverage_id, account_id, surface, coverage_status, import_id, reviewed_at, reviewed_by, notes)
           VALUES (?, ?, ?, 'partial', ?, ?, 'automated legacy reconciliation', ?)""",
        (
            stable("coverage", import_id),
            config["account_id"],
            "dm" if counts["reported_dms"] else "comments",
            import_id,
            now,
            f"Local legacy file {path.name}; absence does not prove no earlier activity.",
        ),
    )
    db.commit()
    return {"source": path.name, "status": "imported", **counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--legacy-root", default=str(DEFAULT_ROOT))
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    db_path = Path(config["database_path"])
    store.initialize(db_path)
    now = datetime.now(timezone.utc).isoformat()
    with store.connect(db_path) as db:
        ensure_account(db, config, now)
        results = [
            import_file(db, path=Path(args.legacy_root) / name, config=config)
            for name in ("replied.json", "leads.json")
        ]
    print(json.dumps({"ok": True, "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
