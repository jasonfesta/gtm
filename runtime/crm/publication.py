"""Record confirmed public replies into shared CRM. No sending."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from crm.contact_normalization import norm
from crm.database import ROOT, configured, connect
from crm.outreach_queue import CRMHistory
from crm.reply_locations import ReplyLocations, public_url
from crm.timeline_intake import CONTACT_KIND, add_reviewed_person
from crm.x_crm_column import note_crm_x_contact

PENDING = ROOT / "logs/publication-receipts/pending"
PROCESSED = ROOT / "logs/publication-receipts/processed"
HELD = ROOT / "logs/publication-receipts/held"
X_REPLY = re.compile(r"https://x\.com/jasonfesta/status/(\d+)")
X_STATUS = re.compile(r"https://x\.com/([A-Za-z0-9_]{1,15})/status/(\d+)")
LI_PERSON = re.compile(r"(person_[0-9a-f]+)", re.I)
LI_REPLY_ID = re.compile(
    r"(?:dashReplyUrn=urn%3Ali%3Afsd_comment%3A%28|commentUrn=urn%3Ali%3Acomment%3A%28activity%3A\d+%2C)(\d+)"
)
LI_COMMENT_ID = re.compile(r"dashCommentUrn=urn%3Ali%3Afsd_comment%3A%28(\d+)")
SENDER = {
    "x": ("https://x.com/jasonfesta", "x:jasonfesta"),
    "linkedin": ("https://www.linkedin.com/in/jasonfesta/", "linkedin:jasonfesta"),
}
LATER_CHANNELS = ("hacker_news", "github", "product_hunt", "apollo")
SOURCE_TYPE = {
    "x": "x",
    "linkedin": "linkedin",
    "hacker_news": "news",
    "github": "official_site",
    "product_hunt": "other",
    "apollo": "other",
}


def _readback(connection, person_id, name):
    row = connection.execute(
        "SELECT full_name FROM people WHERE person_id=?", (person_id,)
    ).fetchone()
    if not row or row["full_name"].casefold().strip() != name.casefold().strip():
        raise RuntimeError("CRM person readback not confirmed")


def record(receipt, *, shared_factory=None, locations=None, history=None):
    """Admit the person if needed, track the published permalink, record outreach."""
    hold = receipt.get("hold")
    name = receipt["name"].strip()
    channel = receipt["channel"]
    profile = (receipt.get("profile_url") or "").strip()
    observed = receipt["observed_at"]
    evidence = receipt.get("evidence") or receipt.get("task_evidence")
    later = hold in ("later_use", "search_hit") and channel in SOURCE_TYPE
    if later:
        if not name or not evidence:
            raise ValueError("named search-hit receipt with evidence required")
        sender, account = None, None
    elif channel not in SENDER or not name or not evidence:
        raise ValueError("named x/linkedin receipt with evidence required")
    else:
        sender, account = SENDER[channel]
        if receipt.get("sender") not in (None, sender) or receipt.get("account") not in (
            None,
            account,
        ):
            raise ValueError("verified operator sender required")
    sources = receipt.get("sources") or (
        [{"url": receipt["parent_url"], "type": channel}] if receipt.get("parent_url") else []
    )
    sources = [
        {**source, "type": SOURCE_TYPE.get(source.get("type"), source.get("type") or "other")}
        for source in sources
    ]
    authority = {
        "kind": "explicit_person_add" if hold else "confirmed_public_reply",
        "evidence": evidence,
        "confirmed": True,
        "published_url": receipt.get("reply_url") or profile,
        "sender": sender,
        "text": receipt.get("text") or "",
    }
    if not hold and (
        not receipt.get("reply_url")
        or not receipt.get("parent_url")
        or not receipt.get("provider_id")
        or not receipt.get("text", "").strip()
        or receipt.get("confirmation") != "provider_readback"
        or receipt.get("status") != "confirmed"
    ):
        raise ValueError("confirmed published permalink, parent, provider id and text required")
    if hold and not sources:
        sources = [{"url": profile, "type": channel}]
    person_record = {
        "name": name,
        "channel": channel,
        "profile_url": profile,
        "role": receipt.get("role"),
        "observed_at": observed,
        "sources": sources,
        "authority": authority,
    }
    if shared_factory is None:
        if not configured():
            raise RuntimeError("shared PostgreSQL required; no legacy fallback")
        shared_factory = connect
    shared = shared_factory()
    try:
        with shared:
            if not profile and receipt.get("person_id"):
                row = shared.execute(
                    "SELECT value FROM contact_points WHERE person_id=? AND contact_type=?",
                    (receipt["person_id"], CONTACT_KIND.get(channel, channel)),
                ).fetchone()
                if not row:
                    raise ValueError("existing person has no matching public profile")
                profile = row["value"]
                person_record["profile_url"] = profile
                sources = person_record["sources"] or [{"url": profile, "type": channel}]
                person_record["sources"] = sources
            if profile:
                kind = CONTACT_KIND.get(channel, channel)
                existing = shared.execute(
                    "SELECT p.person_id, p.full_name FROM contact_points cp"
                    " JOIN people p USING(person_id)"
                    " WHERE cp.contact_type=? AND cp.normalized_value=?",
                    (kind, norm(kind, profile)),
                ).fetchone()
                if existing:
                    expected = receipt.get("person_id")
                    if expected and expected != existing["person_id"]:
                        raise ValueError("person_id does not match contact identity")
                    name = existing["full_name"]
                    person_record["name"] = name
            admitted = add_reviewed_person(shared, person_record)
            person_id = admitted["person_id"]
            expected = receipt.get("person_id")
            if expected and expected != person_id:
                raise ValueError("person_id does not match contact identity")
            _readback(shared, person_id, name)
            task_id = None
            queued = False
            if hold == "search_hit" and not admitted["existing"]:
                task_id = (
                    "task_hourly_search_"
                    + hashlib.sha256((channel + ":" + profile).encode()).hexdigest()[:24]
                )
                shared.execute(
                    "INSERT OR IGNORE INTO research_tasks"
                    "(task_id,entity_type,entity_id,task_type,status,priority,notes) "
                    "VALUES (?,'person',?,'verify_identity','queued',80,?)",
                    (
                        task_id,
                        person_id,
                        "Hourly search lead; verify identity and agent/assistant developer fit.",
                    ),
                )
                queued = True
    finally:
        shared.close()
    if channel == "x":
        note_crm_x_contact(profile, person_id=person_id, name=name)
    if hold:
        return {
            "person_id": person_id,
            "existing": admitted["existing"],
            "hold": hold,
            "tracked": False,
            "outreach": False,
            "task_id": task_id,
            "queued": queued,
        }
    public_url(receipt["reply_url"], channel)
    public_url(receipt["parent_url"], channel)
    facts = {
        "channel": channel,
        "account": account,
        "account_profile_url": sender,
        "author_profile_url": sender,
        "provider_id": receipt["provider_id"],
        "reply_url": receipt["reply_url"],
        "parent_url": receipt["parent_url"],
        "conversation_url": receipt.get("conversation_url") or receipt["parent_url"],
        "person_id": person_id,
        "observed_at": observed,
        "posted_at": receipt.get("posted_at"),
        "confirmation": "provider_readback",
        "status": "confirmed",
        "task_evidence": receipt.get("task_evidence") or evidence,
        "lane": receipt.get("lane") or "unknown",
    }
    tracker = locations or ReplyLocations()
    location_id = tracker.track(facts, evidence, shared_factory=shared_factory)
    event = {
        "event_id": "timeline_public_" + receipt["provider_id"],
        "person_id": person_id,
        "channel": channel,
        "direction": "outbound",
        "occurred_at": observed,
        "outcome": "sent",
        "external_reference": receipt["reply_url"],
    }
    recorder = history or CRMHistory()
    recorder.record(event)
    return {
        "person_id": person_id,
        "existing": admitted["existing"],
        "location_id": location_id,
        "event_id": event["event_id"],
        "tracked": True,
        "outreach": True,
        "hold": None,
    }


def _move(path, folder, payload=None):
    folder.mkdir(parents=True, exist_ok=True)
    path = Path(path)
    dest = folder / path.name
    if dest.resolve() != path.resolve():
        if dest.exists():
            if path.exists():
                path.unlink()
        elif path.exists():
            shutil.move(str(path), str(dest))
        elif not dest.exists():
            raise FileNotFoundError(str(path))
    if payload is not None:
        dest.with_suffix(dest.suffix + ".result.json").write_text(
            json.dumps(payload, indent=2) + "\n"
        )
    return dest


def ingest(paths=None, *, pending=PENDING, processed=PROCESSED, held=HELD, **record_kwargs):
    """Process JSON receipts. Complete sends write CRM; holds admit identity only."""
    pending.mkdir(parents=True, exist_ok=True)
    files = [Path(p) for p in paths] if paths else sorted(pending.glob("*.json"))
    results = []
    for path in files:
        if path.name.endswith(".result.json"):
            continue
        receipt = json.loads(path.read_text())
        try:
            result = record(receipt, **record_kwargs)
            dest = processed if not result.get("hold") else held
            results.append(
                {
                    "path": str(_move(path, dest, result)),
                    "status": "held" if result.get("hold") else "recorded",
                    **result,
                }
            )
        except Exception as exc:
            dest = processed / path.name if (processed / path.name).exists() else held / path.name
            if path.exists() and dest.resolve() != path.resolve():
                dest = _move(path, dest.parent, {"error": type(exc).__name__, "detail": str(exc)})
            elif dest.exists():
                dest.with_suffix(dest.suffix + ".result.json").write_text(
                    json.dumps({"error": type(exc).__name__, "detail": str(exc)}, indent=2) + "\n"
                )
            results.append(
                {
                    "path": str(dest),
                    "status": "held",
                    "error": type(exc).__name__,
                }
            )
    return results


def receipts_from_cycle(text):
    """Extract publication facts from watcher cycle or send receipts. Incomplete rows stay held."""
    x_receipts = _receipts_from_x(text)
    if x_receipts:
        return x_receipts
    return _receipts_from_linkedin(text)


def _receipts_from_x(text):
    """Extract X publication facts from a watcher cycle receipt. Incomplete rows stay held."""
    replies = list(X_REPLY.finditer(text))
    if not replies:
        return []
    person = None
    named = re.search(r"(?im)^-\s*person:\s*(.+?)\s+(\S+)\s+@([A-Za-z0-9_]{1,15})\s*$", text)
    if named:
        person = {
            "name": named.group(1).strip(),
            "person_id": named.group(2),
            "handle": named.group(3),
        }
    else:
        tagged = re.search(r"(?im)^-\s*person:\s*@([A-Za-z0-9_]{1,15})\s+(.+)$", text)
        if tagged:
            person = {
                "name": re.split(r"\s+\(", tagged.group(2).strip(), maxsplit=1)[0].strip(),
                "handle": tagged.group(1),
            }
    receipts = []
    for index, reply in enumerate(replies):
        chunk = text[
            reply.start() : replies[index + 1].start() if index + 1 < len(replies) else len(text)
        ]
        parent = None
        for match in X_STATUS.finditer(chunk):
            if match.group(1).casefold() != "jasonfesta":
                parent = match
                break
        if parent is None:
            for match in X_STATUS.finditer(text):
                if match.group(1).casefold() != "jasonfesta":
                    parent = match
                    break
        body = None
        found = re.search(r"(?im)^\s*(?:-\s*)?text:\s*(.+)$", chunk)
        if found:
            body = found.group(1).strip()
        handle = (person or {}).get("handle") or (parent.group(1) if parent else None)
        name = (person or {}).get("name") or handle
        if len(replies) > 1:
            name = parent.group(1) if parent else handle
            handle = parent.group(1) if parent else handle
        profile = "https://x.com/" + handle if handle else None
        hold = None
        if not parent or not name or not profile or not body:
            hold = "incomplete_publication_facts"
        receipt = {
            "receipt_id": "pub-x-" + reply.group(1),
            "channel": "x",
            "name": name or "unknown",
            "profile_url": profile or "https://x.com/jasonfesta",
            "reply_url": reply.group(0),
            "parent_url": parent.group(0) if parent else "",
            "conversation_url": parent.group(0) if parent else "",
            "provider_id": reply.group(1),
            "text": body or "",
            "sender": SENDER["x"][0],
            "account": SENDER["x"][1],
            "confirmation": "provider_readback",
            "status": "confirmed",
            "hold": hold,
        }
        if person and person.get("person_id") and len(replies) == 1:
            receipt["person_id"] = person["person_id"]
        receipts.append(receipt)
    return receipts


def _field(text, name):
    found = re.search(rf"(?im)^-\s*{name}:\s*(.+)$", text)
    return found.group(1).strip() if found else ""


def _receipts_from_linkedin(text):
    """Extract LinkedIn send facts. Missing comment permalinks stay held."""
    if not re.search(r"(?im)^-\s*channel:\s*linkedin\s*$", text):
        return []
    if not re.search(r"(?im)^-\s*(?:permalink|posted|sends|text):", text):
        return []
    permalink = _field(text, "permalink")
    if not permalink.startswith("https://www.linkedin.com/"):
        posted = _field(text, "posted")
        permalink = posted if posted.startswith("https://www.linkedin.com/") else ""
    thread = _field(text, "thread")
    if not thread.startswith("https://www.linkedin.com/"):
        incoming = _field(text, "incoming")
        thread = incoming if incoming.startswith("https://www.linkedin.com/") else ""
    body = _field(text, "text")
    person_line = _field(text, "person")
    person_id = None
    found_person = LI_PERSON.search(person_line)
    if found_person:
        person_id = found_person.group(1)
    name = re.split(r"\s+\(|\s+person_", person_line, maxsplit=1)[0].strip() if person_line else ""
    profile = _field(text, "profile") or _field(text, "profile_url")
    if not profile.startswith("https://www.linkedin.com/in/"):
        found_profile = re.search(r"https://www\.linkedin\.com/in/[A-Za-z0-9_-]+/?", text)
        profile = found_profile.group(0) if found_profile else ""
    provider_id = None
    if permalink:
        found_id = LI_REPLY_ID.search(permalink) or LI_COMMENT_ID.search(permalink)
        provider_id = found_id.group(1) if found_id else None
    hold = None
    if not permalink or not provider_id:
        hold = "comment_permalink_not_captured"
    elif not name or not body or not thread:
        hold = "incomplete_publication_facts"
    receipt_id = "pub-linkedin-" + (
        provider_id or re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    )
    header = re.search(r"(20\d{2}-\d{2}-\d{2}T\d{2}:\d{2}Z)", text)
    if hold and not provider_id and header:
        receipt_id = receipt_id + "-" + header.group(1).replace("-", "").replace(":", "")
    receipt = {
        "receipt_id": receipt_id,
        "channel": "linkedin",
        "name": name or "unknown",
        "profile_url": profile,
        "reply_url": permalink,
        "parent_url": thread,
        "conversation_url": thread,
        "provider_id": provider_id or "",
        "text": body,
        "sender": SENDER["linkedin"][0],
        "account": SENDER["linkedin"][1],
        "confirmation": "provider_readback",
        "status": "confirmed",
        "hold": hold,
    }
    if person_id:
        receipt["person_id"] = person_id
    if not profile and thread:
        receipt["sources"] = [{"url": thread, "type": "linkedin"}]
    return [receipt]


def _known_receipts(pending, processed, held):
    known = set()
    for folder in (pending, processed, held):
        if not folder.is_dir():
            continue
        for path in folder.glob("*.json"):
            if path.name.endswith(".result.json"):
                continue
            known.add(path.name)
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            if data.get("evidence"):
                known.add(data["evidence"])
            if data.get("receipt_id"):
                known.add(data["receipt_id"] + ".json")
    return known


def scan(log_roots=None, *, pending=PENDING, processed=PROCESSED, held=HELD):
    """Write pending JSON from watcher markdown so CRM intake does not depend on a chat prompt."""
    pending.mkdir(parents=True, exist_ok=True)
    held.mkdir(parents=True, exist_ok=True)
    roots = log_roots or list((ROOT / "logs").glob("watchers-*")) + [
        ROOT / "logs/reply-watcher",
        ROOT / "logs/daily-gtm-chat",
        ROOT / "logs/hourly-search",
    ]
    written = []
    known = _known_receipts(pending, processed, held)
    for folder in roots:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        paths = sorted(
            {
                *folder.glob("cycle-*.md"),
                *folder.glob("*-send.md"),
                *folder.glob("x-*.md"),
            }
        )
        for path in paths:
            receipts = receipts_from_cycle(path.read_text())
            for receipt in receipts:
                try:
                    evidence = str(path.relative_to(ROOT))
                except ValueError:
                    evidence = str(path)
                receipt["evidence"] = evidence
                receipt["task_evidence"] = "Confirmed published public reply in " + path.name
                receipt["observed_at"] = receipt.get("observed_at") or _observed_from_name(
                    path.name
                )
                name = receipt["receipt_id"] + ".json"
                dest = held / name if receipt.get("hold") else pending / name
                existing_held = held / name
                if existing_held.exists() and not receipt.get("hold"):
                    try:
                        prior = json.loads(existing_held.read_text())
                    except json.JSONDecodeError:
                        prior = {}
                    if prior.get("hold"):
                        existing_held.unlink()
                        result = existing_held.with_suffix(existing_held.suffix + ".result.json")
                        if result.exists():
                            result.unlink()
                        if not (processed / name).exists():
                            known.discard(name)
                if name in known or (processed / name).exists():
                    continue
                dest.write_text(json.dumps(receipt, indent=2) + "\n")
                known.add(name)
                known.add(evidence)
                written.append(str(dest))
    return written


def _observed_from_name(name):
    match = re.search(r"(20\d{6}T\d{4})", name)
    if not match:
        return "2026-09-16T00:00:00+00:00"
    stamp = match.group(1)
    return (
        stamp[:4]
        + "-"
        + stamp[4:6]
        + "-"
        + stamp[6:8]
        + "T"
        + stamp[9:11]
        + ":"
        + stamp[11:13]
        + ":00+00:00"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ingest", "scan", "run"))
    parser.add_argument("--receipt", type=Path, action="append")
    args = parser.parse_args()
    if args.command == "scan":
        result = {"written": scan()}
    elif args.command == "ingest":
        result = {"results": ingest(args.receipt)}
    else:
        written = scan()
        result = {"written": written, "results": ingest()}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
