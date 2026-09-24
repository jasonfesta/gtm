"""Day report for the main GTM chat. Opening main means reading this file."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from crm.approvals import pending as pending_approvals
from crm.database import ROOT
from crm.human_discovery_search import DROPS, HANDOFF, day_brief
from crm.publication import HELD, PROCESSED
from crm.send_mode import status as send_mode_status

REPORTS = ROOT / "logs/daily-gtm-chat"
NY = ZoneInfo("America/New_York")


def day_stamp(now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(NY).strftime("%Y-%m-%d")


def _json_files(folder):
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.json") if not p.name.endswith(".result.json"))


def _day_of(value, day):
    text = str(value or "")
    if text.startswith(day):
        return True
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(NY).strftime("%Y-%m-%d") == day


def _receipts(folder, day):
    rows = []
    for path in _json_files(folder):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        observed = data.get("observed_at") or data.get("hour") or path.name
        if _day_of(observed, day):
            rows.append({"path": str(path), **data})
    return rows


def _clean(text, limit=None):
    text = " ".join((text or "").split())
    if limit and len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def parent_index(handoff=HANDOFF, drops=DROPS):
    """Original post body from search handoffs. Publication `text` is our reply."""
    index = {}
    for root in (Path(handoff), Path(drops)):
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            if path.name.endswith(".result.json"):
                continue
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError:
                continue
            rows = [data]
            if isinstance(data.get("comments"), list):
                rows.extend(data["comments"])
            for row in rows:
                url = (row.get("parent_url") or "").strip()
                if not url:
                    continue
                if row.get("reply_url"):
                    stored = (row.get("parent_text") or row.get("parent_post") or "").strip()
                    if stored:
                        index[url] = stored
                    continue
                body = (row.get("text") or "").strip()
                if body and url not in index:
                    index[url] = body
    return index


def original_post(row, index=None):
    index = index or {}
    return (
        row.get("parent_text")
        or row.get("parent_post")
        or index.get(row.get("parent_url") or "")
        or ""
    )


def slack_summary(day, sent, index=None, *, latest=8):
    index = index or {}
    x_sent = [r for r in sent if r.get("channel") == "x"]
    li_sent = [r for r in sent if r.get("channel") == "linkedin"]
    lines = [
        f"GTM {day} · {len(sent)} comments ({len(x_sent)} X / {len(li_sent)} LinkedIn)",
        "",
    ]
    if not sent:
        lines.append("No comments yet.")
        return "\n".join(lines) + "\n"
    lines.append("Latest")
    for row in list(reversed(sent))[:latest]:
        lines.extend(
            [
                f"• {row.get('name')} ({row.get('channel')})",
                f"  post: {_clean(original_post(row, index), 220) or '(original post not stored)'}",
                f"  reply: {_clean(row.get('text'), 220) or '(no reply text)'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render(
    day=None,
    *,
    drops=DROPS,
    handoff=HANDOFF,
    processed=PROCESSED,
    held=HELD,
    reports=REPORTS,
    now=None,
    send_mode_state=None,
    approvals_queue=None,
):
    day = day or day_stamp(now)
    processed_rows = _receipts(processed, day)
    held_rows = _receipts(held, day)
    sent = [r for r in processed_rows if not r.get("hold")]
    later = [r for r in processed_rows + held_rows if r.get("hold") in ("later_use", "search_hit")]
    holds = [r for r in held_rows if r.get("hold") not in ("later_use", "search_hit")]
    search = day_brief(day, now=now, drops=drops)
    pending = []
    for channel in ("x", "linkedin", "outreach"):
        folder = Path(handoff) / channel
        if folder.is_dir():
            pending.extend(
                sorted(p for p in folder.glob("*.json") if not p.name.endswith(".done.json"))
            )
    x_sent = [r for r in sent if r.get("channel") == "x"]
    li_sent = [r for r in sent if r.get("channel") == "linkedin"]
    sent = sorted(sent, key=lambda r: str(r.get("observed_at") or ""))
    index = parent_index(handoff, drops)
    summary = slack_summary(day, sent, index)
    mode = (
        send_mode_status(state=send_mode_state)
        if send_mode_state is not None
        else send_mode_status()
    )
    waiting_approval = pending_approvals(
        day, queue=approvals_queue if approvals_queue is not None else Path(reports) / "approvals"
    )
    if mode["send_live"]:
        mode_line = "Automatic sends. This file is the 15-minute report."
    else:
        mode_line = f"Approve every message in this chat. {waiting_approval['count']} waiting."
    lines = [
        f"# GTM {day}",
        "",
        mode_line,
        "",
        "## Totals",
        f"- comments {len(sent)} · X {len(x_sent)} · LinkedIn {len(li_sent)}",
        f"- waiting on approval {waiting_approval['count']}",
        "",
        "## Summary",
        "",
        summary.rstrip(),
        "",
        "## Comments",
    ]
    if sent:
        for row in sent:
            orig = _clean(original_post(row, index))
            reply = _clean(row.get("text"))
            lines.extend(
                [
                    f"### {row.get('name')} · {row.get('channel')}",
                    f"{row.get('observed_at')}",
                    "",
                    "Their post",
                    f"> {orig or '(original post not stored)'}",
                    "",
                    "Our reply",
                    f"> {reply or '(no reply text)'}",
                    "",
                    f"- post {row.get('parent_url')}",
                    f"- sent {row.get('reply_url') or 'permalink missing'}",
                    "",
                ]
            )
    else:
        lines.append("- none yet")
    lines.extend(["", "## Waiting on approval"])
    if waiting_approval["items"]:
        for row in waiting_approval["items"]:
            lines.extend(
                [
                    f"### {row.get('name')} · {row.get('channel')} · {row.get('kind')} · `{row.get('id')}`",
                    "",
                    "Their post",
                    f"> {_clean(row.get('parent_text')) or '(original post not stored)'}",
                    "",
                    "Proposed send",
                    f"> {_clean(row.get('text')) or '(no reply text)'}",
                    "",
                    f"- post {row.get('parent_url')}",
                    f"- approve: `python -B -m crm.approvals approve --id {row.get('id')} --day {day}`",
                    f"- reject: `python -B -m crm.approvals reject --id {row.get('id')} --day {day}`",
                    "",
                ]
            )
    else:
        lines.append("- none")
    lines.extend(["", "## CRM writes"])
    writes = sent + later
    if writes:
        for row in writes:
            kind = row.get("hold") or "published"
            lines.append(
                f"- {kind} {row.get('channel')} {row.get('name')} {row.get('profile_url')}"
            )
    else:
        lines.append("- none yet")
    lines.extend(["", "## Hourly search"])
    lines.append(
        f"- {search['hours_present']}/24 hours. "
        f"comments={len(search['comments'])} "
        f"later={len(search['later_use'])} "
        f"skipped={search['skipped_count']} "
        f"missed={len(search['missed'])} remaining={len(search['remaining'])}"
    )
    if search["comments"]:
        lines.append("- comment → in CRM; watcher replies to this recent X or LinkedIn post")
        for row in search["comments"]:
            lines.append(f"  - {row['channel']} {row['name']} {row['parent_url']}")
    if search["later_use"]:
        lines.append("- later_use → in CRM; watcher replies to one recent X or LinkedIn post")
        for row in search["later_use"]:
            lines.append(f"  - {row['channel']} {row['name']} {row['parent_url']}")
    if search["hours"]:
        for row in search["hours"]:
            lines.append(
                f"- {row['hour']} comments={len(row.get('comments') or [])} "
                f"later={len(row.get('later_use') or [])} skipped={row.get('skipped_count') or 0}"
            )
    else:
        lines.append("- no drop yet")
    lines.extend(["", "## Waiting on watchers"])
    if pending:
        for path in pending:
            data = json.loads(path.read_text())
            action = data.get("action") or "comment"
            channel = data.get("network") or data.get("channel")
            lines.append(f"- {action} {channel} {data.get('name')} {data.get('parent_url')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Holds"])
    if holds:
        for row in holds:
            lines.append(f"- {row.get('hold') or 'held'} {row.get('channel')} {row.get('name')}")
    else:
        lines.append("- none")
    unattended = Path(reports) / "UNATTENDED.json"
    if unattended.is_file():
        try:
            data = json.loads(unattended.read_text())
            hour = data.get("hour") or {}
            lease = data.get("lease") or {}
            lines.extend(
                [
                    "",
                    "## Unattended",
                    f"- live tick {data.get('at')} hour={hour.get('hour')} "
                    f"search={hour.get('reason') or hour.get('ran')} "
                    f"lease_held={lease.get('held')}",
                ]
            )
        except json.JSONDecodeError:
            lines.extend(["", "## Unattended", "- heartbeat unreadable"])
    lines.append("")
    text = "\n".join(lines)
    dest = Path(reports)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"GTM-{day}.md"
    path.write_text(text)
    slack_path = dest / f"GTM-{day}-slack.md"
    slack_path.write_text(summary)
    return {"path": str(path), "slack_path": str(slack_path), "text": text, "slack": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day")
    args = parser.parse_args()
    result = render(args.day)
    print(result["text"])


if __name__ == "__main__":
    main()
