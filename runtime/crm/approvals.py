"""Day-chat send queue. Main lists each message; watchers send only after yes."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from crm.database import ROOT
from crm.live_channels import hold as channel_hold
from crm.send_mode import status as mode_status

NY = ZoneInfo("America/New_York")
QUEUE = ROOT / "logs/daily-gtm-chat/approvals"
KINDS = ("comment", "dm", "connect")
CHANNELS = ("x", "linkedin")
REQUIRED = ("kind", "channel", "name", "text", "parent_url", "parent_text", "profile_url")


def _now():
    return datetime.now(timezone.utc).isoformat()


def day_stamp(now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(NY).strftime("%Y-%m-%d")


def _path(day, *, queue=QUEUE):
    return Path(queue) / f"{day}.json"


def _load(day, *, queue=QUEUE):
    path = _path(day, queue=queue)
    if not path.is_file():
        return {"day": day, "items": []}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"day": day, "items": []}
    items = loaded.get("items") if isinstance(loaded, dict) else None
    return {"day": day, "items": items if isinstance(items, list) else []}


def _save(payload, *, queue=QUEUE):
    path = _path(payload["day"], queue=queue)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _item(row):
    kind = row.get("kind")
    channel = row.get("channel")
    if kind not in KINDS:
        raise ValueError("kind must be comment, dm, or connect")
    if channel not in CHANNELS:
        raise ValueError("channel must be x or linkedin")
    missing = [key for key in REQUIRED if not str(row.get(key) or "").strip()]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    return {
        "id": row.get("id") or uuid.uuid4().hex[:10],
        "kind": kind,
        "channel": channel,
        "name": str(row["name"]).strip(),
        "profile_url": str(row["profile_url"]).strip(),
        "parent_url": str(row["parent_url"]).strip(),
        "parent_text": str(row["parent_text"]).strip(),
        "text": str(row["text"]).strip(),
        "second_text": str(row.get("second_text") or "").strip(),
        "score": row.get("score") if isinstance(row.get("score"), dict) else {},
        "status": "pending",
        "queued_at": _now(),
        "decided_at": None,
        "sent_at": None,
        "reason": "",
    }


def propose(row, *, day=None, queue=QUEUE, mode_state=None):
    """Queue the send, or return send=True when setup chose automatic."""
    day = day or day_stamp()
    paused = channel_hold(row.get("channel"))
    if paused:
        return {**paused, "queued": False, "id": None, "item": None}
    mode = mode_status(state=mode_state) if mode_state is not None else mode_status()
    item = _item(row)
    if mode["send_live"]:
        return {
            "send": True,
            "queued": False,
            "id": None,
            "mode": "automatic",
            "item": item,
        }
    payload = _load(day, queue=queue)
    payload["items"].append(item)
    path = _save(payload, queue=queue)
    return {
        "send": False,
        "queued": True,
        "id": item["id"],
        "mode": mode["mode"] or "unset",
        "path": str(path),
        "item": item,
    }


def pending(day=None, *, queue=QUEUE):
    day = day or day_stamp()
    items = [row for row in _load(day, queue=queue)["items"] if row.get("status") == "pending"]
    return {"day": day, "count": len(items), "items": items}


def ready_to_send(day=None, *, queue=QUEUE):
    day = day or day_stamp()
    items = [row for row in _load(day, queue=queue)["items"] if row.get("status") == "approved"]
    return {"day": day, "count": len(items), "items": items}


def show(item_id=None, day=None, *, queue=QUEUE):
    day = day or day_stamp()
    payload = _load(day, queue=queue)
    if not item_id:
        return payload
    for row in payload["items"]:
        if row.get("id") == item_id:
            return {"day": day, "item": row}
    raise ValueError("unknown approval id")


def decide(item_id, approved, *, day=None, queue=QUEUE, reason=""):
    day = day or day_stamp()
    payload = _load(day, queue=queue)
    for row in payload["items"]:
        if row.get("id") != item_id:
            continue
        if row.get("status") != "pending":
            raise ValueError(f"already {row.get('status')}")
        row["status"] = "approved" if approved else "rejected"
        row["decided_at"] = _now()
        row["reason"] = reason
        _save(payload, queue=queue)
        return {"day": day, "item": row, "send": approved}
    raise ValueError("unknown approval id")


def mark_sent(item_id, *, day=None, queue=QUEUE, reply_url=""):
    day = day or day_stamp()
    payload = _load(day, queue=queue)
    for row in payload["items"]:
        if row.get("id") != item_id:
            continue
        if row.get("status") != "approved":
            raise ValueError("only an approved item can be marked sent")
        row["status"] = "sent"
        row["sent_at"] = _now()
        if reply_url:
            row["reply_url"] = reply_url
        _save(payload, queue=queue)
        return {"day": day, "item": row}
    raise ValueError("unknown approval id")


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("propose", "pending", "ready", "show", "approve", "reject", "sent"),
    )
    parser.add_argument("--id")
    parser.add_argument("--day")
    parser.add_argument("--kind")
    parser.add_argument("--channel")
    parser.add_argument("--name")
    parser.add_argument("--profile-url")
    parser.add_argument("--parent-url")
    parser.add_argument("--parent-text")
    parser.add_argument("--text")
    parser.add_argument("--second-text", default="")
    parser.add_argument("--reply-url", default="")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    if args.command == "pending":
        print(json.dumps(pending(args.day), indent=2))
        return
    if args.command == "ready":
        print(json.dumps(ready_to_send(args.day), indent=2))
        return
    if args.command == "show":
        print(json.dumps(show(args.id, args.day), indent=2))
        return
    if args.command == "propose":
        print(
            json.dumps(
                propose(
                    {
                        "kind": args.kind,
                        "channel": args.channel,
                        "name": args.name,
                        "profile_url": args.profile_url,
                        "parent_url": args.parent_url,
                        "parent_text": args.parent_text,
                        "text": args.text,
                        "second_text": args.second_text,
                    },
                    day=args.day,
                ),
                indent=2,
            )
        )
        return
    if not args.id:
        raise SystemExit("--id is required")
    if args.command == "sent":
        print(json.dumps(mark_sent(args.id, day=args.day, reply_url=args.reply_url), indent=2))
        return
    print(
        json.dumps(
            decide(args.id, args.command == "approve", day=args.day, reason=args.reason),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
