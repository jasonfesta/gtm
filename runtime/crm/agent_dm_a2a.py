"""Manual A2A 0.3.0 JSON-RPC client. No worker, retries, or local-file receipts.

Supports text task conversations and bearer/no-auth interfaces. Other versions,
transports and security schemes fail closed. Identity comes from a reviewed
attestation, never from a public card alone. HTTP is limited to controlled loopback.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("redirect refused")


def fetch(url, payload=None, token=None):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(
        url, data=None if payload is None else json.dumps(payload).encode(), headers=headers
    )
    with build_opener(NoRedirect).open(request, timeout=55) as response:
        return json.loads(response.read(2_000_001))


def validate(card, policy, approval, token=None):
    endpoint = approval["endpoint"]
    parsed = urlparse(endpoint)
    controlled = (
        approval.get("controlled_test") is True and approval.get("organic_outreach") is False
    )
    if parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError("invalid endpoint")
    if parsed.scheme != "https" and not (
        controlled and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    ):
        raise ValueError("HTTPS required outside controlled loopback")
    if card.get("protocolVersion") != "0.3.0":
        raise ValueError("unsupported A2A version")
    interfaces = [
        {"url": card.get("url"), "transport": card.get("preferredTransport", "JSONRPC")}
    ] + card.get("additionalInterfaces", [])
    if {"url": endpoint, "transport": "JSONRPC"} not in interfaces:
        raise ValueError("reviewed interface not declared")
    if card.get("name") != approval.get("identity") or policy.get("identity") != approval.get(
        "identity"
    ):
        raise ValueError("identity mismatch")
    if approval.get("agent_operated") is not True or policy.get("agent_operated") is not True:
        raise ValueError("agent identity not verified")
    if (
        not approval.get("identity_evidence")
        or not approval.get("policy_evidence")
        or approval.get("authorized") is not True
    ):
        raise ValueError("reviewed identity/policy authorization required")
    verified = datetime.fromisoformat(approval["verified_at"])
    if verified.tzinfo is None:
        raise ValueError("verification timestamp requires timezone")
    age = (datetime.now(timezone.utc) - verified).total_seconds()
    if not 0 <= age <= 86400:
        raise ValueError("stale route verification")
    if (
        approval.get("history_complete") is not True
        or approval.get("suppressed") is not False
        or approval.get("opted_out") is not False
    ):
        raise ValueError("incomplete or suppressed history")
    if approval.get("budget_available") is not True or approval.get("cooldown_clear") is not True:
        raise ValueError("budget or cooldown hold")
    if policy.get("owner_approval_required") != "no" and approval.get("owner_approved") is not True:
        raise ValueError("owner approval required")
    if policy.get("controlled_test") is True and not controlled:
        raise ValueError("controlled route cannot be organic")
    if "a2a-0.3.0-jsonrpc" not in policy.get("allowed_protocols", []):
        raise ValueError("policy does not allow A2A")
    if "text/plain" not in card.get("defaultInputModes", []) or not card.get("skills"):
        raise ValueError("text capability missing")
    if approval.get("skill_id") not in [s.get("id") for s in card["skills"]]:
        raise ValueError("reviewed capability missing")
    security = card.get("security", [])
    schemes = card.get("securitySchemes", {})
    if security:
        supported = any(
            len(requirement) == 1
            and any(
                schemes.get(name, {}).get("type") == "http"
                and schemes[name].get("scheme", "").lower() == "bearer"
                and scopes == []
                for name, scopes in requirement.items()
            )
            for requirement in security
        )
        if not supported or not token:
            raise ValueError("unsupported or missing authentication")
    elif token:
        raise ValueError("credentials not declared by card")
    elif approval.get("allow_no_auth") is not True:
        raise ValueError("no-auth requires explicit approval")
    return endpoint


class Adapter:
    def __init__(self, db, card_url, policy_url, approval, token=None, transport=fetch):
        self.path = Path(db)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.approval, self.token, self.transport = approval, token, transport
        self.card_url, self.policy_url = card_url, policy_url
        # No cross-origin discovery, policy or authentication surprises.
        origin = urlparse(approval["endpoint"])[:2]
        if urlparse(card_url)[:2] != origin or urlparse(policy_url)[:2] != origin:
            raise ValueError("discovery origin mismatch")
        endpoint_url = urlparse(approval["endpoint"])
        controlled_loopback = (
            approval.get("controlled_test") is True
            and approval.get("organic_outreach") is False
            and endpoint_url.scheme == "http"
            and endpoint_url.hostname == "127.0.0.1"
        )
        if endpoint_url.scheme != "https" and not controlled_loopback:
            raise ValueError("discovery requires HTTPS or controlled loopback")
        if (
            approval.get("identity_evidence") != card_url
            or approval.get("policy_evidence") != policy_url
        ):
            raise ValueError("reviewed evidence URL mismatch")
        self.card = transport(card_url)
        self.policy = transport(policy_url)
        self.endpoint = validate(self.card, self.policy, approval, token)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, endpoint TEXT, state TEXT, payload TEXT, sha TEXT, task TEXT, context TEXT, response TEXT, readback TEXT, created TEXT)"
            )

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def rpc(self, method, params):
        request = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}
        response = self.transport(self.endpoint, request, self.token)
        if (
            response.get("jsonrpc") != "2.0"
            or response.get("id") != request["id"]
            or "error" in response
        ):
            raise ValueError("invalid or error RPC response; reconcile without resend")
        return response["result"]

    def stage(self, attempt_id, text, parent=None):
        validate(self.card, self.policy, self.approval, self.token)
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError("text must be 1-4000 characters")
        message = {
            "kind": "message",
            "role": "user",
            "messageId": str(uuid.uuid4()),
            "parts": [{"kind": "text", "text": text}],
        }
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM attempts WHERE endpoint=? ORDER BY rowid", (self.endpoint,)
            ).fetchall()
            if parent:
                prior = next((r for r in rows if r["id"] == parent), None)
                if not prior or prior["state"] != "confirmed" or rows[-1]["id"] != parent:
                    raise ValueError("follow-up requires latest confirmed parent")
                readback = json.loads(prior["readback"])
                if readback["status"]["state"] != "input-required":
                    raise ValueError("task not awaiting input")
                message.update(taskId=prior["task"], contextId=prior["context"])
            elif rows or self.approval.get("unanswered_outbound") is not False:
                raise ValueError("duplicate or unanswered first contact")
            payload = {
                "message": message,
                "configuration": {"blocking": True, "historyLength": 100},
            }
            sha = digest(payload)
            connection.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    self.endpoint,
                    "staged",
                    json.dumps(payload),
                    sha,
                    message.get("taskId"),
                    message.get("contextId"),
                    None,
                    None,
                    now(),
                ),
            )
        return {"attempt_id": attempt_id, "payload": payload, "sha256": sha}

    def attempt(self, attempt_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE id=? AND endpoint=?", (attempt_id, self.endpoint)
            ).fetchone()
        if row is None:
            raise ValueError("unknown attempt")
        return dict(row)

    def send(self, attempt_id, expected_sha256):
        validate(self.card, self.policy, self.approval, self.token)
        # Re-fetch discovery/policy just before the irreversible request.
        validate(
            self.transport(self.card_url),
            self.transport(self.policy_url),
            self.approval,
            self.token,
        )
        row = self.attempt(attempt_id)
        if row["sha"] != expected_sha256:
            raise ValueError("review hash mismatch")
        with self.connect() as connection:
            changed = connection.execute(
                "UPDATE attempts SET state='uncertain' WHERE id=? AND state='staged'", (attempt_id,)
            ).rowcount
            if changed != 1:
                raise ValueError("attempt already claimed; no resend")
        result = self.rpc("message/send", json.loads(row["payload"]))
        if result.get("kind") != "task" or not result.get("id") or not result.get("contextId"):
            raise ValueError("task response required; remains uncertain")
        if row["task"] and (result["id"] != row["task"] or result["contextId"] != row["context"]):
            raise ValueError("response task/context mismatch")
        with self.connect() as connection:
            connection.execute(
                "UPDATE attempts SET task=?, context=?, response=? WHERE id=?",
                (result["id"], result["contextId"], json.dumps(result), attempt_id),
            )
        return self.reconcile(attempt_id)

    def reconcile(self, attempt_id):
        row = self.attempt(attempt_id)
        if row["state"] == "staged" or not row["task"]:
            raise ValueError("provider task ID unavailable; hold, never resend")
        task = self.rpc("tasks/get", {"id": row["task"], "historyLength": 100})
        if (
            task.get("kind") != "task"
            or task.get("id") != row["task"]
            or task.get("contextId") != row["context"]
        ):
            raise ValueError("readback task/context mismatch")
        message = json.loads(row["payload"])["message"]
        history = task.get("history", [])
        indices = [
            i for i, item in enumerate(history) if item.get("messageId") == message["messageId"]
        ]
        if len(indices) != 1:
            raise ValueError("missing or ambiguous outbound identity")
        index = indices[0]
        if (
            history[index].get("role") != "user"
            or history[index].get("parts") != message["parts"]
            or history[index].get("taskId") != row["task"]
            or history[index].get("contextId") != row["context"]
        ):
            raise ValueError("readback does not contain exact outbound")
        reply_ids = [m.get("messageId") for m in history]
        if len(reply_ids) != len(set(reply_ids)) or None in reply_ids:
            raise ValueError("ambiguous history message IDs")
        replies = [
            m
            for m in history[index + 1 :]
            if m.get("role") == "agent"
            and m.get("messageId")
            and m.get("taskId") == row["task"]
            and m.get("contextId") == row["context"]
            and any(
                p.get("kind") == "text" and p.get("text", "").strip() for p in m.get("parts", [])
            )
        ]
        state = (
            "confirmed"
            if replies and task.get("status", {}).get("state") in ("input-required", "completed")
            else "uncertain"
        )
        with self.connect() as connection:
            connection.execute(
                "UPDATE attempts SET state=?, readback=? WHERE id=?",
                (state, json.dumps(task), attempt_id),
            )
        return self.attempt(attempt_id)


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument(
        "--token-env", help="Name of environment variable containing advertised bearer token"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--attempt-id", required=True)
    stage.add_argument("--text-file", type=Path, required=True)
    stage.add_argument("--parent")
    stage.add_argument("--output", type=Path, required=True)
    send = commands.add_parser("send")
    send.add_argument("--attempt-id", required=True)
    send.add_argument("--expect-sha256", required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--attempt-id", required=True)
    evidence = commands.add_parser("evidence")
    evidence.add_argument("--attempt-id", required=True)
    evidence.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    approval = json.loads(args.approval.read_text())
    adapter = Adapter(
        args.db,
        approval["identity_evidence"],
        approval["policy_evidence"],
        approval,
        token=os.environ.get(args.token_env) if args.token_env else None,
    )
    if args.command == "stage":
        result = adapter.stage(args.attempt_id, args.text_file.read_text(), args.parent)
        private_json(args.output, result)
        print(
            json.dumps(
                {
                    "attempt_id": args.attempt_id,
                    "sha256": result["sha256"],
                    "preview": str(args.output),
                }
            )
        )
    elif args.command == "send":
        result = adapter.send(args.attempt_id, args.expect_sha256)
        print(json.dumps({key: result[key] for key in ("id", "state", "task", "context")}))
    elif args.command == "reconcile":
        result = adapter.reconcile(args.attempt_id)
        print(json.dumps({key: result[key] for key in ("id", "state", "task", "context")}))
    else:
        result = adapter.attempt(args.attempt_id)
        # This exports evidence only; it never upgrades an uncertain attempt or
        # accepts local JSON as a provider receipt. No credentials are persisted.
        private_json(
            args.output,
            {
                "schema_version": 1,
                "protocol": "a2a-0.3.0-jsonrpc",
                "controlled_test": approval.get("controlled_test", False),
                "organic_outreach": approval.get("organic_outreach", False),
                "card": adapter.card,
                "policy": adapter.policy,
                "approval": approval,
                "attempt": result,
                "exported_at": now(),
            },
        )
        print(json.dumps({"state": result["state"], "evidence": str(args.output)}))


if __name__ == "__main__":
    main()
