"""Replay-safe X DM request workflow with a private, step-by-step CRM audit.

Provider access is injected by the scheduled operator. This module stores no
credentials and never retries an uncertain external action automatically.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data/x-dm-watcher.sqlite3"
STEP_NAMES = {
    1: "check_x_dm_requests",
    2: "log_request",
    3: "verify_sender",
    4: "log_verification",
    5: "accept_or_hold",
    6: "capture_context",
    7: "reply_or_hold",
    8: "confirm_x_action",
    9: "flag_in_slack",
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS dm_runs (
 run_id TEXT PRIMARY KEY, account TEXT NOT NULL, identity TEXT NOT NULL,
 checked_at TEXT NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dm_requests (
 request_key TEXT PRIMARY KEY, account TEXT NOT NULL, request_id TEXT NOT NULL,
 conversation_id TEXT NOT NULL, incoming_id TEXT NOT NULL,
 sender_handle TEXT NOT NULL, sender_profile_url TEXT NOT NULL,
 detected_at TEXT NOT NULL, facts TEXT NOT NULL, state TEXT NOT NULL,
 person_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(account,request_id), UNIQUE(account,incoming_id));
CREATE TABLE IF NOT EXISTS dm_steps (
 step_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, request_key TEXT,
 step_number INTEGER NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
 occurred_at TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dm_contexts (
 request_key TEXT PRIMARY KEY, context_hash TEXT NOT NULL, payload TEXT NOT NULL,
 captured_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dm_replies (
 request_key TEXT PRIMARY KEY, incoming_id TEXT NOT NULL, body TEXT NOT NULL,
 body_hash TEXT NOT NULL, review TEXT NOT NULL, state TEXT NOT NULL,
 approver TEXT, approved_at TEXT);
CREATE TABLE IF NOT EXISTS dm_actions (
 action_id TEXT PRIMARY KEY, request_key TEXT NOT NULL, action_type TEXT NOT NULL,
 idempotency_key TEXT NOT NULL UNIQUE, payload_hash TEXT NOT NULL,
 state TEXT NOT NULL, external_reference TEXT, observed_at TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(request_key,action_type));
"""


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def required(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonempty exact string required")
    return value


def timestamp(value):
    parsed = datetime.fromisoformat(required(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed


class XDMWatcher:
    """Nine-step X DM workflow. Private message content remains in local CRM data."""

    def __init__(self, path=DEFAULT_DB):
        self.path = Path(path).resolve()
        if self.path.name in {
            "crm.sqlite3",
            "private-crm-cache.sqlite3",
            "activity.sqlite3",
            "linkedin.sqlite3",
        }:
            raise ValueError("dedicated X DM watcher ledger required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connection() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def begin_run(self, *, account, identity, checked_at, evidence):
        for value in (account, checked_at, evidence):
            required(value)
        timestamp(checked_at)
        if not isinstance(identity, dict) or set(identity) != {"handle", "profile_url"}:
            raise ValueError("exact X handle and profile URL required")
        required(identity["handle"])
        if not required(identity["profile_url"]).startswith("https://x.com/"):
            raise ValueError("verified X profile URL required")
        run_id = digest([account, identity, checked_at, evidence])
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM dm_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            values = (run_id, account, encode(identity), checked_at, evidence)
            if existing and tuple(existing) != values:
                raise ValueError("run identity conflict")
            connection.execute("INSERT OR IGNORE INTO dm_runs VALUES (?,?,?,?,?)", values)
            self._step(
                connection,
                run_id,
                None,
                1,
                "complete",
                {"account": account, "identity": identity, "evidence": evidence},
                checked_at,
            )
        return run_id

    def observe(self, run_id, facts):
        required(run_id)
        expected = {
            "account",
            "request_id",
            "conversation_id",
            "incoming_id",
            "sender_handle",
            "sender_profile_url",
            "incoming_message",
            "detected_at",
            "conversation_url",
            "evidence",
        }
        if (
            not isinstance(facts, dict)
            or not expected <= set(facts)
            or set(facts) - expected - {"occurred_at"}
        ):
            raise ValueError("complete exact X DM request facts required")
        for key in expected:
            required(facts[key])
        detected = timestamp(facts["detected_at"])
        if facts.get("occurred_at") and timestamp(facts["occurred_at"]) > detected:
            raise ValueError("message timestamp cannot follow detection")
        if not facts["sender_profile_url"].startswith("https://x.com/"):
            raise ValueError("exact X sender profile required")
        if not facts["conversation_url"].startswith("https://x.com/messages/"):
            raise ValueError("exact X conversation URL required")
        request_key = digest([facts["account"], facts["request_id"]])
        with self.connection() as connection:
            run = connection.execute(
                "SELECT account FROM dm_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not run or run["account"] != facts["account"]:
                raise ValueError("request must match a verified X run account")
            existing = connection.execute(
                "SELECT facts FROM dm_requests WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing and existing["facts"] != encode(facts):
                raise ValueError("same X request changed; reconcile before acting")
            at = now()
            connection.execute(
                "INSERT OR IGNORE INTO dm_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    request_key,
                    facts["account"],
                    facts["request_id"],
                    facts["conversation_id"],
                    facts["incoming_id"],
                    facts["sender_handle"],
                    facts["sender_profile_url"],
                    facts["detected_at"],
                    encode(facts),
                    "detected",
                    None,
                    at,
                    at,
                ),
            )
            self._step(
                connection,
                run_id,
                request_key,
                2,
                "complete",
                {
                    "request_id": facts["request_id"],
                    "incoming_id": facts["incoming_id"],
                    "sender_handle": facts["sender_handle"],
                    "evidence": facts["evidence"],
                },
            )
        return request_key

    def review_sender(self, run_id, request_key, *, decision, evidence, person_id=None):
        if decision not in {"legitimate", "spam", "hold"}:
            raise ValueError("sender decision must be legitimate, spam or hold")
        required(evidence)
        if person_id is not None:
            required(person_id)
        with self.connection() as connection:
            request = self._request(connection, request_key)
            detail = {"decision": decision, "evidence": evidence, "person_id": person_id}
            self._step(connection, run_id, request_key, 3, "complete", detail)
            self._step(connection, run_id, request_key, 4, decision, detail)
            state = "verified" if decision == "legitimate" else "held"
            connection.execute(
                "UPDATE dm_requests SET state=?,person_id=?,updated_at=? WHERE request_key=?",
                (state, person_id, now(), request_key),
            )
            if request["state"] not in {"detected", state}:
                raise ValueError("sender review conflicts with current request state")

    def accept(self, run_id, request_key, submit):
        with self.connection() as connection:
            request = self._request(connection, request_key)
            if request["state"] == "held":
                self._step(
                    connection,
                    run_id,
                    request_key,
                    5,
                    "held",
                    {"reason": "sender_not_verified"},
                )
                return {"status": "held", "reason": "sender_not_verified"}
            if request["state"] not in {"verified", "accepted"}:
                raise ValueError("verified sender required before accepting request")
            facts = json.loads(request["facts"])
        result = self._external_action(request_key, "accept", facts, submit)
        with self.connection() as connection:
            status = "accepted" if result["status"] == "confirmed" else "uncertain"
            self._step(connection, run_id, request_key, 5, status, result)
            if status == "accepted":
                connection.execute(
                    "UPDATE dm_requests SET state='accepted',updated_at=? WHERE request_key=?",
                    (now(), request_key),
                )
        return result

    def capture_context(self, run_id, request_key, context):
        expected = {
            "incoming_message",
            "incoming_id",
            "conversation_history",
            "crm_summary",
            "readback_complete",
            "context_sufficient",
            "sensitive",
        }
        if not isinstance(context, dict) or set(context) != expected:
            raise ValueError("complete DM and CRM context required")
        if context["readback_complete"] is not True:
            raise ValueError("complete conversation readback required")
        if (
            type(context["context_sufficient"]) is not bool
            or type(context["sensitive"]) is not bool
        ):
            raise ValueError("explicit context and sensitivity decisions required")
        with self.connection() as connection:
            request = self._request(connection, request_key)
            facts = json.loads(request["facts"])
            if request["state"] != "accepted":
                raise ValueError("accepted request required before context capture")
            if (
                context["incoming_message"] != facts["incoming_message"]
                or context["incoming_id"] != facts["incoming_id"]
            ):
                raise ValueError("context must preserve the exact incoming X message")
            payload = encode(context)
            context_hash = digest(context)
            existing = connection.execute(
                "SELECT context_hash FROM dm_contexts WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing and existing["context_hash"] != context_hash:
                raise ValueError("changed context requires a new reviewed incoming message")
            connection.execute(
                "INSERT OR IGNORE INTO dm_contexts VALUES (?,?,?,?)",
                (request_key, context_hash, payload, now()),
            )
            connection.execute(
                "UPDATE dm_requests SET state='context_ready',updated_at=? WHERE request_key=?",
                (now(), request_key),
            )
            self._step(
                connection,
                run_id,
                request_key,
                6,
                "complete",
                {
                    "context_hash": context_hash,
                    "context_sufficient": context["context_sufficient"],
                    "sensitive": context["sensitive"],
                },
            )
        return context_hash

    def prepare_reply(self, run_id, request_key, *, body, review):
        required(review)
        with self.connection() as connection:
            request = self._request(connection, request_key)
            context = connection.execute(
                "SELECT payload FROM dm_contexts WHERE request_key=?", (request_key,)
            ).fetchone()
            if request["state"] != "context_ready" or not context:
                raise ValueError("reviewed context required")
            context_payload = json.loads(context["payload"])
            if not context_payload["context_sufficient"] or context_payload["sensitive"]:
                self._step(
                    connection,
                    run_id,
                    request_key,
                    7,
                    "held",
                    {"reason": "insufficient_or_sensitive_context", "review": review},
                )
                connection.execute(
                    "UPDATE dm_requests SET state='held',updated_at=? WHERE request_key=?",
                    (now(), request_key),
                )
                return None
            required(body)
            if len(body) > 1000:
                raise ValueError("X DM reply exceeds local safety limit")
            body_hash = digest(body)
            existing = connection.execute(
                "SELECT body_hash FROM dm_replies WHERE request_key=?", (request_key,)
            ).fetchone()
            if existing and existing["body_hash"] != body_hash:
                raise ValueError("reply revision requires explicit replacement review")
            connection.execute(
                "INSERT OR IGNORE INTO dm_replies VALUES (?,?,?,?,?,'prepared',NULL,NULL)",
                (request_key, request["incoming_id"], body, body_hash, review),
            )
            connection.execute(
                "UPDATE dm_requests SET state='reply_prepared',updated_at=? WHERE request_key=?",
                (now(), request_key),
            )
            self._step(
                connection,
                run_id,
                request_key,
                7,
                "prepared",
                {"body_hash": body_hash, "review": review},
            )
        return body_hash

    def approve_reply(self, request_key, *, exact_body, approver):
        required(exact_body)
        required(approver)
        with self.connection() as connection:
            reply = connection.execute(
                "SELECT * FROM dm_replies WHERE request_key=?", (request_key,)
            ).fetchone()
            if not reply or reply["body_hash"] != digest(exact_body):
                raise ValueError("approval must match the exact prepared reply")
            connection.execute(
                "UPDATE dm_replies SET state='approved',approver=?,approved_at=? WHERE request_key=?",
                (approver, now(), request_key),
            )
            connection.execute(
                "UPDATE dm_requests SET state='reply_approved',updated_at=? WHERE request_key=?",
                (now(), request_key),
            )

    def send_reply(self, run_id, request_key, submit):
        with self.connection() as connection:
            request = self._request(connection, request_key)
            reply = connection.execute(
                "SELECT * FROM dm_replies WHERE request_key=?", (request_key,)
            ).fetchone()
            if request["state"] not in {"reply_approved", "reply_sent"}:
                raise ValueError("exact reply approval required before X submission")
            if not reply or reply["state"] != "approved":
                raise ValueError("exact reply approval required before X submission")
            payload = {
                "account": request["account"],
                "conversation_id": request["conversation_id"],
                "incoming_id": request["incoming_id"],
                "body": reply["body"],
                "body_hash": reply["body_hash"],
            }
        result = self._external_action(request_key, "reply", payload, submit)
        with self.connection() as connection:
            status = "sent" if result["status"] == "confirmed" else "uncertain"
            self._step(connection, run_id, request_key, 7, status, result)
            self._step(connection, run_id, request_key, 8, status, result)
            if status == "sent":
                connection.execute(
                    "UPDATE dm_requests SET state='reply_sent',updated_at=? WHERE request_key=?",
                    (now(), request_key),
                )
        return result

    def confirm_accept_only(self, run_id, request_key):
        """Log step 8 when no reply is useful for this incoming request."""
        with self.connection() as connection:
            request = self._request(connection, request_key)
            action = connection.execute(
                "SELECT * FROM dm_actions WHERE request_key=? AND action_type='accept'",
                (request_key,),
            ).fetchone()
            if request["state"] not in {"context_ready", "held"} or not action:
                raise ValueError("confirmed accept and reviewed context required")
            if action["state"] != "confirmed":
                raise ValueError("uncertain X action cannot be confirmed")
            self._step(
                connection,
                run_id,
                request_key,
                8,
                "accepted_no_reply",
                {"external_reference": action["external_reference"]},
            )

    def flag_slack(self, run_id, request_key, notify):
        with self.connection() as connection:
            request = self._request(connection, request_key)
            step8 = connection.execute(
                "SELECT 1 FROM dm_steps WHERE request_key=? AND step_number=8",
                (request_key,),
            ).fetchone()
            if not step8:
                raise ValueError("X action outcome must be logged before Slack")
            facts = json.loads(request["facts"])
            payload = {
                "watcher": "x dm watcher",
                "sender_handle": request["sender_handle"],
                "sender_profile_url": request["sender_profile_url"],
                "conversation_url": facts["conversation_url"],
                "request_state": request["state"],
                "person_id": request["person_id"],
                "incoming_id": request["incoming_id"],
            }
        result = self._external_action(request_key, "slack", payload, notify)
        with self.connection() as connection:
            status = "flagged" if result["status"] == "confirmed" else "uncertain"
            self._step(connection, run_id, request_key, 9, status, result)
        return result

    def sync_shared_history(self, request_key, *, shared_factory=None):
        """Write body-free inbound/outbound history after an exact person match."""
        if shared_factory is None:
            from crm.database import configured, connect

            if not configured():
                raise RuntimeError("shared CRM configuration required; no SQLite fallback")
            shared_factory = connect
        with self.connection() as local:
            request = self._request(local, request_key)
            if not request["person_id"]:
                raise ValueError("exact CRM person association required")
            facts = json.loads(request["facts"])
            reply = local.execute(
                "SELECT * FROM dm_actions WHERE request_key=? AND action_type='reply' AND state='confirmed'",
                (request_key,),
            ).fetchone()
        records = [
            (
                "xdm_in_" + digest([request["account"], request["incoming_id"]])[:24],
                request["person_id"],
                "x",
                "inbound",
                facts["detected_at"],
                "replied",
                request["incoming_id"],
                "X DM watcher; private message content retained only in the local ledger",
            )
        ]
        if reply:
            records.append(
                (
                    "xdm_out_" + digest(reply["external_reference"])[:24],
                    request["person_id"],
                    "x",
                    "outbound",
                    reply["observed_at"],
                    "sent",
                    reply["external_reference"],
                    "Approved X DM conversation reply; body retained only in the local ledger",
                )
            )
        connection = shared_factory()
        try:
            with connection as shared:
                if not shared.execute(
                    "SELECT 1 FROM people WHERE person_id=?", (request["person_id"],)
                ).fetchone():
                    raise ValueError("unknown shared CRM person")
                for record in records:
                    shared.execute(
                        "INSERT INTO outreach_events(event_id,person_id,channel,direction,occurred_at,outcome,external_reference,notes) "
                        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
                        record,
                    )
                    from crm.relationships import mirror_interaction

                    inbound = record[3] == "inbound"
                    mirror_interaction(
                        shared,
                        event_id=record[0],
                        person_id=request["person_id"],
                        kind="reply" if inbound else "outbound",
                        account_key=request["account"],
                        provider_id=record[6],
                        observed_at=facts["detected_at"] if inbound else reply["observed_at"],
                        occurred_at=facts.get("occurred_at") if inbound else None,
                        evidence="Verified X DM watcher provider readback; body retained locally",
                        reviewer="x_dm_watcher",
                    )
        finally:
            connection.close()
        return [record[0] for record in records]

    def status(self):
        with self.connection() as connection:
            return {
                "runs": connection.execute("SELECT count(*) FROM dm_runs").fetchone()[0],
                "requests": connection.execute("SELECT count(*) FROM dm_requests").fetchone()[0],
                "steps": connection.execute("SELECT count(*) FROM dm_steps").fetchone()[0],
                "states": {
                    row["state"]: row["n"]
                    for row in connection.execute(
                        "SELECT state,count(*) AS n FROM dm_requests GROUP BY state"
                    )
                },
                "uncertain_actions": connection.execute(
                    "SELECT count(*) FROM dm_actions WHERE state='uncertain'"
                ).fetchone()[0],
            }

    def audit(self, request_key):
        with self.connection() as connection:
            self._request(connection, request_key)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT step_number,name,status,occurred_at,detail FROM dm_steps "
                    "WHERE request_key=? ORDER BY occurred_at,step_number",
                    (request_key,),
                )
            ]

    def _external_action(self, request_key, action_type, payload, submit):
        if action_type not in {"accept", "reply", "slack"}:
            raise ValueError("unsupported external action")
        if not callable(submit):
            raise TypeError("external action callback required")
        payload_hash = digest(payload)
        action_id = digest([request_key, action_type])
        idempotency_key = "xdm-" + action_id[:32]
        at = now()
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM dm_actions WHERE action_id=?", (action_id,)
            ).fetchone()
            if existing:
                if existing["payload_hash"] != payload_hash:
                    raise ValueError("external action payload changed")
                if existing["state"] == "confirmed":
                    return {
                        "status": "confirmed",
                        "external_reference": existing["external_reference"],
                        "observed_at": existing["observed_at"],
                    }
                return {"status": "uncertain", "reason": "reconciliation_required"}
            connection.execute(
                "INSERT INTO dm_actions VALUES (?,?,?,?,?,'reserved',NULL,NULL,?,?)",
                (action_id, request_key, action_type, idempotency_key, payload_hash, at, at),
            )
        try:
            receipt = submit(payload, idempotency_key)
            if not isinstance(receipt, dict) or receipt.get("status") != "confirmed":
                raise ValueError("confirmed external readback required")
            external_reference = required(receipt.get("external_reference"))
            observed_at = required(receipt.get("observed_at"))
            timestamp(observed_at)
            with self.connection() as connection:
                connection.execute(
                    "UPDATE dm_actions SET state='confirmed',external_reference=?,observed_at=?,updated_at=? WHERE action_id=?",
                    (external_reference, observed_at, now(), action_id),
                )
            return {
                "status": "confirmed",
                "external_reference": external_reference,
                "observed_at": observed_at,
            }
        except Exception as exc:  # noqa: BLE001 - uncertain external outcomes must be held.
            with self.connection() as connection:
                connection.execute(
                    "UPDATE dm_actions SET state='uncertain',updated_at=? WHERE action_id=?",
                    (now(), action_id),
                )
            return {"status": "uncertain", "error": type(exc).__name__}

    def _request(self, connection, request_key):
        row = connection.execute(
            "SELECT * FROM dm_requests WHERE request_key=?", (required(request_key),)
        ).fetchone()
        if not row:
            raise ValueError("unknown X DM request")
        return row

    def _step(
        self,
        connection,
        run_id,
        request_key,
        number,
        status,
        detail,
        occurred_at=None,
    ):
        if number not in STEP_NAMES:
            raise ValueError("step must be 1 through 9")
        required(run_id)
        required(status)
        occurred_at = occurred_at or now()
        timestamp(occurred_at)
        payload = encode(detail)
        step_id = digest([run_id, request_key, number, status, detail])
        existing = connection.execute(
            "SELECT * FROM dm_steps WHERE step_id=?", (step_id,)
        ).fetchone()
        values = (
            step_id,
            run_id,
            request_key,
            number,
            STEP_NAMES[number],
            status,
            occurred_at,
            payload,
        )
        if existing:
            existing_keys = existing.keys()
            stable_existing = tuple(existing[key] for key in existing_keys if key != "occurred_at")
            stable_values = values[:6] + values[7:]
            if stable_existing != stable_values:
                raise ValueError("CRM step audit conflict")
        connection.execute("INSERT OR IGNORE INTO dm_steps VALUES (?,?,?,?,?,?,?,?)", values)
        return step_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--audit", help="request key to inspect")
    args = parser.parse_args()
    watcher = XDMWatcher(args.db)
    if args.audit:
        print(json.dumps(watcher.audit(args.audit), indent=2))
    else:
        print(json.dumps(watcher.status(), indent=2))


if __name__ == "__main__":
    main()
