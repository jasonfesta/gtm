"""Durable reply/outbox prototype. Does not operate X or start a worker.

The caller supplies a CRM-checked plan and an authorized sender. An unresolved
send is never retried automatically. Only a provider-confirmed receipt can move
it to posted. CRM retries use the existing idempotent receipt writer.
"""

import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path

from crm.human_reply_capture import normalize_handle, record_confirmed, stamp, utc


class ReplyQueue:
    def __init__(self, path):
        self.path = Path(path)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reply_jobs (
                parent_id TEXT PRIMARY KEY, handle TEXT NOT NULL,
                item TEXT NOT NULL, state TEXT NOT NULL,
                token TEXT, receipt TEXT, posted_at TEXT,
                reply_url TEXT UNIQUE
            )""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, plan, *, at=None):
        """Accept a checked plan once; suppress pending and recent local sends."""
        cutoff = stamp(utc(at) - dt.timedelta(hours=72))
        added = []
        db = self.connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                for item in plan["items"]:
                    for key in ("id", "handle", "url", "reply", "person_id", "fingerprint", "lane"):
                        if not item.get(key):
                            raise ValueError("missing plan field: " + key)
                    handle = normalize_handle(item["handle"])
                    duplicate = db.execute(
                        "SELECT 1 FROM reply_jobs WHERE parent_id=? OR "
                        "(handle=? AND (state IN ('ready','sending') OR posted_at>?))",
                        (str(item["id"]), handle, cutoff),
                    ).fetchone()
                    if duplicate:
                        continue
                    db.execute(
                        "INSERT INTO reply_jobs(parent_id,handle,item,state) VALUES (?,?,?,'ready')",
                        (str(item["id"]), handle, json.dumps(item)),
                    )
                    added.append(str(item["id"]))
        finally:
            db.close()
        return added

    def claim(self):
        """Commit sending before touching X; concurrent claimers cannot duplicate."""
        db = self.connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM reply_jobs WHERE state='ready' ORDER BY rowid LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                token = str(uuid.uuid4())
                db.execute(
                    "UPDATE reply_jobs SET state='sending',token=? WHERE parent_id=?",
                    (token, row["parent_id"]),
                )
                return {"item": json.loads(row["item"]), "token": token}
        finally:
            db.close()

    def confirm(self, parent_id, token, receipt):
        """Also used to reconcile a sending job after a crash, without resending."""
        provider_id = str(receipt.get("provider_id", ""))
        if (
            str(receipt.get("id")) != str(parent_id)
            or receipt.get("status") != "confirmed"
            or not provider_id.isdigit()
            or receipt.get("account") != "jasonfesta"
            or receipt.get("account_profile_url") != "https://x.com/jasonfesta"
            or receipt.get("reply_url") != f"https://x.com/jasonfesta/status/{provider_id}"
            or not receipt.get("observed_at")
        ):
            raise ValueError("exact provider-confirmed receipt required")
        observed = stamp(utc(receipt["observed_at"]))
        db = self.connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM reply_jobs WHERE parent_id=? AND token=?",
                    (str(parent_id), token),
                ).fetchone()
                if row is None:
                    raise ValueError("unknown send claim")
                if row["state"] in ("posted", "logged"):
                    if json.loads(row["receipt"]) != receipt:
                        raise ValueError("conflicting receipt")
                    return
                if row["state"] != "sending":
                    raise ValueError("job is not sending")
                db.execute(
                    "UPDATE reply_jobs SET state='posted',receipt=?,posted_at=?,reply_url=? "
                    "WHERE parent_id=?",
                    (json.dumps(receipt), observed, receipt["reply_url"], str(parent_id)),
                )
        finally:
            db.close()

    def send_one(self, sender):
        job = self.claim()
        if job is None:
            return None
        # Exceptions deliberately leave a durable 'sending' job for reconciliation.
        receipt = sender(job["item"])
        self.confirm(job["item"]["id"], job["token"], receipt)
        return receipt

    def flush(self, *, connection_factory, after_write=None):
        """Drain saved receipts independently; a partial commit is safe to replay.

        This prototype uses the existing writer (one CRM connection per receipt).
        A single-transaction batch writer is an explicitly unimplemented speed step.
        """
        db = self.connect()
        try:
            rows = db.execute("SELECT * FROM reply_jobs WHERE state='posted'").fetchall()
            if not rows:
                return []
            result = record_confirmed(
                {"items": [json.loads(r["item"]) for r in rows]},
                [json.loads(r["receipt"]) for r in rows],
                connection_factory=connection_factory,
            )
            if after_write:
                after_write()  # Fault injection: CRM committed, local ack not saved.
            with db:
                db.executemany(
                    "UPDATE reply_jobs SET state='logged' WHERE parent_id=? AND state='posted'",
                    [(r["parent_id"],) for r in rows],
                )
            return result
        finally:
            db.close()

    def jobs(self):
        db = self.connect()
        try:
            return [dict(row) for row in db.execute("SELECT * FROM reply_jobs ORDER BY rowid")]
        finally:
            db.close()
