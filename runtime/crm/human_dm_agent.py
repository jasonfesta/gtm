"""Small handoff helpers for explicitly started Human DM passes.

The Codex task operates X through the browser. This module only normalizes
provider results for Ops Agents; it contains no scheduler or provider client.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat()


def _ordered(items):
    """Warm first, preserving the supplied order inside each group."""
    indexed = list(enumerate(items))
    indexed.sort(key=lambda pair: (pair[1].get("candidate_kind") != "warm", pair[0]))
    return [item for _, item in indexed]


class HumanDMLedger:
    """Durable cursor and completed-run state for explicitly invoked DM passes."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS streams (stream TEXT PRIMARY KEY, cursor TEXT NOT NULL)"
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS completed_runs (
                run_id TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL,
                handoff TEXT NOT NULL
            )"""
        )
        self.connection.commit()

    def cursor(self, stream, default=None):
        row = self.connection.execute(
            "SELECT cursor FROM streams WHERE stream=?", (stream,)
        ).fetchone()
        return json.loads(row[0]) if row else default

    def completed(self, run_id):
        row = self.connection.execute(
            "SELECT handoff FROM completed_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def commit(self, stream, cursor, handoff):
        """Atomically save the next cursor and immutable completed handoff."""
        run_id = handoff["run_id"]
        encoded_handoff = json.dumps(handoff, sort_keys=True, separators=(",", ":"))
        encoded_cursor = json.dumps(cursor, sort_keys=True, separators=(",", ":"))
        with self.connection:
            existing = self.connection.execute(
                "SELECT handoff FROM completed_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing and existing[0] != encoded_handoff:
                raise ValueError("completed Human DM run cannot be changed")
            self.connection.execute(
                "INSERT OR IGNORE INTO completed_runs(run_id,completed_at,handoff) VALUES(?,?,?)",
                (run_id, handoff["completed_at"], encoded_handoff),
            )
            self.connection.execute(
                """INSERT INTO streams(stream,cursor) VALUES(?,?)
                   ON CONFLICT(stream) DO UPDATE SET cursor=excluded.cursor""",
                (stream, encoded_cursor),
            )


def run_batch(batch, *, draft, send, completed_at=None):
    """Run one explicitly invoked batch.

    ``draft(candidate)`` returns the message body. ``send(candidate, body)``
    returns provider facts. A raised send exception is recorded as uncertain
    because the provider may have accepted the message.
    """
    run_id = batch["run_id"]
    records = []

    for candidate in _ordered(batch.get("items", [])):
        body = draft(candidate)
        attempted_at = _now()
        try:
            receipt = send(candidate, body) or {}
            # A callback returning nothing does not prove X accepted a message.
            provider_status = receipt.get("provider_status") or "uncertain"
            if provider_status == "confirmed" and not receipt.get("provider_message_id"):
                provider_status = "uncertain"
            error_code = receipt.get("error_code", "")
        except Exception as exc:  # noqa: BLE001 - interrupted sends are uncertain.
            receipt = {}
            provider_status = "uncertain"
            error_code = type(exc).__name__

        records.append(
            {
                "run_id": run_id,
                "candidate_id": candidate["candidate_id"],
                "idempotency_key": candidate["idempotency_key"],
                "target_human_id": candidate["target_human_id"],
                "candidate_kind": candidate["candidate_kind"],
                "channel": candidate["channel"],
                "sender_account": candidate["sender_account"],
                "conversation_id": receipt.get("conversation_id")
                or candidate.get("conversation_id", ""),
                "outbound_text": body,
                "attempted_at": receipt.get("attempted_at") or attempted_at,
                "provider_status": provider_status,
                "provider_message_id": receipt.get("provider_message_id", ""),
                "permalink": receipt.get("permalink", ""),
                "error_code": error_code,
                "crm_category": candidate.get("crm_category", ""),
            }
        )

    completed = completed_at or _now()
    handoff_id = batch.get("handoff_id") or "human-dm-" + run_id
    return {
        "schema_version": 1,
        "handoff_id": handoff_id,
        "source_agent": "human_dm_agent",
        "run_id": run_id,
        "completed_at": completed,
        "operations": [{"action": "event_record", "payload": record} for record in records],
        "posthog_notice": {
            "source_agent": "human_dm_agent",
            "run_id": run_id,
            "crm_handoff_id": handoff_id,
            "status": "crm_handoff_ready",
        },
    }


def inbound_record(outbound, inbound):
    """Normalize an inbound DM, including a new request with no prior outbound.

    Pass ``None`` for ``outbound`` when the person contacted us first. Exact
    provider IDs and text come from the reviewed X conversation, never a guess.
    """
    outbound = outbound or {}
    record = {
        "schema_version": 1,
        "source_agent": "human_dm_agent",
        "run_id": inbound.get("run_id") or outbound.get("run_id", ""),
        "target_human_id": inbound.get("target_human_id") or outbound.get("target_human_id", ""),
        "channel": inbound.get("channel") or outbound.get("channel", "x"),
        "sender_account": inbound.get("sender_account") or outbound.get("sender_account", ""),
        "sender_handle": inbound.get("sender_handle", ""),
        "conversation_id": inbound.get("conversation_id") or outbound.get("conversation_id", ""),
        "conversation_url": inbound.get("conversation_url", ""),
        "in_reply_to_provider_message_id": outbound.get("provider_message_id", ""),
        "inbound_message_id": inbound["inbound_message_id"],
        "inbound_text": inbound["inbound_text"],
        "permalink": inbound.get("permalink", ""),
        "observed_at": inbound["observed_at"],
        "crm_category": inbound.get("crm_category") or outbound.get("crm_category", ""),
    }
    if inbound.get("occurred_at"):
        record["occurred_at"] = inbound["occurred_at"]
    return record


def inbound_handoff(run_id, messages, *, handoff_id=None):
    """Package a manual pass's new inbound messages for the CRM owner."""
    return {
        "schema_version": 1,
        "handoff_id": handoff_id or "human-dm-inbound-" + run_id,
        "source_agent": "human_dm_agent",
        "run_id": run_id,
        "operations": [
            {
                "action": "event_record",
                "payload": inbound_record(None, {**message, "run_id": run_id}),
            }
            for message in messages
        ],
    }
