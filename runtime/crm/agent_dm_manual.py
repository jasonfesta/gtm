"""Manual Agent DM outbox with AgentMail send and exact readback.

No scheduler, worker, or automatic retry. The only sending command names one
staged candidate and requires an explicit sender inbox. Network errors leave an
uncertain row; reconciliation reads the provider before any further action.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path

from crm.agent_dm import _time, confirmed_handoffs

API = "https://api.agentmail.to/v0"


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def now():
    return datetime.now(timezone.utc).isoformat()


def _email(value):
    parsed = parseaddr(str(value or ""))[1]
    if not parsed or "@" not in parsed or parsed.count("@") != 1:
        raise ValueError("valid email address required")
    return parsed.casefold()


def message_payload(prepared, resource_path=None):
    """Freeze the exact AgentMail request bytes before a possible send."""
    if prepared.get("outcome") != "prepared":
        raise ValueError("prepared candidate required")
    route = prepared["route"]
    if route.get("channel") != "agent_email" or route.get("transport_provider") != "agentmail":
        raise ValueError("manual AgentMail sender requires a verified agent_email route")
    recipient = _email(route["address"])
    draft = prepared["draft"]
    subject = str(draft.get("subject") or "").strip()
    body = str(draft.get("body") or "").strip()
    kind = prepared.get("kind") or "reaction"
    if not body or (kind != "follow_up" and not subject):
        raise ValueError("reviewed email subject and body required")
    resource = prepared.get("resource") or {"delivery_form": "none"}
    form = resource.get("delivery_form")
    payload = {"to": [recipient], "text": body}
    if kind != "follow_up":
        payload["subject"] = subject
    elif not (prepared.get("trigger") or {}).get("inbound_message_id"):
        raise ValueError("follow-up requires inbound AgentMail message ID")
    if form == "https_link":
        link = str(resource.get("url") or "")
        if not link.startswith("https://"):
            raise ValueError("verified HTTPS Markdown URL required")
        if link not in body:
            payload["text"] += f"\n\nDarwin guide ({resource['version']}): {link}"
    elif form == "attachment":
        if not resource_path:
            raise ValueError("local Markdown path required for attachment")
        path = Path(resource_path)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != resource.get("sha256"):
            raise ValueError("Markdown attachment hash mismatch")
        if len(raw) > 3_000_000:
            raise ValueError("Markdown attachment exceeds conservative inline limit")
        filename = resource.get("attachment_name") or path.name
        if not filename.endswith(".md"):
            raise ValueError("Markdown attachment must have .md filename")
        payload["attachments"] = [
            {
                "content": base64.b64encode(raw).decode("ascii"),
                "filename": filename,
                "content_type": "text/markdown",
            }
        ]
    elif form != "none":
        raise ValueError("delivery form is unsupported by AgentMail email")
    return payload


class AgentMail:
    """Small documented REST transport; an MCP tool may supply the same readback."""

    def __init__(self, key=None, opener=None):
        self.key = key if key is not None else os.environ.get("AGENTMAIL_API_KEY", "")
        self.opener = opener or urllib.request.urlopen
        if not self.key:
            raise ValueError("AGENTMAIL_API_KEY required")

    def request(self, method, path, payload=None, idempotency_key=None):
        headers = {"Authorization": "Bearer " + self.key, "Accept": "application/json"}
        body = None
        if payload is not None:
            body = encoded(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(API + path, data=body, headers=headers, method=method)
        try:
            with self.opener(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            error.read()
            raise RuntimeError(f"AgentMail HTTP {error.code}") from None

    def get_inbox(self, inbox_id):
        return self.request("GET", "/inboxes/" + urllib.parse.quote(inbox_id, safe=""))

    def send(self, inbox_id, payload, idempotency_key):
        path = "/inboxes/" + urllib.parse.quote(inbox_id, safe="") + "/messages/send"
        return self.request("POST", path, payload, idempotency_key)

    def reply(self, inbox_id, message_id, payload, idempotency_key):
        path = (
            "/inboxes/"
            + urllib.parse.quote(inbox_id, safe="")
            + "/messages/"
            + urllib.parse.quote(message_id, safe="")
            + "/reply"
        )
        return self.request("POST", path, payload, idempotency_key)

    def get_message(self, inbox_id, message_id):
        path = (
            "/inboxes/"
            + urllib.parse.quote(inbox_id, safe="")
            + "/messages/"
            + urllib.parse.quote(message_id, safe="")
        )
        return self.request("GET", path)


class ManualOutbox:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS agent_dm_jobs_v2 (
                    candidate_id TEXT PRIMARY KEY,
                    target_agent_id TEXT NOT NULL,
                    inbox_id TEXT NOT NULL,
                    sender_address TEXT NOT NULL,
                    prepared_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL
                        CHECK(state IN ('ready','sending','uncertain','confirmed')),
                    provider_id TEXT,
                    provider_response_json TEXT,
                    receipt_json TEXT,
                    handoffs_json TEXT,
                    claimed_at TEXT,
                    created_at TEXT NOT NULL
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS agent_dm_jobs_v2_target "
                "ON agent_dm_jobs_v2(target_agent_id)"
            )
            old = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_dm_jobs'"
            ).fetchone()
            if old:
                db.execute("INSERT OR IGNORE INTO agent_dm_jobs_v2 SELECT * FROM agent_dm_jobs")
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def stage(self, prepared, inbox_id, sender_address, *, resource_path=None):
        candidate_id = prepared["candidate_id"]
        target_agent_id = prepared["target_agent_id"]
        if not target_agent_id:
            raise ValueError("target_agent_id required")
        inbox_id = str(inbox_id or "").strip()
        if not inbox_id:
            raise ValueError("sender inbox ID required")
        sender_address = _email(sender_address)
        payload = message_payload(prepared, resource_path)
        payload_json = encoded(payload)
        digest = hashlib.sha256(payload_json.encode()).hexdigest()
        key = "agent-dm-" + candidate_id.removeprefix("agent_dm_candidate_")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM agent_dm_jobs_v2 WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if prior:
                if (
                    prior["payload_sha256"] != digest
                    or prior["inbox_id"] != inbox_id
                    or prior["sender_address"] != sender_address
                    or prior["prepared_json"] != encoded(prepared)
                ):
                    raise ValueError("staged candidate conflict")
                return self.public(prior)
            earlier = db.execute(
                "SELECT candidate_id,state,prepared_json,receipt_json FROM agent_dm_jobs_v2 "
                "WHERE target_agent_id=?",
                (target_agent_id,),
            ).fetchall()
            if any(item["state"] != "confirmed" for item in earlier):
                raise ValueError("agent already has a staged or uncertain outbound message")
            kind = prepared.get("kind") or "reaction"
            if earlier and kind != "follow_up":
                raise ValueError("agent already has a confirmed first-contact message")
            if kind == "follow_up":
                trigger = prepared.get("trigger") or {}
                inbound_id = trigger.get("inbound_message_id")
                if not inbound_id:
                    raise ValueError("follow-up inbound message ID required")
                for item in earlier:
                    previous = json.loads(item["prepared_json"])
                    if (previous.get("trigger") or {}).get("inbound_message_id") == inbound_id:
                        raise ValueError("inbound reply already has a staged follow-up")
                if earlier:
                    latest_send = max(
                        json.loads(item["receipt_json"])["sent_at"] for item in earlier
                    )
                    if _time(trigger.get("occurred_at"), "trigger.occurred_at") <= latest_send:
                        raise ValueError("follow-up reply is not newer than prior outbound")
            db.execute(
                "INSERT INTO agent_dm_jobs_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    target_agent_id,
                    inbox_id,
                    sender_address,
                    encoded(prepared),
                    payload_json,
                    digest,
                    key,
                    "ready",
                    None,
                    None,
                    None,
                    None,
                    None,
                    now(),
                ),
            )
        return self.get(candidate_id)

    @staticmethod
    def public(row):
        return {
            "candidate_id": row["candidate_id"],
            "target_agent_id": row["target_agent_id"],
            "inbox_id": row["inbox_id"],
            "sender_address": row["sender_address"],
            "recipient": json.loads(row["payload_json"])["to"][0],
            "subject": json.loads(row["payload_json"]).get("subject"),
            "reply_to_message_id": (json.loads(row["prepared_json"]).get("trigger") or {}).get(
                "inbound_message_id"
            ),
            "payload_sha256": row["payload_sha256"],
            "idempotency_key": row["idempotency_key"],
            "state": row["state"],
            "provider_id": row["provider_id"],
        }

    def get(self, candidate_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_dm_jobs_v2 WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown candidate")
            return self.public(row)

    def preview(self, candidate_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_dm_jobs_v2 WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise ValueError("unknown candidate")
        payload = json.loads(row["payload_json"])
        return {
            **self.public(row),
            "body": payload["text"],
            "attachments": [
                {
                    "filename": item.get("filename"),
                    "content_type": item.get("content_type"),
                    "attachment_bytes": len(base64.b64decode(item.get("content") or "")),
                }
                for item in payload.get("attachments") or []
            ],
        }

    def claim(self, candidate_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM agent_dm_jobs_v2 WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if row is None or row["state"] != "ready":
                raise ValueError("candidate is not ready; reconcile uncertain work")
            db.execute(
                "UPDATE agent_dm_jobs_v2 SET state='sending',claimed_at=? WHERE candidate_id=?",
                (now(), candidate_id),
            )
            return dict(row)

    def _update(self, candidate_id, state, **values):
        assignments = ["state=?"] + [key + "=?" for key in values]
        with self.connect() as db:
            result = db.execute(
                "UPDATE agent_dm_jobs_v2 SET "
                + ",".join(assignments)
                + " WHERE candidate_id=? AND state IN ('sending','uncertain')",
                (state, *values.values(), candidate_id),
            )
            if result.rowcount != 1:
                raise ValueError("candidate state changed during provider operation")

    def send_one(self, candidate_id, client, *, expected_sha256=None):
        """Send exactly one named ready job; exceptions leave an uncertain row."""
        if not expected_sha256:
            raise ValueError("reviewed payload hash required before manual send")
        if self.get(candidate_id)["payload_sha256"] != expected_sha256:
            raise ValueError("reviewed payload hash does not match staged message")
        row = self.claim(candidate_id)
        try:
            inbox = client.get_inbox(row["inbox_id"])
            if _email(inbox.get("email")) != row["sender_address"]:
                raise ValueError("AgentMail inbox identity mismatch")
            prepared = json.loads(row["prepared_json"])
            payload = json.loads(row["payload_json"])
            if (prepared.get("kind") or "reaction") == "follow_up":
                trigger = prepared["trigger"]
                inbound_id = trigger["inbound_message_id"]
                inbound = client.get_message(row["inbox_id"], inbound_id)
                if (
                    inbound.get("inbox_id") != row["inbox_id"]
                    or inbound.get("message_id") != inbound_id
                    or inbound.get("thread_id") != trigger["inbound_thread_id"]
                    or _email(inbound.get("from")) != payload["to"][0]
                    or row["sender_address"]
                    not in {_email(address) for address in inbound.get("to") or []}
                    or _time(inbound.get("timestamp"), "inbound.timestamp")
                    != trigger["occurred_at"]
                    or hashlib.sha256(str(inbound.get("text") or "").encode()).hexdigest()
                    != prepared["inbound_text_sha256"]
                ):
                    raise ValueError("inbound AgentMail reply does not match reviewed follow-up")
                response = client.reply(
                    row["inbox_id"], inbound_id, payload, row["idempotency_key"]
                )
            else:
                response = client.send(row["inbox_id"], payload, row["idempotency_key"])
            provider_id = str(response.get("message_id") or "").strip()
            if not provider_id:
                raise RuntimeError("AgentMail send returned no message_id")
            self._update(
                candidate_id,
                "uncertain",
                provider_id=provider_id,
                provider_response_json=encoded(response),
            )
            observed = client.get_message(row["inbox_id"], provider_id)
            return self._confirm_readback(candidate_id, observed, expected_id=provider_id)
        except Exception:
            self._update(candidate_id, "uncertain")
            raise

    def reconcile(self, candidate_id, observed, client=None):
        """A file is only a locator: fetch the authoritative message again."""
        if client is None:
            raise ValueError("authenticated provider fetch required; local JSON is not proof")
        locator = str(observed.get("message_id") or "").strip()
        if not locator:
            raise ValueError("provider message ID required")
        row = self.get(candidate_id)
        if row["provider_id"] and row["provider_id"] != locator:
            raise ValueError("provider message ID mismatch")
        return self._confirm_readback(
            candidate_id, client.get_message(row["inbox_id"], locator), expected_id=locator
        )

    def _confirm_readback(self, candidate_id, observed, *, expected_id):
        """Internal validation called only on a fresh provider response."""
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM agent_dm_jobs_v2 WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
        if row is None or row["state"] not in ("sending", "uncertain"):
            raise ValueError("candidate is not awaiting provider reconciliation")
        payload = json.loads(row["payload_json"])
        prepared = json.loads(row["prepared_json"])
        kind = prepared.get("kind") or "reaction"
        message_id = str(observed.get("message_id") or "").strip()
        if (
            not message_id
            or message_id != expected_id
            or (row["provider_id"] and row["provider_id"] != message_id)
        ):
            raise ValueError("provider message ID mismatch")
        thread_id = observed.get("thread_id")
        response = json.loads(row["provider_response_json"] or "{}")
        if (
            not isinstance(thread_id, str)
            or not thread_id.strip()
            or (response.get("thread_id") and response["thread_id"] != thread_id)
        ):
            raise ValueError("provider thread mismatch or missing")
        if observed.get("cc") or observed.get("bcc"):
            raise ValueError("provider unexpected additional recipients")
        if observed.get("inbox_id") != row["inbox_id"]:
            raise ValueError("provider inbox mismatch")
        if _email(observed.get("from")) != row["sender_address"]:
            raise ValueError("provider sender mismatch")
        recipients = observed.get("to") or []
        if len(recipients) != 1 or _email(recipients[0]) != payload["to"][0]:
            raise ValueError("provider recipient mismatch")
        if kind != "follow_up" and observed.get("subject") != payload["subject"]:
            raise ValueError("provider subject mismatch")
        if kind == "follow_up" and (
            observed.get("in_reply_to") != prepared["trigger"]["inbound_message_id"]
            or observed.get("thread_id") != prepared["trigger"]["inbound_thread_id"]
        ):
            raise ValueError("provider reply linkage mismatch")
        observed_text = str(observed.get("text") or "")
        expected_text = payload["text"]
        if observed_text != expected_text:
            raise ValueError("provider body mismatch")
        if len(observed.get("attachments") or []) != len(payload.get("attachments") or []):
            raise ValueError("provider attachment set mismatch")
        if payload.get("attachments"):
            expected = payload["attachments"][0]["filename"]
            matches = [
                a for a in observed.get("attachments") or [] if a.get("filename") == expected
            ]
            if len(matches) != 1:
                raise ValueError("provider Markdown attachment missing or ambiguous")
            # A filename alone is not content verification. Hold until a provider
            # exposes the bytes; never use the local staged bytes as readback.
            content = matches[0].get("content")
            if not isinstance(content, str):
                raise ValueError("provider attachment bytes unavailable; remains uncertain")
            try:
                raw = base64.b64decode(content, validate=True)
            except Exception as error:
                raise ValueError("provider attachment content invalid") from error
            expected_raw = base64.b64decode(payload["attachments"][0]["content"], validate=True)
            if hashlib.sha256(raw).digest() != hashlib.sha256(expected_raw).digest():
                raise ValueError("provider attachment content mismatch")
        sent_at = _time(observed.get("timestamp"), "provider.timestamp")
        if not row["claimed_at"] or sent_at < _time(row["claimed_at"], "claimed_at"):
            raise ValueError("provider timestamp precedes send claim")
        receipt = {
            "status": "confirmed",
            "channel": "agent_email",
            "provider": "agentmail",
            "recipient": payload["to"][0],
            "provider_id": message_id,
            "sender": row["sender_address"],
            "thread_id": thread_id,
            "text": observed_text,
            "sent_at": sent_at,
            "observed_at": now(),
            "evidence": f"AgentMail get_message:{row['inbox_id']}:{message_id}",
        }
        handoffs = confirmed_handoffs(prepared, receipt)
        self._update(
            candidate_id,
            "confirmed",
            provider_id=message_id,
            receipt_json=encoded(receipt),
            handoffs_json=encoded(handoffs),
        )
        return handoffs

    def readback(self, candidate_id, client):
        with self.connect() as db:
            row = db.execute(
                "SELECT inbox_id,provider_id,state FROM agent_dm_jobs_v2 WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
        if row is None or row["state"] != "uncertain" or not row["provider_id"]:
            raise ValueError("provider ID missing; reconcile from independent inbox evidence")
        return self._confirm_readback(
            candidate_id,
            client.get_message(row["inbox_id"], row["provider_id"]),
            expected_id=row["provider_id"],
        )

    def handoffs(self, candidate_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT state,handoffs_json FROM agent_dm_jobs_v2 WHERE candidate_id=?",
                (candidate_id,),
            ).fetchone()
        if row is None or row["state"] != "confirmed":
            raise ValueError("confirmed readback required before handoffs")
        return json.loads(row["handoffs_json"])


def _write_new(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2) + "\n"
    if path.exists():
        if path.read_text() != raw:
            raise FileExistsError("conflicting handoff export")
        path.chmod(0o600)
        return
    # Write completely before publishing. A crash can leave a private temp file,
    # never a partial destination. link() publishes without clobbering a peer.
    descriptor, temporary = tempfile.mkstemp(prefix=".agent-dm-export-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_text() != raw:
                raise FileExistsError("conflicting handoff export") from None
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("plan", type=Path)
    stage.add_argument("--candidate-id", required=True)
    stage.add_argument("--inbox-id", required=True)
    stage.add_argument("--sender-address", required=True)
    stage.add_argument("--resource-path", type=Path)
    send = commands.add_parser("send")
    send.add_argument("--candidate-id", required=True)
    send.add_argument("--expect-sha256", required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--candidate-id", required=True)
    reconcile.add_argument("--readback", type=Path)
    reconcile.add_argument("--fetch", action="store_true")
    export = commands.add_parser("export")
    export.add_argument("--candidate-id", required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--candidate-id", required=True)
    preview = commands.add_parser("preview")
    preview.add_argument("--candidate-id", required=True)
    args = parser.parse_args(argv)
    outbox = ManualOutbox(args.db)
    if args.command == "stage":
        manifest = json.loads(args.plan.read_text())
        matches = [
            item
            for item in manifest.get("candidates", [])
            if item.get("candidate_id") == args.candidate_id
        ]
        if len(matches) != 1:
            raise ValueError("candidate ID does not identify one planned candidate")
        result = outbox.stage(
            matches[0], args.inbox_id, args.sender_address, resource_path=args.resource_path
        )
    elif args.command == "send":
        result = outbox.send_one(args.candidate_id, AgentMail(), expected_sha256=args.expect_sha256)
    elif args.command == "reconcile":
        if args.readback and args.fetch:
            raise ValueError("choose readback file or provider fetch")
        if args.readback:
            result = outbox.reconcile(
                args.candidate_id, json.loads(args.readback.read_text()), AgentMail()
            )
        elif args.fetch:
            result = outbox.readback(args.candidate_id, AgentMail())
        else:
            raise ValueError("readback file or provider fetch required")
    elif args.command == "export":
        handoffs = outbox.handoffs(args.candidate_id)
        crm_path = args.output_dir / (args.candidate_id + ".crm.json")
        posthog_path = args.output_dir / (args.candidate_id + ".posthog.json")
        _write_new(crm_path, handoffs["crm_request"])
        _write_new(posthog_path, handoffs["posthog_request"])
        result = {"crm_path": str(crm_path), "posthog_path": str(posthog_path)}
    elif args.command == "preview":
        result = outbox.preview(args.candidate_id)
    else:
        result = outbox.get(args.candidate_id)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
