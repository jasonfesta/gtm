"""Manually served, loopback-only Portkey counterpart for protocol verification.

This is a controlled test agent, never organic outreach or a production route.
No scheduler or retry worker. Run in a foreground terminal and stop after testing.
"""

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from crm.portkey import complete


def now():
    return datetime.now(timezone.utc).isoformat()


class Counterpart:
    def __init__(self, path, credentials):
        self.path, self.credentials = Path(path), credentials
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, context TEXT, protocol TEXT, state TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, task TEXT, role TEXT, text TEXT, at TEXT, model TEXT)"
            )
        self.path.chmod(0o600)

    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def get(self, task_id):
        with self.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("unknown task")
            rows = db.execute(
                "SELECT * FROM messages WHERE task=? ORDER BY rowid", (task_id,)
            ).fetchall()
        return {
            "kind": "task",
            "id": task["id"],
            "contextId": task["context"],
            "status": {"state": task["state"]},
            "metadata": {
                "controlled_test": True,
                "organic_outreach": False,
                "protocol": task["protocol"],
            },
            "history": [
                {
                    "kind": "message",
                    "role": r["role"],
                    "messageId": r["id"],
                    "taskId": task["id"],
                    "contextId": task["context"],
                    "parts": [{"kind": "text", "text": r["text"]}],
                    "metadata": {"occurred_at": r["at"], "model": r["model"]},
                }
                for r in rows
            ],
        }

    def send(self, message, protocol):
        if message.get("role") != "user" or message.get("kind") != "message":
            raise ValueError("A2A user Message required")
        mid = message.get("messageId")
        parts = message.get("parts")
        if not isinstance(mid, str) or not mid or not isinstance(parts, list) or not parts:
            raise ValueError("messageId and text parts required")
        if any(p.get("kind") != "text" or not isinstance(p.get("text"), str) for p in parts):
            raise ValueError("only text parts supported")
        text = "\n".join(p["text"] for p in parts)
        if not text.strip() or len(text) > 4000:
            raise ValueError("message must be 1-4000 characters")
        task_id = message.get("taskId")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if existing:
                existing_task = db.execute(
                    "SELECT * FROM tasks WHERE id=?", (existing["task"],)
                ).fetchone()
                if (
                    existing["text"] != text
                    or existing_task["protocol"] != protocol
                    or (task_id and existing["task"] != task_id)
                    or (
                        message.get("contextId")
                        and existing_task["context"] != message["contextId"]
                    )
                ):
                    raise ValueError("message ID conflict")
                return self.get(existing["task"])
            if task_id:
                task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                if (
                    not task
                    or task["state"] != "input-required"
                    or task["protocol"] != protocol
                    or task["context"] != message.get("contextId")
                ):
                    raise ValueError("follow-up task/context/state mismatch")
                context = task["context"]
                db.execute("UPDATE tasks SET state=? WHERE id=?", ("working", task_id))
            else:
                if message.get("contextId"):
                    raise ValueError("new controlled conversation must omit contextId")
                task_id, context = str(uuid.uuid4()), str(uuid.uuid4())
                db.execute(
                    "INSERT INTO tasks VALUES (?,?,?,?)", (task_id, context, protocol, "working")
                )
            db.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?,?)",
                (mid, task_id, "user", text, now(), None),
            )
        transcript = self.get(task_id)["history"]
        try:
            response = complete(
                "You are the Darwin controlled protocol-test research agent. This is an authorized "
                "test conversation with the operator, not organic outreach. Answer the latest "
                "question concretely using conversation context. Do not claim to run Darwin queries "
                "or external actions. Return JSON with one nonempty key reply. Transcript: "
                + json.dumps(transcript),
                credentials=self.credentials,
                max_completion_tokens=350,
            )
            reply = json.loads(response["text"])["reply"]
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("empty agent reply")
            with self.connect() as db:
                db.execute(
                    "INSERT INTO messages VALUES (?,?,?,?,?,?)",
                    (str(uuid.uuid4()), task_id, "agent", reply, now(), response["model"]),
                )
                db.execute("UPDATE tasks SET state=? WHERE id=?", ("input-required", task_id))
        except Exception:
            # Persist the request; do not repeat a possibly completed model operation.
            with self.connect() as db:
                db.execute("UPDATE tasks SET state=? WHERE id=?", ("failed", task_id))
            raise
        return self.get(task_id)


def card(base):
    return {
        "protocolVersion": "0.3.0",
        "name": "Darwin controlled protocol-test research agent",
        "description": "Operator-owned Portkey-backed counterpart; controlled tests only, no organic outreach.",
        "url": base + "/a2a",
        "preferredTransport": "JSONRPC",
        "version": "1.0.0",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "protocol-test",
                "name": "Controlled conversational research",
                "description": "Discuss research-query design; no external actions.",
                "tags": ["controlled-test"],
            }
        ],
        "security": [],
        "securitySchemes": {},
        "documentationUrl": base + "/policy",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--page", type=Path)
    parser.add_argument("--portkey-credentials", type=Path, required=True)
    args = parser.parse_args()
    peer = Counterpart(args.db, args.portkey_credentials)
    base = f"http://127.0.0.1:{args.port}"
    page = (
        args.page
        or Path(__file__).resolve().parents[2] / "agents/agent-dm-agent/protocol-test/index.html"
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, value, status=200, content_type="application/json"):
            raw = value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def allowed(self):
            return (
                self.headers.get("Host") == f"127.0.0.1:{args.port}"
                and self.headers.get("Origin", base) == base
            )

        def do_GET(self):
            if not self.allowed():
                return self.respond({"error": "origin rejected"}, 403)
            if self.path == "/.well-known/agent-card.json":
                return self.respond(card(base))
            if self.path == "/policy":
                return self.respond(
                    {
                        "identity": card(base)["name"],
                        "agent_operated": True,
                        "controlled_test": True,
                        "organic_outreach": False,
                        "interaction_policy": "Jason authorized a controlled counterpart protocol test in GTM-692.",
                        "owner_approval_required": "no",
                        "authentication": "No authentication: loopback-only test; no private user data permitted.",
                        "allowed_protocols": ["a2a-0.3.0-jsonrpc", "webmcp"],
                        "model_route": "portkey",
                    }
                )
            if self.path == "/":
                return self.respond(page.read_text(), content_type="text/html; charset=utf-8")
            return self.respond({"error": "not found"}, 404)

        def do_POST(self):
            if not self.allowed():
                return self.respond({"error": "origin rejected"}, 403)
            rpc_id = None
            try:
                size = int(self.headers.get("Content-Length", 0))
                if size < 1 or size > 20000:
                    raise ValueError("invalid request length")
                data = json.loads(self.rfile.read(size))
                if self.path == "/a2a":
                    rpc_id = data.get("id")
                    if data.get("jsonrpc") != "2.0":
                        raise ValueError("JSON-RPC 2.0 required")
                    params = data.get("params") or {}
                    if data.get("method") == "message/send":
                        value = peer.send(params["message"], "a2a")
                    elif data.get("method") == "tasks/get":
                        value = peer.get(params["id"])
                        if value["metadata"]["protocol"] != "a2a":
                            raise ValueError("protocol mismatch")
                    else:
                        return self.respond(
                            {
                                "jsonrpc": "2.0",
                                "id": rpc_id,
                                "error": {"code": -32601, "message": "method not found"},
                            }
                        )
                    return self.respond({"jsonrpc": "2.0", "id": rpc_id, "result": value})
                if self.path == "/webmcp/send":
                    return self.respond(peer.send(data, "webmcp"))
                if self.path == "/webmcp/read":
                    task = peer.get(data["taskId"])
                    if task["metadata"]["protocol"] != "webmcp":
                        raise ValueError("protocol mismatch")
                    return self.respond(task)
                return self.respond({"error": "not found"}, 404)
            except Exception as error:
                # Never expose credentials or gateway payloads.
                if self.path == "/a2a":
                    return self.respond(
                        {
                            "jsonrpc": "2.0",
                            "id": rpc_id,
                            "error": {"code": -32603, "message": type(error).__name__},
                        }
                    )
                return self.respond({"error": type(error).__name__}, 400)

    print(
        json.dumps({"url": base, "controlled_test": True, "manual_foreground_server": True}),
        flush=True,
    )
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
