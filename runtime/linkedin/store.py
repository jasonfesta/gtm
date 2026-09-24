#!/usr/bin/env python3
"""Initialize and inspect the local LinkedIn ledger. This module never accesses LinkedIn."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCHEMA = ROOT / "sql" / "schema.sql"
DEFAULT_DB = ROOT / "data" / "linkedin.sqlite3"


def database_path(value: str | None = None) -> Path:
    return Path(value or os.environ.get("LINKEDIN_DB_PATH", DEFAULT_DB)).expanduser().resolve()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(path: Path) -> None:
    with connect(path) as db:
        db.executescript(SCHEMA.read_text(encoding="utf-8"))


def counts(path: Path) -> dict[str, object]:
    tables = (
        "accounts",
        "browser_profiles",
        "account_policies",
        "people",
        "profiles",
        "posts",
        "draft_revisions",
        "conversations",
        "messages",
        "suppressions",
        "action_intents",
        "runs",
        "import_sources",
        "history_coverage",
        "confirmed_send_receipts",
        "analytics_outbox",
    )
    with connect(path) as db:
        version = db.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        return {
            "database": str(path),
            "schema_version": version[0] if version else None,
            "counts": {
                table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables
            },
        }


def verify(path: Path) -> dict[str, object]:
    with connect(path) as db:
        quick = db.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = [dict(row) for row in db.execute("PRAGMA foreign_key_check")]
        triggers = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN "
                "('messages_no_update', 'messages_no_delete', 'draft_revisions_no_update', 'draft_revisions_no_delete')"
            )
        }
    expected = {
        "messages_no_update",
        "messages_no_delete",
        "draft_revisions_no_update",
        "draft_revisions_no_delete",
    }
    return {
        "database": str(path),
        "quick_check": quick,
        "foreign_key_errors": foreign_keys,
        "append_only_triggers": sorted(triggers),
        "ok": quick == "ok" and not foreign_keys and triggers == expected,
    }


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def eligibility(
    db: sqlite3.Connection,
    *,
    account_id: str,
    person_id: str,
    action_type: str,
    workflow: str,
    post_id: str | None = None,
    in_reply_to_message_id: str | None = None,
    cooldown_hours: int | None = 72,
    now: datetime | None = None,
) -> dict[str, object]:
    """Return the single preflight decision future interactive and scheduled senders share."""
    now = now or datetime.now(timezone.utc)
    person = db.execute(
        "SELECT identity_status FROM people WHERE person_id = ?", (person_id,)
    ).fetchone()
    if not person:
        return {"allowed": False, "reason": "unknown_person"}
    if person[0] not in ("supported", "verified"):
        return {"allowed": False, "reason": "identity_needs_review"}

    suppression = db.execute(
        """SELECT suppression_id FROM suppressions
           WHERE is_active = 1
             AND (account_id IS NULL OR account_id = ?)
             AND person_id = ?
             AND (action_type IS NULL OR action_type = ?)
             AND (expires_at IS NULL OR expires_at > ?)
           LIMIT 1""",
        (account_id, person_id, action_type, now.isoformat()),
    ).fetchone()
    if suppression:
        return {"allowed": False, "reason": "suppressed", "suppression_id": suppression[0]}

    # runtime/Rules.md replaces the old one-comment lifetime policy. This local
    # ledger check complements (but cannot replace) cross-platform reconciliation.
    import sys

    crm_root = str(ROOT.parent)
    if crm_root not in sys.path:
        sys.path.insert(0, crm_root)
    from crm.outreach_rules import assess

    history = [
        dict(
            event_id=r["message_id"],
            channel="linkedin",
            direction=r["direction"],
            outcome="replied" if r["direction"] == "inbound" else "sent",
            occurred_at=r["occurred_at"],
            external_reference=r["platform_message_id"],
            in_reply_to_message_id=r["in_reply_to_message_id"],
        )
        for r in db.execute("SELECT * FROM messages WHERE person_id=?", (person_id,))
    ]
    pending = db.execute(
        "SELECT action_id FROM v_latest_action_state WHERE person_id=? "
        "AND state IN ('reserved','attempted','uncertain') LIMIT 1",
        (person_id,),
    ).fetchone()
    if pending:
        return {"allowed": False, "reason": "author_action_pending", "action_id": pending[0]}
    cadence = assess(
        "linkedin",
        history,
        now=now,
        reply_to=in_reply_to_message_id if action_type == "reply" else None,
    )
    if cadence["blockers"]:
        return {"allowed": False, "reason": cadence["blockers"][0]}

    if action_type == "comment":
        if not post_id:
            return {"allowed": False, "reason": "post_required"}
        if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone():
            return {"allowed": False, "reason": "unknown_post"}
        prior_post = db.execute(
            """SELECT message_id FROM messages
               WHERE account_id = ? AND post_id = ? AND direction = 'outbound'
                 AND message_kind IN ('casual_comment', 'lead_comment', 'hiring_comment')
               LIMIT 1""",
            (account_id, post_id),
        ).fetchone()
        if prior_post:
            return {
                "allowed": False,
                "reason": "post_already_commented",
                "message_id": prior_post[0],
            }
        active_person_action = db.execute(
            """SELECT action_id FROM v_latest_action_state
               WHERE account_id = ? AND person_id = ? AND action_type = 'comment'
                 AND state IN ('reserved', 'attempted', 'uncertain')
               LIMIT 1""",
            (account_id, person_id),
        ).fetchone()
        if active_person_action:
            return {
                "allowed": False,
                "reason": "author_action_pending",
                "action_id": active_person_action[0],
            }

        if workflow not in {"casual", "lead", "hiring"}:
            return {"allowed": False, "reason": "unsupported_comment_workflow"}

    elif action_type == "dm":
        historical_action = db.execute(
            """SELECT ai.action_id FROM action_intents ai
               JOIN v_latest_action_state latest ON latest.action_id = ai.action_id
               WHERE ai.account_id = ? AND ai.person_id = ? AND ai.action_type = 'dm'
                 AND latest.state IN ('sent', 'reconciled', 'uncertain')
               LIMIT 1""",
            (account_id, person_id),
        ).fetchone()
        if historical_action:
            return {
                "allowed": False,
                "reason": "existing_private_history_needs_review",
                "action_id": historical_action[0],
            }
        coverage = db.execute(
            """SELECT coverage_status FROM history_coverage
               WHERE account_id = ? AND (person_id = ? OR person_id IS NULL)
                 AND surface IN ('dm', 'all')
               ORDER BY CASE coverage_status WHEN 'complete' THEN 2 WHEN 'partial' THEN 1 ELSE 0 END DESC
               LIMIT 1""",
            (account_id, person_id),
        ).fetchone()
        if not coverage or coverage[0] != "complete":
            return {"allowed": False, "reason": "private_history_incomplete"}

    elif action_type == "reply":
        if not in_reply_to_message_id:
            return {"allowed": False, "reason": "inbound_message_required"}
        inbound = db.execute(
            """SELECT message_id FROM messages
               WHERE message_id = ? AND account_id = ? AND person_id = ? AND direction = 'inbound'""",
            (in_reply_to_message_id, account_id, person_id),
        ).fetchone()
        if not inbound:
            return {"allowed": False, "reason": "inbound_message_not_found"}
        prior = db.execute(
            "SELECT message_id FROM messages WHERE in_reply_to_message_id = ? AND direction = 'outbound' LIMIT 1",
            (in_reply_to_message_id,),
        ).fetchone()
        if prior:
            return {
                "allowed": False,
                "reason": "inbound_message_already_replied",
                "message_id": prior[0],
            }
    else:
        return {"allowed": False, "reason": "unsupported_action_type"}

    return {"allowed": True, "reason": "eligible"}


def idempotency_key(
    *, account_id: str, action_type: str, target_key: str, policy_scope: str
) -> str:
    raw = f"{account_id}|linkedin|{action_type}|{target_key}|{policy_scope}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def public_reply_count_for_day(
    db: sqlite3.Connection,
    *,
    account_id: str,
    timezone_name: str = "America/New_York",
    now: datetime | None = None,
) -> int:
    """Count confirmed public LinkedIn replies in the account's local calendar day."""
    zone = ZoneInfo(timezone_name)
    local_now = (now or datetime.now(timezone.utc)).astimezone(zone)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return db.execute(
        """SELECT COUNT(*)
           FROM confirmed_send_receipts csr
           JOIN messages m ON m.message_id = csr.message_id
           WHERE csr.account_id = ?
             AND m.message_kind IN ('casual_comment', 'lead_comment', 'hiring_comment', 'comment_reply')
             AND csr.confirmed_at >= ? AND csr.confirmed_at < ?""",
        (
            account_id,
            start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat(),
        ),
    ).fetchone()[0]


def reserve_action(
    db: sqlite3.Connection,
    *,
    account_id: str,
    person_id: str,
    action_type: str,
    workflow: str,
    target_key: str,
    policy_scope: str,
    post_id: str | None = None,
    in_reply_to_message_id: str | None = None,
    run_id: str | None = None,
    profile_id: str | None = None,
    conversation_id: str | None = None,
    draft_id: str | None = None,
    cooldown_hours: int = 72,
    daily_cap: int | None = None,
    timezone_name: str = "America/New_York",
    now: datetime | None = None,
) -> dict[str, object]:
    """Atomically run preflight and reserve a unique external action."""
    now = now or datetime.now(timezone.utc)
    action_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    key = idempotency_key(
        account_id=account_id,
        action_type=action_type,
        target_key=target_key,
        policy_scope=policy_scope,
    )
    try:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute(
            "SELECT action_id FROM action_intents WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing:
            db.rollback()
            return {
                "allowed": False,
                "reason": "action_already_reserved",
                "action_id": existing[0],
                "idempotency_key": key,
            }
        decision = eligibility(
            db,
            account_id=account_id,
            person_id=person_id,
            action_type=action_type,
            workflow=workflow,
            post_id=post_id,
            in_reply_to_message_id=in_reply_to_message_id,
            cooldown_hours=cooldown_hours,
            now=now,
        )
        if not decision["allowed"]:
            db.rollback()
            return decision
        if action_type == "comment" and daily_cap is not None:
            confirmed = public_reply_count_for_day(
                db, account_id=account_id, timezone_name=timezone_name, now=now
            )
            pending = db.execute(
                """SELECT COUNT(*) FROM v_latest_action_state
                   WHERE account_id = ? AND action_type = 'comment' AND state IN ('reserved', 'attempted')""",
                (account_id,),
            ).fetchone()[0]
            if confirmed + pending >= daily_cap:
                db.rollback()
                return {
                    "allowed": False,
                    "reason": "daily_public_reply_cap",
                    "count": confirmed + pending,
                }
        try:
            db.execute(
                """INSERT INTO action_intents(
                    action_id, idempotency_key, run_id, account_id, person_id, profile_id,
                    post_id, conversation_id, draft_id, action_type, workflow, policy_scope,
                    reserved_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}')""",
                (
                    action_id,
                    key,
                    run_id,
                    account_id,
                    person_id,
                    profile_id,
                    post_id,
                    conversation_id,
                    draft_id,
                    action_type,
                    workflow,
                    policy_scope,
                    now.isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO action_events(event_id, action_id, state, occurred_at) VALUES (?, ?, 'reserved', ?)",
                (event_id, action_id, now.isoformat()),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            if db.execute(
                "SELECT 1 FROM action_intents WHERE idempotency_key = ?", (key,)
            ).fetchone():
                return {
                    "allowed": False,
                    "reason": "action_already_reserved",
                    "idempotency_key": key,
                }
            raise
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    return {"allowed": True, "reason": "reserved", "action_id": action_id, "idempotency_key": key}


def record_action_result(
    db: sqlite3.Connection,
    *,
    action_id: str,
    state: str,
    platform_reference: str | None = None,
    error_message: str | None = None,
    metadata: dict[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> str:
    """Append a non-confirmed browser result. Confirmed sends use record_confirmed_send."""
    if state not in {"attempted", "failed", "uncertain", "skipped", "cancelled", "reconciled"}:
        raise ValueError("unsupported action result")
    event_id = str(uuid.uuid4())
    when = (occurred_at or datetime.now(timezone.utc)).isoformat()
    db.execute(
        """INSERT INTO action_events(
               event_id, action_id, state, occurred_at, platform_reference, error_message, metadata_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            action_id,
            state,
            when,
            platform_reference,
            error_message,
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    db.commit()
    return event_id


def record_confirmed_send(
    db: sqlite3.Connection,
    *,
    action_id: str,
    body: str,
    message_kind: str,
    platform_reference: str,
    confirmation_method: str,
    occurred_at: datetime | None = None,
    conversation_id: str | None = None,
    in_reply_to_message_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> dict[str, str]:
    """Atomically persist the exact sent text, confirmation receipt, and privacy-safe outbox row."""
    when = (occurred_at or datetime.now(timezone.utc)).isoformat()
    action = db.execute(
        """SELECT action_id, account_id, person_id, post_id, run_id, workflow, action_type,
                  idempotency_key, policy_scope
           FROM action_intents WHERE action_id = ?""",
        (action_id,),
    ).fetchone()
    if not action:
        raise ValueError("unknown action")
    mapping = {
        "casual_comment": ("reply", "public_comment", "casual_engagement"),
        "lead_comment": ("reply", "public_comment", "gtm_outreach"),
        "hiring_comment": ("reply", "public_comment", "gtm_outreach"),
        "comment_reply": ("reply", "public_comment", "gtm_conversation"),
        "dm": ("dm", "private_dm", "gtm_outreach"),
        "dm_reply": ("reply", "private_dm", "gtm_conversation"),
    }
    if message_kind not in mapping or not body.strip() or not platform_reference:
        raise ValueError("invalid confirmed send")
    message_id = str(uuid.uuid4())
    receipt_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    status_id = str(uuid.uuid4())
    source_event_id = receipt_id
    entry_type, reply_surface, program = mapping[message_kind]
    account_key = hashlib.sha256(f"linkedin-account|{action['account_id']}".encode()).hexdigest()
    payload = {
        "schema_version": 1,
        "event_id": event_id,
        "source_event_id": source_event_id,
        "occurred_at": when,
        "platform": "linkedin",
        "account_key": account_key,
        "entry_type": entry_type,
        "reply_surface": reply_surface,
        "program": program,
        "workflow": action["workflow"],
        "policy_scope": action["policy_scope"],
    }
    try:
        db.execute("BEGIN IMMEDIATE")
        if db.execute(
            "SELECT 1 FROM confirmed_send_receipts WHERE action_id = ?", (action_id,)
        ).fetchone():
            db.rollback()
            raise ValueError("confirmed send already recorded")
        db.execute(
            """INSERT INTO action_events(event_id, action_id, state, occurred_at, platform_reference, metadata_json)
               VALUES (?, ?, 'sent', ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                action_id,
                when,
                platform_reference,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        db.execute(
            """INSERT INTO messages(
                   message_id, account_id, person_id, conversation_id, post_id, action_id,
                   platform_message_id, idempotency_key, direction, message_kind, body,
                   occurred_at, observed_at, in_reply_to_message_id, source, metadata_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'outbound', ?, ?, ?, ?, ?, 'live', ?)""",
            (
                message_id,
                action["account_id"],
                action["person_id"],
                conversation_id,
                action["post_id"],
                action_id,
                platform_reference,
                action["idempotency_key"],
                message_kind,
                body,
                when,
                when,
                in_reply_to_message_id,
                json.dumps(metadata or {}, sort_keys=True),
            ),
        )
        db.execute(
            "INSERT INTO message_status_events(status_event_id, message_id, status, occurred_at, source) VALUES (?, ?, 'sent', ?, 'live')",
            (status_id, message_id, when),
        )
        db.execute(
            """INSERT INTO confirmed_send_receipts(
                   receipt_id, message_id, action_id, account_id, platform_reference,
                   confirmation_method, confirmed_at, source_run_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                receipt_id,
                message_id,
                action_id,
                action["account_id"],
                platform_reference,
                confirmation_method,
                when,
                action["run_id"],
            ),
        )
        db.execute(
            """INSERT INTO analytics_outbox(
                   event_id, receipt_id, source_event_id, occurred_at, payload_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                receipt_id,
                source_event_id,
                when,
                json.dumps(payload, sort_keys=True),
                when,
            ),
        )
        db.commit()
    except Exception:
        if db.in_transaction:
            db.rollback()
        raise
    return {"message_id": message_id, "receipt_id": receipt_id, "outbox_event_id": event_id}


def _safe_segment(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not cleaned:
        raise ValueError("empty or unsafe path segment")
    return cleaned


def _fenced_text(value: str) -> str:
    longest = max((len(run) for run in re.findall(r"`+", value)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{value}\n{fence}"


def export_history(
    path: Path,
    *,
    account_id: str,
    person_id: str,
    output: Path | None = None,
) -> Path:
    with connect(path) as db:
        account = db.execute(
            "SELECT display_name, public_identifier, profile_url FROM accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        person = db.execute(
            "SELECT canonical_name, identity_status FROM people WHERE person_id = ?",
            (person_id,),
        ).fetchone()
        if not account or not person:
            raise ValueError("unknown account or person")
        profiles = list(
            db.execute(
                "SELECT profile_url, identity_method FROM profiles WHERE person_id = ? ORDER BY first_seen_at",
                (person_id,),
            )
        )
        coverage = list(
            db.execute(
                """SELECT surface, coverage_status, covered_from, covered_through, source_scope
                   FROM history_coverage hc
                   LEFT JOIN import_sources src ON src.import_id = hc.import_id
                   WHERE hc.account_id = ? AND (hc.person_id = ? OR hc.person_id IS NULL)
                   ORDER BY surface, covered_through""",
                (account_id, person_id),
            )
        )
        messages = list(
            db.execute(
                """SELECT m.message_id, m.direction, m.message_kind, m.body, m.occurred_at,
                          m.platform_message_id, m.in_reply_to_message_id, m.source,
                          (SELECT mse.status FROM message_status_events mse
                           WHERE mse.message_id = m.message_id
                           ORDER BY mse.occurred_at DESC, mse.status_event_id DESC LIMIT 1) AS latest_status
                   FROM messages m
                   WHERE m.account_id = ? AND m.person_id = ?
                   ORDER BY m.occurred_at, m.message_id""",
                (account_id, person_id),
            )
        )

    lines = [
        "# LinkedIn conversation history",
        "",
        f"account: {account['display_name']} (`{account_id}`)",
        f"account identifier: {account['public_identifier'] or 'unknown'}",
        f"person: {person['canonical_name']} (`{person_id}`)",
        f"identity status: {person['identity_status']}",
        "",
        "## Profiles",
        "",
    ]
    if profiles:
        lines.extend(f"- {row['profile_url']} — {row['identity_method']}" for row in profiles)
    else:
        lines.append("- none recorded")
    lines.extend(["", "## Coverage", ""])
    if coverage:
        for row in coverage:
            bounds = (
                f"{row['covered_from'] or 'unknown'} through {row['covered_through'] or 'unknown'}"
            )
            lines.append(
                f"- {row['surface']}: {row['coverage_status']} ({bounds}); source: {row['source_scope'] or 'manual'}"
            )
    else:
        lines.append("- unknown; absence from this export does not mean no earlier contact")
    lines.extend(["", "## Messages", ""])
    if not messages:
        lines.append("No messages recorded for this account and person.")
    for row in messages:
        lines.extend(
            [
                f"### {row['occurred_at']} — {row['direction']} {row['message_kind']}",
                "",
                f"message id: `{row['message_id']}`",
                f"platform id: `{row['platform_message_id'] or 'unknown'}`",
                f"reply to: `{row['in_reply_to_message_id'] or 'none'}`",
                f"source: {row['source']}; latest status: {row['latest_status'] or 'unknown'}",
                "",
                _fenced_text(row["body"]),
                "",
            ]
        )

    target = (
        output or ROOT / "history" / _safe_segment(account_id) / f"{_safe_segment(person_id)}.md"
    )
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Override the local SQLite path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "verify"):
        subparsers.add_parser(name)
    history = subparsers.add_parser("export-history")
    history.add_argument("--account", required=True)
    history.add_argument("--person", required=True)
    history.add_argument("--output")
    args = parser.parse_args()
    path = database_path(args.db)

    if args.command == "init":
        initialize(path)
        result = counts(path)
        result["initialized"] = True
    elif args.command == "status":
        result = counts(path)
    elif args.command == "verify":
        result = verify(path)
    else:
        target = export_history(
            path,
            account_id=args.account,
            person_id=args.person,
            output=Path(args.output) if args.output else None,
        )
        result = {"database": str(path), "history_export": str(target)}

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
