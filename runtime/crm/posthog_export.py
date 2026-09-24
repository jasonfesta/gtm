"""Read-only LinkedIn receipt export rehearsal. No network or ledger mutations."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from crm.cli import ROOT


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def prepare(db, account_id, day, *, include_current=False):
    """Export only exact receipt/outbox/message matches for one complete UTC day."""
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), timezone.utc)
    end = start + timedelta(days=1)
    if end > datetime.now(timezone.utc) and not (
        include_current and start.date() == datetime.now(timezone.utc).date()
    ):
        raise ValueError("a complete UTC day is required")
    events, rejected = [], 0
    with closing(sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        if not conn.execute("SELECT 1 FROM accounts WHERE account_id=?", (account_id,)).fetchone():
            raise ValueError("account not found in source ledger")
        rows = conn.execute(
            """
            SELECT r.*, m.direction, m.source, m.message_kind, m.platform_message_id,
                   m.account_id AS message_account, m.action_id AS message_action,
                   m.occurred_at AS message_time,
                   o.event_id, o.source_event_id, o.event_name, o.occurred_at AS event_time,
                   o.payload_json, o.state
            FROM confirmed_send_receipts r
            JOIN messages m ON m.message_id=r.message_id
            LEFT JOIN analytics_outbox o ON o.receipt_id=r.receipt_id
            WHERE r.account_id=?
        """,
            (account_id,),
        ).fetchall()
        expected_key = hashlib.sha256(f"linkedin-account|{account_id}".encode()).hexdigest()
        for row in rows:
            occurred = timestamp(row["message_time"])
            if not start <= occurred < end:
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
                event_id = str(UUID(row["event_id"]))
                source_id = str(UUID(row["receipt_id"]))
                formats = {
                    "casual_comment": ("reply", "public_comment", "casual_engagement"),
                    "lead_comment": ("reply", "public_comment", "gtm_outreach"),
                    "hiring_comment": ("reply", "public_comment", "gtm_outreach"),
                    "comment_reply": ("reply", "public_comment", "gtm_conversation"),
                    "dm": ("dm", "private_dm", "gtm_outreach"),
                    "dm_reply": ("reply", "private_dm", "gtm_conversation"),
                }
                entry, surface, program = formats[row["message_kind"]]
                valid = (
                    row["direction"] == "outbound"
                    and row["source"] == "live"
                    and row["platform_reference"] == row["platform_message_id"]
                    and bool(row["platform_reference"])
                    and bool(row["confirmation_method"])
                    and row["message_account"] == account_id
                    and row["message_action"] == row["action_id"]
                    and row["state"] != "discarded"
                    and row["event_name"] == "gtm.message_sent"
                    and row["source_event_id"] == row["receipt_id"]
                    and timestamp(row["event_time"]) == occurred
                    and payload.get("event_id") == row["event_id"]
                    and payload.get("source_event_id") == row["receipt_id"]
                    and payload.get("account_key") == expected_key
                    and payload.get("platform") == "linkedin"
                    and payload.get("entry_type") == entry
                    and payload.get("reply_surface") == surface
                    and payload.get("program") == program
                )
                if not valid:
                    raise ValueError("receipt mismatch")
                # Reconstruct an allowlist. Never export policy_scope, bodies, contacts or URLs.
                events.append(
                    {
                        "event": "gtm.message_sent",
                        "uuid": event_id,
                        "timestamp": occurred.isoformat(),
                        "properties": {
                            "distinct_id": "gtm-operator-" + expected_key,
                            "$process_person_profile": False,
                            "schema_version": 1,
                            "event_id": event_id,
                            "source_event_id": source_id,
                            "platform": "linkedin",
                            "account_key": expected_key,
                            "entry_type": entry,
                            "reply_surface": surface,
                            "program": program,
                        },
                    }
                )
            except (ValueError, TypeError, KeyError, AttributeError):
                rejected += 1
    events.sort(key=lambda event: event["uuid"])
    return {
        "day_utc": day,
        "platform": "linkedin",
        "events": events,
        "confirmed_receipts_in_window": len(events) + rejected,
        "rejected_receipts": rejected,
        "prepared_events": len(events),
        "delivery_status": "not_sent",
        "ingestion_status": "not_checked",
        "limitations": [
            "Local ledger confirmation is not a fresh platform verification.",
            "Operator identities are not recipient or product-user identities.",
            "Campaign, tags and copy attribution are unavailable in these source receipts.",
            "Gmail, X, inbound replies, clicks and product/calendar joins are not implemented here.",
        ],
    }


def reconcile(prepared, observed_ids):
    """Compare independently retrieved event IDs; duplicate ingestion remains visible."""
    expected = {e["uuid"] for e in prepared["events"]}
    observed = set(observed_ids)
    return {
        "missing": sorted(expected - observed),
        "unexpected": sorted(observed - expected),
        "duplicate_observations": len(observed_ids) - len(observed),
        "matched": len(expected & observed),
        "complete": bool(expected)
        and expected == observed
        and len(observed_ids) == len(observed)
        and not prepared["rejected_receipts"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "linkedin/data/linkedin.sqlite3")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.db, args.account_id, args.day)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "events"}))


if __name__ == "__main__":
    main()
