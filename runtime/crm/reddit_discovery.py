"""Offline Reddit qualification and Human Reply handoffs; never sends outreach."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from crm.human_discovery import PERSONAL_ACTION, WORK_CONTEXT, relevant_live_observation

THREAD = re.compile(r"/r/([A-Za-z0-9_]+)/comments/([a-z0-9]+)(?:/[^/?#]*)?/?")
ACCOUNT = re.compile(r"[A-Za-z0-9_-]{3,20}")


def thread_identity(value):
    value = str(value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        value = "https://www.reddit.com" + value
    try:
        parsed = urlsplit(value)
        match = THREAD.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in {
                "reddit.com",
                "www.reddit.com",
                "old.reddit.com",
                "new.reddit.com",
            }
            or parsed.username
            or parsed.password
            or parsed.port
            or not match
        ):
            return None
    except ValueError:
        return None
    return "https://www.reddit.com" + parsed.path, match[2]


def observation(raw, run_id):
    """Require provider-observed author and genuine thread, never invent identity."""
    author = raw.get("author") or raw.get("authorUsername") or raw.get("username") or ""
    if isinstance(author, dict):
        author = author.get("username") or author.get("name") or ""
    author = str(author).removeprefix("u/").strip()
    if not ACCOUNT.fullmatch(author) or author.lower() in {"automoderator", "deleted"}:
        return None, "invalid_account"
    thread = next(
        (found for field in ("permalink", "url") if (found := thread_identity(raw.get(field)))),
        None,
    )
    if not thread:
        return None, "invalid_thread"
    title = str(raw.get("title") or "")
    body = str(raw.get("selftext") or raw.get("body") or raw.get("text") or "")
    text = (title + "\n" + body).strip()
    item = {
        "channel": "reddit",
        "name": author,
        "account": author,
        "profile_url": f"https://www.reddit.com/user/{author}/",
        "parent_url": thread[0],
        "provider_id": thread[1],
        "title": title,
        "text": text,
        "provider_run_id": run_id,
        "person_key": "reddit:" + author.lower(),
        "action_key": "reddit:" + author.lower() + ":" + thread[1],
    }
    if not relevant_live_observation(item):
        return None, "no_tool_work_context"
    if not WORK_CONTEXT.search(text) or not PERSONAL_ACTION.search(text):
        return None, "no_personal_work_evidence"
    return item, None


def qualify(rows, *, run_id, previous=(), provider_status="UNKNOWN"):
    """Previous qualified items suppress account/thread duplicates across retries."""
    if not isinstance(rows, list):
        raise ValueError("Expected a list of provider observations")
    seen = {row["action_key"] for row in previous}
    qualified, rejected = [], Counter()
    for raw in rows:
        if not isinstance(raw, dict):
            rejected["invalid_row"] += 1
            continue
        item, reason = observation(raw, run_id)
        if reason:
            rejected[reason] += 1
        elif item["action_key"] in seen:
            rejected["duplicate_action"] += 1
        else:
            seen.add(item["action_key"])
            qualified.append(item)
    return {
        "provider_run_id": run_id,
        "provider_status": provider_status,
        "partial": provider_status != "SUCCEEDED",
        "raw_count": len(rows),
        "qualified_count": len(qualified),
        "distinct_people": len({item["person_key"] for item in qualified}),
        "rejected": dict(rejected),
        "items": qualified,
    }


def reply_handoff(batch):
    return {
        "owner": "human_reply",
        "channel": "reddit",
        "provider_run_id": batch["provider_run_id"],
        "provider_status": batch["provider_status"],
        "partial": batch["partial"],
        "items": batch["items"],
        "delivery_contract": {
            "dedup_key": "action_key",
            "before_send": "Persist action_key, sender_account, exact body and attempt time in the owner ledger.",
            "uncertain_send": "Mark uncertain; read back the exact thread for the sender account and exact body. Do not resend while unresolved.",
            "confirmed_send": "Save observed comment permalink and sender account in the owner ledger.",
            "no_match": "An empty or incomplete readback does not establish that a send failed. Keep uncertain.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--provider-status", required=True)
    parser.add_argument("--previous", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    previous = [item for path in args.previous for item in json.loads(path.read_text())["items"]]
    batch = qualify(
        json.loads(args.input.read_text()),
        run_id=args.run_id,
        previous=previous,
        provider_status=args.provider_status,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [("reddit.qualified.json", batch), ("reddit.human-reply.json", reply_handoff(batch))]
    for name, _ in outputs:
        if (args.output_dir / name).exists():
            raise FileExistsError(f"Refusing to overwrite evidence: {args.output_dir / name}")
    for name, data in outputs:
        destination = args.output_dir / name
        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite evidence: {destination}")
        destination.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps({key: value for key, value in batch.items() if key != "items"}))


if __name__ == "__main__":
    main()
