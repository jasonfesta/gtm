"""Confirmed local GTM activity, durable PostHog delivery and daily reconciliation.

This records observations, never sends messages. Private evidence stays in CRM.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from crm.cli import ROOT
from crm.database import private_connection
from crm.posthog_export import prepare, timestamp

PROJECT = 121185
DEFAULT_DB = ROOT / "data/activity.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS identities (
    local_key TEXT PRIMARY KEY, public_key TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS activity (
    receipt_id TEXT PRIMARY KEY, platform TEXT NOT NULL, account TEXT NOT NULL,
    provider_id TEXT NOT NULL, facts_json TEXT NOT NULL,
    evidence_path TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0, accepted_at TEXT, confirmed_at TEXT,
    last_error TEXT, UNIQUE(platform, account, provider_id)
);
CREATE TABLE IF NOT EXISTS snapshots (
    event_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending', last_error TEXT
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class Ledger:
    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        self.path.chmod(0o600)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return closing_connection(conn)

    @staticmethod
    def identity(conn, local_key):
        conn.execute(
            "INSERT OR IGNORE INTO identities VALUES (?,?)", (local_key, str(uuid.uuid4()))
        )
        return conn.execute(
            "SELECT public_key FROM identities WHERE local_key=?", (local_key,)
        ).fetchone()[0]

    def record(self, facts, evidence_path):
        """Record a provider observation reviewed by the local operator, not an attempt.

        Gmail send adapters must additionally validate the approved preparation using
        validate_gmail. X requires a reread of the posted item by its provider ID.
        """
        evidence = Path(evidence_path).resolve()
        if not evidence.is_relative_to(ROOT.resolve()) or not evidence.is_file():
            raise ValueError("existing private evidence file inside CRM required")
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        required = ("platform", "account", "provider_id", "occurred_at", "kind", "reviewer")
        if any(not isinstance(facts.get(k), str) or not facts[k].strip() for k in required):
            raise ValueError("complete confirmed provider observation required")
        platform, kind = facts["platform"], facts["kind"]
        if platform not in ("gmail", "x") or kind not in (
            "email",
            "dm",
            "public_reply",
            "inbound_reply",
        ):
            raise ValueError(
                "use the LinkedIn ledger for LinkedIn; Smartlead uses its campaign feed"
            )
        if (platform == "gmail" and kind not in ("email", "inbound_reply")) or (
            platform == "x" and kind == "email"
        ):
            raise ValueError("invalid channel format")
        if facts.get("confirmation") != "provider_readback" or facts.get("status") != "confirmed":
            raise ValueError("drafts, submissions and uncertain outcomes are not confirmations")
        when = timestamp(facts["occurred_at"])
        if when > datetime.now(timezone.utc):
            raise ValueError("future activity is not confirmed")
        if platform == "gmail":
            validate_gmail(facts)
        elif facts.get("author_account") != facts["account"] and kind != "inbound_reply":
            raise ValueError("X reread author must match the sending account")
        if (
            platform == "x"
            and kind != "inbound_reply"
            and (
                not isinstance(facts.get("body"), str)
                or not facts["body"].strip()
                or not isinstance(facts.get("target_reference"), str)
                or not facts["target_reference"].strip()
            )
        ):
            raise ValueError("X exact posted body and target reference required")
        facts = {**facts, "occurred_at": when.isoformat()}
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute(
                "SELECT * FROM activity WHERE platform=? AND account=? AND provider_id=?",
                (platform, facts["account"], facts["provider_id"]),
            ).fetchone()
            if prior:
                if prior["facts_json"] != encode(facts):
                    raise ValueError("conflicting receipt; original retained")
                return prior["receipt_id"]
            person_key = None
            if facts.get("person_id"):
                person_key = self.identity(conn, "recipient|" + facts["person_id"])
            parent = None
            if kind == "inbound_reply":
                parent = conn.execute(
                    "SELECT * FROM activity WHERE receipt_id=?", (facts.get("in_reply_to"),)
                ).fetchone()
                if not parent:
                    raise ValueError("reply requires a confirmed outbound receipt")
                sent = json.loads(parent["facts_json"])
                if (
                    sent["kind"] == "inbound_reply"
                    or sent["platform"] != platform
                    or sent["account"] != facts["account"]
                    or not facts.get("person_id")
                    or sent.get("person_id") != facts["person_id"]
                    or timestamp(sent["occurred_at"]) > when
                    or facts.get("in_reply_to_provider_id") != sent["provider_id"]
                ):
                    raise ValueError("reply must match the original outbound, account and person")
                if platform == "gmail" and facts.get("in_reply_to_rfc_message_id") != sent.get(
                    "rfc_message_id"
                ):
                    raise ValueError("Gmail reply must reference the outbound RFC Message-ID")
                if facts.get("reply_classification") not in ("human", "automated", "unknown"):
                    raise ValueError("explicit reply classification required")
            receipt_id, event_id = str(uuid.uuid4()), str(uuid.uuid4())
            account_key = self.identity(conn, platform + "|" + facts["account"])
            props = {
                "distinct_id": "gtm-operator-" + account_key,
                "$process_person_profile": False,
                "$geoip_disable": True,
                "schema_version": 2,
                "event_id": event_id,
                "source_event_id": receipt_id,
                "workspace": "gtm-dev",
                "dashboard_key": "gtm-dev",
                "platform": platform,
                "entry_type": kind,
                "account_key": account_key,
                "program": "gtm_outreach",
            }
            if person_key:
                props["recipient_key"] = person_key
            if parent:
                props.update(
                    in_reply_to_source_event_id=parent["receipt_id"],
                    reply_classification=facts["reply_classification"],
                )
            payload = {
                "uuid": event_id,
                "timestamp": when.isoformat(),
                "event": "gtm.reply_received" if parent else "gtm.message_sent",
                "properties": props,
            }
            conn.execute(
                """INSERT INTO activity(receipt_id,platform,account,provider_id,facts_json,
                   evidence_path,evidence_sha256,payload_json) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    receipt_id,
                    platform,
                    facts["account"],
                    facts["provider_id"],
                    encode(facts),
                    str(evidence),
                    digest,
                    encode(payload),
                ),
            )
            return receipt_id

    def import_linkedin(self, path):
        """Replay the existing validated source outbox, preserving its original event UUID."""
        path = Path(path)
        if not path.is_file():
            return {"imported": 0, "rejected": 0, "source": "unavailable"}
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            windows = {
                (account, timestamp(when).date().isoformat())
                for account, when in source.execute(
                    "SELECT account_id,confirmed_at FROM confirmed_send_receipts"
                )
            }
        imported, rejected = 0, 0
        for account, day in windows:
            prepared = prepare(path, account, day, include_current=True)
            rejected += prepared["rejected_receipts"]
            for payload in prepared["events"]:
                props = payload["properties"]
                props.update(workspace="gtm-dev", dashboard_key="gtm-dev")
                props["$geoip_disable"] = True
                props["entry_type"] = (
                    "public_reply" if props["reply_surface"] == "public_comment" else "dm"
                )
                with self.connect() as conn:
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO activity(receipt_id,platform,account,provider_id,
                           facts_json,evidence_path,evidence_sha256,payload_json)
                           VALUES(?,'linkedin',?,?,?,?,'source-ledger',?)""",
                        (
                            props["source_event_id"],
                            account,
                            props["source_event_id"],
                            encode({"source_event_id": props["source_event_id"]}),
                            str(path),
                            encode(payload),
                        ),
                    )
                    imported += cursor.rowcount
        return {"imported": imported, "rejected": rejected, "source": "available"}

    def import_x_dm_reconciliation(self, path):
        """Import a reviewed X inbox readback as one outreach receipt per conversation.

        The private file may retain every observed message ID, but the dashboard unit is
        a contacted conversation. The selected outbound ID is a real X message UUID, and
        any reply must carry its own real X message UUID and reference that outbound.
        """
        path = Path(path).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise ValueError("private X reconciliation inside CRM required")
        data = json.loads(path.read_text())
        if data.get("schema_version") != 1:
            raise ValueError("unsupported X DM reconciliation schema")
        account = data.get("account")
        if (
            not isinstance(account, str)
            or not account.strip()
            or data.get("account_profile_url") != f"https://x.com/{account}"
        ):
            raise ValueError("verified X account identity required")
        conversations = data.get("conversations")
        if not isinstance(conversations, list) or not conversations:
            raise ValueError("nonempty X conversation readback required")
        imported, replies, seen_conversations, seen_messages = 0, 0, set(), set()
        for conversation in conversations:
            required = (
                "conversation_id",
                "name",
                "handle",
                "profile_url",
                "conversation_url",
                "all_message_ids",
                "outbound",
            )
            if any(key not in conversation for key in required):
                raise ValueError("complete X conversation identity required")
            conversation_id = conversation["conversation_id"]
            handle = conversation["handle"]
            if (
                not isinstance(conversation_id, str)
                or not conversation_id.strip()
                or conversation_id in seen_conversations
                or conversation["profile_url"].casefold() != f"https://x.com/{handle}".casefold()
                or conversation["conversation_url"] != f"https://x.com/i/chat/{conversation_id}"
            ):
                raise ValueError("conflicting X conversation identity")
            seen_conversations.add(conversation_id)
            message_ids = conversation["all_message_ids"]
            if not isinstance(message_ids, list) or not message_ids:
                raise ValueError("observed X message IDs required")
            for message_id in message_ids:
                try:
                    uuid.UUID(message_id)
                except (TypeError, ValueError):
                    raise ValueError("native X message UUID required") from None
                if message_id in seen_messages:
                    raise ValueError("X message UUID reused across conversations")
                seen_messages.add(message_id)
            outbound = conversation["outbound"]
            if outbound.get("message_id") not in message_ids:
                raise ValueError("selected outbound must be an observed X message")
            parent = self.record(
                {
                    "platform": "x",
                    "account": account,
                    "provider_id": outbound["message_id"],
                    "occurred_at": outbound["occurred_at"],
                    "kind": "dm",
                    "reviewer": "local operator",
                    "confirmation": "provider_readback",
                    "status": "confirmed",
                    "author_account": account,
                    "person_id": "x:" + handle.casefold(),
                    "body": outbound["body"],
                    "target_reference": conversation["conversation_url"],
                },
                path,
            )
            imported += 1
            reply = conversation.get("reply")
            if reply:
                if reply.get("message_id") not in message_ids:
                    raise ValueError("reply must be an observed X message")
                self.record(
                    {
                        "platform": "x",
                        "account": account,
                        "provider_id": reply["message_id"],
                        "occurred_at": reply["occurred_at"],
                        "kind": "inbound_reply",
                        "reviewer": "local operator",
                        "confirmation": "provider_readback",
                        "status": "confirmed",
                        "author_account": handle,
                        "person_id": "x:" + handle.casefold(),
                        "body": reply["body"],
                        "target_reference": conversation["conversation_url"],
                        "in_reply_to": parent,
                        "in_reply_to_provider_id": outbound["message_id"],
                        "reply_classification": reply["classification"],
                    },
                    path,
                )
                replies += 1
        return {
            "conversations": imported,
            "replies": replies,
            "observed_outbound_messages": data.get("observed_outbound_message_count"),
            "source": str(path),
        }

    def sync(self, client):
        """Reconcile before retry. HTTP acceptance is never reported as verified ingestion."""
        client.verify_project()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM activity WHERE state!='confirmed' ORDER BY rowid LIMIT 500"
            ).fetchall()
        result = {"checked": len(rows), "confirmed": 0, "accepted": 0, "failed": 0}
        for row in rows:
            event = json.loads(row["payload_json"])
            try:
                if client.observed(event):
                    with self.connect() as conn:
                        conn.execute(
                            "UPDATE activity SET state='confirmed',confirmed_at=?,last_error=NULL WHERE receipt_id=?",
                            (now(), row["receipt_id"]),
                        )
                    result["confirmed"] += 1
                    continue
                client.capture(event)
                with self.connect() as conn:
                    conn.execute(
                        "UPDATE activity SET state='accepted',attempts=attempts+1,accepted_at=?,last_error=NULL WHERE receipt_id=?",
                        (now(), row["receipt_id"]),
                    )
                result["accepted"] += 1
            except Exception as exc:
                # Exceptions from HTTP libraries can contain credential-bearing URLs.
                with self.connect() as conn:
                    conn.execute(
                        "UPDATE activity SET attempts=attempts+1,last_error=? WHERE receipt_id=?",
                        (type(exc).__name__, row["receipt_id"]),
                    )
                result["failed"] += 1
        return result

    def status(self):
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT platform,state,count(*) AS receipts,max(confirmed_at) AS last_confirmed FROM activity GROUP BY platform,state"
                )
            ]

    def snapshot(self, client):
        """Publish actual ledger coverage, including empty counts, without fake sends."""
        from datetime import timedelta

        when = datetime.now(timezone.utc)
        today, yesterday = when.date(), (when - timedelta(days=1)).date()
        with self.connect() as conn:
            rows = conn.execute("SELECT payload_json,state FROM activity").fetchall()
            events = [json.loads(row["payload_json"]) for row in rows]
            pending = sum(row["state"] != "confirmed" for row in rows)
            for platform, kind in (
                ("gmail", "email"),
                ("x", "dm"),
                ("x", "public_reply"),
                ("linkedin", "dm"),
                ("linkedin", "public_reply"),
            ):
                sends = [
                    event
                    for event in events
                    if event["event"] == "gtm.message_sent"
                    and event["properties"]["platform"] == platform
                    and event["properties"]["entry_type"] == kind
                ]
                replies = [
                    event
                    for event in events
                    if event["event"] == "gtm.reply_received"
                    and event["properties"]["platform"] == platform
                ]
                human = [
                    event
                    for event in replies
                    if event["properties"]["reply_classification"] == "human"
                ]
                event_id = str(uuid.uuid4())
                props = {
                    "distinct_id": "gtm-local-ledger",
                    "$process_person_profile": False,
                    "$geoip_disable": True,
                    "workspace": "gtm-dev",
                    "dashboard_key": "gtm-dev",
                    "platform": platform,
                    "entry_type": kind,
                    "coverage": "confirmed local ledger only; no historical backfill",
                    "total": len(sends),
                    "outreach_total": sum(
                        e["properties"]["program"] == "gtm_outreach" for e in sends
                    ),
                    "conversation_total": sum(
                        e["properties"]["program"] == "gtm_conversation" for e in sends
                    ),
                    "casual_total": sum(
                        e["properties"]["program"] == "casual_engagement" for e in sends
                    ),
                    "yesterday": sum(timestamp(e["timestamp"]).date() == yesterday for e in sends),
                    "today": sum(timestamp(e["timestamp"]).date() == today for e in sends),
                    "reply_total": len(replies),
                    "reply_yesterday": sum(
                        timestamp(e["timestamp"]).date() == yesterday for e in replies
                    ),
                    "reply_unique": len({e["properties"]["recipient_key"] for e in replies}),
                    "human_reply_unique": len({e["properties"]["recipient_key"] for e in human}),
                    "pending_receipt_events": pending,
                    "day_utc": today.isoformat(),
                }
                payload = {
                    "event": "gtm.local_activity_snapshot",
                    "uuid": event_id,
                    "timestamp": when.isoformat(),
                    "properties": props,
                }
                conn.execute(
                    "INSERT INTO snapshots(event_id,payload_json) VALUES(?,?)",
                    (event_id, encode(payload)),
                )
        return self.deliver_snapshots(client)

    def deliver_snapshots(self, client):
        """Deliver already queued aggregate snapshots without creating new ones."""
        client.verify_project()
        with self.connect() as conn:
            queue = conn.execute(
                "SELECT * FROM snapshots WHERE state!='confirmed' ORDER BY rowid LIMIT 100"
            ).fetchall()
        result = {"accepted": 0, "confirmed": 0, "failed": 0}
        for row in queue:
            event = json.loads(row["payload_json"])
            try:
                state = "confirmed" if client.observed(event) else "accepted"
                if state == "accepted":
                    client.capture(event)
                with self.connect() as conn:
                    conn.execute(
                        "UPDATE snapshots SET state=?,last_error=NULL WHERE event_id=?",
                        (state, row["event_id"]),
                    )
                result[state] += 1
            except Exception as exc:
                with self.connect() as conn:
                    conn.execute(
                        "UPDATE snapshots SET last_error=? WHERE event_id=?",
                        (type(exc).__name__, row["event_id"]),
                    )
                result["failed"] += 1
        return result


class closing_connection:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *args):
        try:
            return self.conn.__exit__(*args)
        finally:
            self.conn.close()


def validate_gmail(facts, db=None):
    """Match a same-account Gmail API readback against the saved reviewed draft."""
    db = Path(db or ROOT / "data/crm.sqlite3")
    with closing(private_connection(db, readonly=True)) as conn:
        conn.row_factory = sqlite3.Row
        prep = conn.execute(
            """SELECT p.*,c.subject,c.body FROM email_preparations p JOIN copy_revisions c
               ON c.draft_id=p.draft_id AND c.revision=p.revision WHERE p.preparation_id=?""",
            (facts.get("preparation_id"),),
        ).fetchone()
    if not prep or prep["state"] != "gmail_draft" or prep["account"] != facts["account"]:
        raise ValueError("same-account saved Gmail preparation required")
    if not isinstance(facts.get("rfc_message_id"), str) or not facts["rfc_message_id"].strip():
        raise ValueError("Gmail RFC Message-ID required")
    if facts.get("thread_id") != prep["gmail_thread_id"] or "DRAFT" in facts.get("labels", []):
        raise ValueError("Gmail thread mismatch or still a draft")
    if not facts.get("person_id") or facts["person_id"] != prep["person_id"]:
        raise ValueError("Gmail preparation person mismatch")
    if facts["kind"] == "email":
        if (
            "SENT" not in facts.get("labels", [])
            or facts.get("from_address") != prep["account"]
            or facts.get("to_addresses") != [prep["exact_address"]]
            or facts.get("subject") != prep["subject"]
            or facts.get("body") != prep["body"]
            or timestamp(facts["occurred_at"]) < timestamp(prep["created_at"])
        ):
            raise ValueError("Gmail SENT label, exact reviewed copy, sender and recipient required")
    elif (
        "SENT" in facts.get("labels", [])
        or facts.get("from_address") != prep["exact_address"]
        or prep["account"] not in facts.get("to_addresses", [])
        or (facts.get("reply_classification") == "human" and facts.get("auto_submitted") != "no")
    ):
        raise ValueError("Gmail reply sender, recipient and automation headers must be verified")


class PostHog:
    def __init__(self):
        import requests

        project_id = os.getenv("POSTHOG_PROJECT_ID")
        if not project_id:
            raise ValueError("PostHog credentials unavailable")
        if str(project_id) != str(PROJECT):
            raise ValueError("unexpected PostHog project")
        self.http = requests.Session()
        self.token = os.getenv("POSTHOG_PROJECT_TOKEN")
        self.key = os.getenv("POSTHOG_PERSONAL_API_KEY")
        if not self.token or not self.key:
            raise ValueError("PostHog credentials unavailable")
        self.base = "https://us.posthog.com/api/projects/121185/"

    def request(self, method, path, **kwargs):
        response = self.http.request(
            method,
            self.base + path,
            headers={"Authorization": "Bearer " + self.key},
            timeout=40,
            **kwargs,
        )
        if response.status_code >= 400:
            raise RuntimeError("PostHog API request failed")
        return response.json()

    def verify_project(self):
        project = self.request("GET", "")
        if project["id"] != PROJECT or project["api_token"] != self.token:
            raise ValueError("PostHog token/project binding mismatch")

    def observed(self, event):
        event_id = str(uuid.UUID(event["uuid"]))
        day = timestamp(event["timestamp"]).strftime("%Y-%m-%d")
        sql = f"SELECT count() FROM events WHERE timestamp >= toDateTime('{day}') AND timestamp < toDateTime('{day}') + INTERVAL 1 DAY AND uuid = '{event_id}'"
        result = self.request(
            "POST",
            "query/",
            json={"query": {"kind": "HogQLQuery", "query": sql}, "refresh": "force_blocking"},
        )
        return bool(result["results"][0][0])

    def capture(self, event):
        self.capture_batch([event])

    def capture_batch(self, events):
        response = self.http.post(
            "https://us.i.posthog.com/batch/",
            json={"api_key": self.token, "batch": events},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError("PostHog capture failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--record", type=Path, help="private reviewed provider observation JSON")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--x-dm-reconciliation",
        type=Path,
        help="private reviewed X DM conversation readback JSON",
    )
    parser.add_argument(
        "--daily",
        action="store_true",
        help="also fetch yesterday and unique-person metrics for Smartlead 3935204",
    )
    parser.add_argument("--linkedin-db", type=Path, default=ROOT / "linkedin/data/linkedin.sqlite3")
    parser.add_argument(
        "--sync",
        action="store_true",
        help="import LinkedIn receipts and reconcile/deliver to PostHog",
    )
    args = parser.parse_args()
    ledger = Ledger(args.db)
    result = {}
    if args.record:
        result["receipt_id"] = ledger.record(
            json.loads(args.record.read_text()), args.evidence or args.record
        )
    if args.x_dm_reconciliation:
        result["x_dm_reconciliation"] = ledger.import_x_dm_reconciliation(args.x_dm_reconciliation)
    if args.sync or args.daily:
        # One local writer. Stable event UUIDs also protect retries across processes.
        import fcntl

        with Path(str(args.db) + ".sync.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result["linkedin"] = ledger.import_linkedin(args.linkedin_db)
            client = PostHog()
            result["delivery"] = ledger.sync(client)
            if args.daily:
                from crm.smartlead import SmartleadClient
                from crm.smartlead_metrics import queue

                result["smartlead"] = queue(ledger, SmartleadClient(os.getenv("SMARTLEAD_API_KEY")))
            result["snapshots"] = ledger.snapshot(client)
    result["status"] = ledger.status()
    print(json.dumps(result, indent=2))
    if (
        result.get("delivery", {}).get("failed")
        or result.get("linkedin", {}).get("rejected")
        or result.get("snapshots", {}).get("failed")
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "status": "not_completed"}))
        raise SystemExit(1) from None
