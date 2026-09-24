"""CRM X handles vs the X Pro CRM list column. Does not follow or send."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from crm.database import ROOT, connect

LIST_ID = "2098128306927182019"
LIST_NAME = "reads_"
LIST_URL = f"https://x.com/i/lists/{LIST_ID}"
MEMBERS_URL = f"https://x.com/i/lists/{LIST_ID}/members"
DECK_URL = "https://pro.x.com/i/decks/1990937645631799520"
STATE = ROOT / "logs/daily-gtm-chat/x-crm-list.json"
HANDLE_RE = re.compile(r"(?:x\.com|twitter\.com)/@?([A-Za-z0-9_]{1,15})\b", re.I)
AT_RE = re.compile(r"^@?([A-Za-z0-9_]{1,15})$")
BATCH = 12


def _now():
    return datetime.now(timezone.utc).isoformat()


def handle_from_contact(value="", normalized=""):
    """Exact X handle from a CRM contact value. Empty when the URL is not a profile."""
    for text in (value, normalized):
        raw = str(text or "").strip()
        if not raw:
            continue
        match = HANDLE_RE.search(raw)
        if match:
            return match.group(1)
        match = AT_RE.match(raw.lstrip("/"))
        if match:
            return match.group(1)
    return ""


def roster(*, connection=None):
    """Every CRM person with an X contact, newest identity first."""
    own = connection is None
    conn = connection or connect()
    try:
        rows = conn.execute(
            "SELECT p.person_id, p.full_name, c.value, c.normalized_value, "
            "c.verification_status FROM contact_points c "
            "JOIN people p ON p.person_id=c.person_id "
            "WHERE c.contact_type='x' ORDER BY p.full_name"
        ).fetchall()
    finally:
        if own and hasattr(conn, "close"):
            conn.close()
    people = []
    seen = set()
    for row in rows:
        mapping = dict(row) if not isinstance(row, dict) else row
        handle = handle_from_contact(mapping.get("value"), mapping.get("normalized_value"))
        key = handle.casefold() if handle else mapping.get("person_id")
        if key in seen:
            continue
        seen.add(key)
        people.append(
            {
                "person_id": mapping.get("person_id"),
                "name": mapping.get("full_name"),
                "handle": handle,
                "profile_url": f"https://x.com/{handle}" if handle else mapping.get("value") or "",
                "verification_status": mapping.get("verification_status"),
            }
        )
    return people


def coverage(members, *, people=None, connection=None):
    """Diff CRM X handles against the live CRM list membership."""
    people = people if people is not None else roster(connection=connection)
    listed = {str(handle).lstrip("@").casefold() for handle in members if handle}
    with_handle = [row for row in people if row.get("handle")]
    present = [row for row in with_handle if row["handle"].casefold() in listed]
    missing = [row for row in with_handle if row["handle"].casefold() not in listed]
    no_handle = [row for row in people if not row.get("handle")]
    extra = sorted(listed - {row["handle"].casefold() for row in with_handle})
    return {
        "list_id": LIST_ID,
        "list_name": LIST_NAME,
        "list_url": LIST_URL,
        "members_url": MEMBERS_URL,
        "deck_url": DECK_URL,
        "crm_x_people": len(people),
        "listed": len(listed),
        "present": len(present),
        "missing": len(missing),
        "no_handle": len(no_handle),
        "extra_on_list": extra,
        "missing_people": missing,
        "extra_people": extra,
        "complete": not missing and not extra,
        "matched": not missing and not extra,
    }


def _state(path=STATE):
    path = Path(path)
    if not path.is_file():
        return {"list_id": LIST_ID, "members": [], "added": []}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {"list_id": LIST_ID, "members": [], "added": []}
    if not isinstance(loaded, dict):
        return {"list_id": LIST_ID, "members": [], "added": []}
    loaded.setdefault("members", [])
    loaded.setdefault("added", [])
    loaded.setdefault("removed", [])
    loaded.setdefault("pending_add", [])
    return loaded


def note_crm_x_contact(handle, *, path=STATE, person_id="", name=""):
    """A CRM X write updates reads. The watcher still syncs the live list."""
    folded = handle_from_contact(handle) or str(handle or "").strip().lstrip("@")
    if not folded:
        return None
    payload = _state(path)
    listed = {str(h).lstrip("@").casefold() for h in payload.get("members") or [] if h}
    if folded.casefold() in listed:
        return payload
    pending = payload.setdefault("pending_add", [])
    seen = {str(h).lstrip("@").casefold() for h in pending if h}
    if folded.casefold() not in seen:
        pending.append(folded)
        payload["crm_updated_at"] = _now()
        if person_id or name:
            payload.setdefault("pending_meta", {})
            payload["pending_meta"][folded] = {
                "person_id": person_id,
                "name": name,
                "noted_at": payload["crm_updated_at"],
            }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def record_members(members, *, path=STATE, added=(), removed=(), replace=False):
    payload = _state(path)
    payload["list_id"] = LIST_ID
    payload["checked_at"] = _now()
    incoming = [str(h).lstrip("@") for h in members if h]
    if replace:
        payload["members"] = sorted(incoming, key=str.casefold)
    else:
        existing = {str(h).lstrip("@") for h in payload.get("members") or [] if h}
        drop = {str(h).lstrip("@").casefold() for h in removed if h}
        kept = {h for h in existing if h.casefold() not in drop}
        payload["members"] = sorted(kept | set(incoming), key=str.casefold)
    listed_fold = {h.casefold() for h in payload["members"]}
    payload["pending_add"] = [
        h
        for h in payload.get("pending_add") or []
        if str(h).lstrip("@").casefold() not in listed_fold
    ]
    if added:
        payload.setdefault("added", [])
        seen = {h.casefold() for h in payload["added"]}
        for handle in added:
            folded = str(handle).lstrip("@")
            if folded and folded.casefold() not in seen:
                payload["added"].append(folded)
                seen.add(folded.casefold())
    if removed:
        payload.setdefault("removed", [])
        seen = {h.casefold() for h in payload["removed"]}
        for handle in removed:
            folded = str(handle).lstrip("@")
            if folded and folded.casefold() not in seen:
                payload["removed"].append(folded)
                seen.add(folded.casefold())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def record_membership_readback(readback, *, path=STATE):
    """Persist an exact, independently verified provider membership readback."""
    from crm.browser_adapter import ReadsMembershipReadback

    if not isinstance(readback, ReadsMembershipReadback):
        raise TypeError("reads membership readback required")
    if not readback.applied:
        raise ValueError("membership mutation was not verified by provider readback")
    mutation = readback.mutation
    return record_members(
        readback.observation.handles,
        path=path,
        added=(mutation.handle,) if mutation.operation == "add" else (),
        removed=(mutation.handle,) if mutation.operation == "remove" else (),
        replace=True,
    )


def next_missing(members, *, limit=None, people=None, connection=None):
    """Handles to add so the list matches CRM. List membership is not a follow."""
    return sync(members, limit=limit, people=people, connection=connection)


def sync(members, *, limit=None, people=None, connection=None, path=STATE):
    """Full add/remove set so reads matches CRM X contacts exactly."""
    result = coverage(members, people=people, connection=connection)
    missing = result["missing_people"]
    pending = {str(h).lstrip("@").casefold() for h in _state(path).get("pending_add") or [] if h}
    if pending:
        missing = sorted(
            missing,
            key=lambda row: 0 if (row.get("handle") or "").casefold() in pending else 1,
        )
    if limit is not None:
        missing = missing[: max(0, int(limit))]
    return {
        **{
            k: result[k]
            for k in (
                "list_id",
                "list_url",
                "members_url",
                "present",
                "missing",
                "complete",
                "matched",
            )
        },
        "add": missing,
        "remove": result["extra_on_list"],
        "batch": None if limit is None else max(0, int(limit)),
    }


def status(*, members=None, path=STATE, connection=None):
    stored = _state(path)
    listed = members if members is not None else stored.get("members") or []
    result = coverage(listed, connection=connection)
    result["checked_at"] = stored.get("checked_at")
    result["stored_members"] = len(listed)
    nxt = sync(listed, connection=connection)
    result["add_now"] = nxt["add"]
    result["remove_now"] = nxt["remove"]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "missing", "sync", "record", "note"))
    parser.add_argument("--members", default="")
    parser.add_argument("--added", default="")
    parser.add_argument("--removed", default="")
    parser.add_argument("--handle", default="")
    parser.add_argument("--name", default="")
    parser.add_argument("--person-id", default="")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    members = [part.strip().lstrip("@") for part in args.members.split(",") if part.strip()]
    if args.command == "note":
        print(
            json.dumps(
                note_crm_x_contact(args.handle, person_id=args.person_id, name=args.name),
                indent=2,
            )
        )
        return
    if args.command == "record":
        added = [part.strip().lstrip("@") for part in args.added.split(",") if part.strip()]
        removed = [part.strip().lstrip("@") for part in args.removed.split(",") if part.strip()]
        print(
            json.dumps(
                record_members(members, added=added, removed=removed, replace=args.replace),
                indent=2,
            )
        )
        return
    if args.command in ("missing", "sync"):
        print(
            json.dumps(
                sync(members or _state()["members"], limit=args.limit),
                indent=2,
            )
        )
        return
    print(json.dumps(status(members=members or None), indent=2))


if __name__ == "__main__":
    main()
