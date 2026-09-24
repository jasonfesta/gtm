#!/usr/bin/env python3
"""Single local entry point for LinkedIn scans, prepared actions, and confirmed writes."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import store

ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "browser" / "runner.js"
NS = uuid.UUID("e1c5c85c-9a57-4aa5-955d-f4d36fc85107")
REVIEW_RE = re.compile(
    r"\b(lawyer|legal|lawsuit|attorney|contract|tax|investment|invest|financial advice|"
    r"wire|bank|routing|password|passcode|verification code|ssn|social security|"
    r"medical|diagnos|prescription|urgent|emergency|confidential|nda|salary|compensation)\b",
    re.I,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sync_confirmed_analytics(database_path):
    """Analytics failure never retries the already confirmed browser action."""
    workspace = ROOT.parent.parent
    interpreter = workspace / ".venv/bin/python"
    runtime = str(interpreter) if interpreter.is_file() else sys.executable
    try:
        result = subprocess.run(
            [runtime, "-B", "-m", "crm.activity", "--sync", "--linkedin-db", str(database_path)],
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        return {"status": "sync_requested" if result.returncode == 0 else "pending_retry"}
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "pending_retry"}


def stable(kind: str, value: str) -> str:
    return str(uuid.uuid5(NS, f"{kind}|{value}"))


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    path = parts.path.rstrip("/") + "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def parse_row_timestamp(value: str | None, *, seen_at: str, timezone_name: str) -> datetime | None:
    """Interpret LinkedIn's visible conversation-row time without claiming false precision."""
    if not value:
        return None
    text = " ".join(value.split()).strip()
    seen = store._time(seen_at)
    zone = ZoneInfo(timezone_name)
    local_seen = seen.astimezone(zone)
    relative = re.fullmatch(r"(\d+)\s*([mhdw])", text, re.I)
    if relative:
        count = int(relative.group(1))
        delta = {
            "m": timedelta(minutes=count),
            "h": timedelta(hours=count),
            "d": timedelta(days=count),
            "w": timedelta(weeks=count),
        }[relative.group(2).lower()]
        return seen - delta
    if text.lower() in {"now", "just now"}:
        return seen
    if text.lower() == "yesterday":
        return (
            (local_seen - timedelta(days=1))
            .replace(hour=12, minute=0, second=0, microsecond=0)
            .astimezone(timezone.utc)
        )
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = datetime.strptime(text, fmt).replace(tzinfo=zone, hour=12)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    for fmt in ("%b %d", "%B %d"):
        try:
            parsed = datetime.strptime(text, fmt).replace(
                year=local_seen.year, tzinfo=zone, hour=12
            )
            if parsed > local_seen + timedelta(days=1):
                parsed = parsed.replace(year=local_seen.year - 1)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    for fmt in ("%I:%M %p", "%I %p"):
        try:
            parsed_time = datetime.strptime(text, fmt).time()
            return datetime.combine(local_seen.date(), parsed_time, tzinfo=zone).astimezone(
                timezone.utc
            )
        except ValueError:
            pass
    return None


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = ["account_id", "identity", "timezone", "local_root", "database_path", "browser"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"config missing: {', '.join(missing)}")
    if config["dm_replies"].get("never_initiate") is not True:
        raise ValueError("DM safety setting never_initiate must be true")
    if config["public_replies"].get("never_connect_or_follow") is not True:
        raise ValueError("public safety setting never_connect_or_follow must be true")
    targeting = config.get("targeting_policy", {})
    if targeting.get("generation") != "fresh_daily_v1":
        raise ValueError("targeting policy must use fresh_daily_v1")
    if targeting.get("inherit_legacy_targeting") is not False:
        raise ValueError("legacy targeting must remain disabled")
    if targeting.get("inherit_legacy_exclusions") is not False:
        raise ValueError("legacy exclusions must remain disabled")
    if targeting.get("current_special_exclusions") != []:
        raise ValueError("special exclusions must be empty for the fresh-slate baseline")
    repeat_policy = config["public_replies"].get("author_repeat_policy")
    if repeat_policy not in {"lifetime", "cooldown", "crm_rules"}:
        raise ValueError("author_repeat_policy must be crm_rules, lifetime or cooldown")
    if (
        repeat_policy == "cooldown"
        and config["public_replies"].get("author_cooldown_hours") is None
    ):
        raise ValueError("cooldown policy requires author_cooldown_hours")
    return config


def configured_cooldown(config: dict) -> int | None:
    if config["public_replies"]["author_repeat_policy"] == "crm_rules":
        # Eligibility itself reads Rules.md; this argument remains for compatibility.
        return None
    if config["public_replies"]["author_repeat_policy"] == "lifetime":
        return None
    return int(config["public_replies"]["author_cooldown_hours"])


@contextmanager
def account_lock(config: dict):
    lock_path = (
        Path(config["local_root"]) / "logs" / f"{config['account_id'].replace(':', '-')}.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another LinkedIn run is already active for this account") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def bind_account(db, config: dict, identity: dict, detected_at: str) -> None:
    expected = config["identity"]["linkedin_slug"].lower()
    if identity.get("slug", "").lower() != expected:
        raise ValueError(
            f"LinkedIn identity mismatch: expected {expected}, detected {identity.get('slug')}"
        )
    db.execute(
        """INSERT INTO accounts(account_id, public_identifier, profile_url, display_name, timezone,
                   status, first_detected_at, last_detected_at)
           VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
           ON CONFLICT(account_id) DO UPDATE SET public_identifier=excluded.public_identifier,
             profile_url=excluded.profile_url, display_name=excluded.display_name,
             timezone=excluded.timezone, status='active', last_detected_at=excluded.last_detected_at""",
        (
            config["account_id"],
            expected,
            identity.get("profileUrl"),
            identity.get("displayName") or expected,
            config["timezone"],
            detected_at,
            detected_at,
        ),
    )
    browser_key = config["browser"]["profile_key"]
    db.execute(
        """INSERT INTO browser_profiles(browser_profile_key, label, active_account_id, first_seen_at,
                   last_seen_at, last_identity_check)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(browser_profile_key) DO UPDATE SET active_account_id=excluded.active_account_id,
             last_seen_at=excluded.last_seen_at, last_identity_check=excluded.last_identity_check""",
        (browser_key, browser_key, config["account_id"], detected_at, detected_at, detected_at),
    )
    policy_json = json.dumps(config, sort_keys=True)
    latest = db.execute(
        """SELECT revision, policy_json FROM account_policies
           WHERE account_id = ? ORDER BY revision DESC LIMIT 1""",
        (config["account_id"],),
    ).fetchone()
    if not latest or latest["policy_json"] != policy_json:
        revision = 1 if not latest else int(latest["revision"]) + 1
        db.execute(
            """INSERT INTO account_policies(
                   account_id, revision, mode, policy_json, reviewed_by, created_at)
               VALUES (?, ?, ?, ?, 'workspace owner', ?)""",
            (
                config["account_id"],
                revision,
                config.get("mode", "preview"),
                policy_json,
                detected_at,
            ),
        )


def ensure_person(db, *, name: str, profile_url: str, seen_at: str) -> tuple[str, str]:
    normalized_url = normalize_url(profile_url)
    person_id = stable("person", normalized_url)
    profile_id = stable("profile", normalized_url)
    normalized_name = " ".join(name.lower().split()) or normalized_url
    slug = normalized_url.split("/in/", 1)[1].strip("/") if "/in/" in normalized_url else None
    db.execute(
        """INSERT INTO people(person_id, canonical_name, normalized_name, identity_status, created_at, updated_at)
           VALUES (?, ?, ?, 'supported', ?, ?)
           ON CONFLICT(person_id) DO UPDATE SET canonical_name=excluded.canonical_name,
             normalized_name=excluded.normalized_name, updated_at=excluded.updated_at""",
        (person_id, name or slug or "LinkedIn member", normalized_name, seen_at, seen_at),
    )
    db.execute(
        """INSERT INTO profiles(profile_id, person_id, public_identifier, profile_url,
                   normalized_profile_url, display_name, identity_method, first_seen_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, 'self_link', ?, ?)
           ON CONFLICT(profile_id) DO UPDATE SET display_name=excluded.display_name,
             last_seen_at=excluded.last_seen_at""",
        (profile_id, person_id, slug, profile_url, normalized_url, name, seen_at, seen_at),
    )
    return person_id, profile_id


def invoke_browser(
    config_path: Path, config: dict, command: str, run_id: str, *, input_path: Path | None = None
) -> tuple[dict, Path]:
    output = Path(config["local_root"]) / "logs" / f"{run_id}-{command}.json"
    argv = [
        "node",
        str(RUNNER),
        command,
        "--config",
        str(config_path),
        "--output",
        str(output),
        "--run-id",
        run_id,
    ]
    if input_path:
        argv.extend(["--input", str(input_path)])
    completed = subprocess.run(argv, text=True, capture_output=True, check=False)
    if completed.returncode or not output.exists():
        detail = completed.stderr.strip() or completed.stdout.strip() or "browser runner failed"
        raise RuntimeError(detail[-1000:])
    return json.loads(output.read_text(encoding="utf-8")), output


def ingest_feed(db, config: dict, scan: dict, run_id: str, seen_at: str) -> list[dict]:
    prepared = []
    ordinal = 0
    for item in scan.get("feed", {}).get("candidates", []):
        reason = None
        if item.get("promoted"):
            reason = "promoted"
        elif item.get("repost"):
            reason = "repost"
        elif item.get("commented"):
            reason = "comment_activity"
        elif not item.get("authorProfileUrl") or not item.get("authorSlug"):
            reason = "author_identity_unavailable"
        if reason:
            continue
        person_id, profile_id = ensure_person(
            db,
            name=item.get("author") or item["authorSlug"],
            profile_url=item["authorProfileUrl"],
            seen_at=seen_at,
        )
        post_id = stable("post", item["urn"])
        db.execute(
            """INSERT INTO posts(post_id, author_profile_id, platform_post_id, canonical_url,
                       post_signature, body, body_sha256, observed_at, post_kind, has_media)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'original', ?)
               ON CONFLICT(post_id) DO UPDATE SET body=excluded.body, body_sha256=excluded.body_sha256,
                 observed_at=excluded.observed_at, has_media=excluded.has_media""",
            (
                post_id,
                profile_id,
                item["urn"],
                item.get("canonicalUrl"),
                item["urn"],
                item.get("body"),
                item.get("bodySha256"),
                seen_at,
                int(bool(item.get("hasMedia"))),
            ),
        )
        decision = store.eligibility(
            db,
            account_id=config["account_id"],
            person_id=person_id,
            action_type="comment",
            workflow="casual",
            post_id=post_id,
            cooldown_hours=configured_cooldown(config),
        )
        entry = {
            "kind": "casual_comment",
            "person_id": person_id,
            "profile_id": profile_id,
            "post_id": post_id,
            "urn": item["urn"],
            "canonical_url": item.get("canonicalUrl"),
            "author": item.get("author"),
            "author_slug": item.get("authorSlug"),
            "post_body": item.get("body"),
            "body_sha256": item.get("bodySha256"),
            "content_anchor": item.get("contentAnchor"),
            "screenshot": item.get("screenshot"),
            "eligibility": decision,
        }
        prepared.append(entry)
        db.execute(
            """INSERT INTO run_items(run_item_id, run_id, ordinal, person_id, profile_id, post_id,
                       decision, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                run_id,
                ordinal,
                person_id,
                profile_id,
                post_id,
                "eligible" if decision["allowed"] else "skipped",
                decision["reason"],
                seen_at,
            ),
        )
        ordinal += 1
    return prepared


def ingest_dms(
    db, config: dict, scan: dict, run_id: str, seen_at: str, ordinal_start: int
) -> list[dict]:
    prepared = []
    cutoff = store._time(config["dm_replies"]["monitoring_not_before"])
    ordinal = ordinal_start
    for thread in scan.get("dms", {}).get("threads", []):
        target = thread.get("replyTarget")
        profiles = thread.get("profileUrls") or []
        if not target or len(profiles) != 1:
            continue
        person_id, profile_id = ensure_person(
            db,
            name=thread.get("participant") or thread.get("title") or "LinkedIn member",
            profile_url=profiles[0],
            seen_at=seen_at,
        )
        thread_key = thread.get("threadId") or thread.get("threadUrl") or f"unknown:{person_id}"
        conversation_id = stable("conversation", f"{config['account_id']}|{thread_key}")
        db.execute(
            """INSERT INTO conversations(conversation_id, account_id, person_id, surface,
                       platform_thread_id, opened_at, last_activity_at, status)
               VALUES (?, ?, ?, 'dm', ?, ?, ?, 'open')
               ON CONFLICT(conversation_id) DO UPDATE SET last_activity_at=excluded.last_activity_at""",
            (conversation_id, config["account_id"], person_id, thread_key, seen_at, seen_at),
        )
        last_outbound_index = -1
        target_index = -1
        target_message_id = None
        target_first_observed = seen_at
        for index, message in enumerate(thread.get("messages", [])):
            message_id = stable("linkedin-message", message["eventUrn"])
            existing = db.execute(
                "SELECT observed_at FROM messages WHERE message_id = ?", (message_id,)
            ).fetchone()
            if message["direction"] == "outbound":
                last_outbound_index = index
            if message["eventUrn"] == target["eventUrn"]:
                target_index = index
                target_message_id = message_id
                target_first_observed = existing["observed_at"] if existing else seen_at
            kind = "dm" if message["direction"] == "inbound" else "dm_reply"
            occurred = message.get("datetime") or seen_at
            db.execute(
                """INSERT OR IGNORE INTO messages(
                       message_id, account_id, person_id, conversation_id, platform_message_id,
                       direction, message_kind, body, occurred_at, observed_at, source, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?)""",
                (
                    message_id,
                    config["account_id"],
                    person_id,
                    conversation_id,
                    message["eventUrn"],
                    message["direction"],
                    kind,
                    message["body"],
                    occurred,
                    seen_at,
                    json.dumps({"sender_heading": message.get("senderHeading")}, sort_keys=True),
                ),
            )
            db.execute(
                """INSERT OR IGNORE INTO message_status_events(
                       status_event_id, message_id, status, occurred_at, source)
                   VALUES (?, ?, 'observed', ?, 'live')""",
                (stable("observed", message_id), message_id, seen_at),
            )
        row_time = parse_row_timestamp(
            thread.get("rowTimestamp"), seen_at=seen_at, timezone_name=config["timezone"]
        )
        occurred = (
            store._time(target["datetime"])
            if target.get("datetime")
            else (row_time or store._time(target_first_observed))
        )
        first_observed = store._time(target_first_observed)
        reason = "eligible"
        if occurred < cutoff or first_observed < cutoff:
            reason = "before_monitoring_baseline"
        elif thread.get("ambiguous"):
            reason = "ambiguous_conversation"
        elif thread.get("inMail") and config["dm_replies"].get("skip_inmail", True):
            reason = "inmail"
        elif thread.get("sponsored") and config["dm_replies"].get("skip_sponsored", True):
            reason = "sponsored"
        elif last_outbound_index > target_index:
            reason = "later_outbound_exists"
        elif REVIEW_RE.search(target["body"]):
            reason = "sensitive_or_judgment_required"
        else:
            decision = store.eligibility(
                db,
                account_id=config["account_id"],
                person_id=person_id,
                action_type="reply",
                workflow="reply_dm",
                in_reply_to_message_id=target_message_id,
            )
            reason = decision["reason"]
        entry = {
            "kind": "dm_reply",
            "person_id": person_id,
            "profile_id": profile_id,
            "conversation_id": conversation_id,
            "thread_url": thread.get("threadUrl"),
            "participant": thread.get("participant"),
            "inbound_message_id": target_message_id,
            "inbound_event_urn": target["eventUrn"],
            "inbound_body": target["body"],
            "inbound_body_sha256": target.get("bodySha256") or sha(target["body"]),
            "context": thread.get("messages", []),
            "eligibility": {"allowed": reason == "eligible", "reason": reason},
        }
        prepared.append(entry)
        db.execute(
            """INSERT INTO run_items(run_item_id, run_id, ordinal, person_id, profile_id,
                       decision, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                run_id,
                ordinal,
                person_id,
                profile_id,
                "eligible"
                if reason == "eligible"
                else (
                    "needs_review"
                    if reason in {"ambiguous_conversation", "sensitive_or_judgment_required"}
                    else "skipped"
                ),
                reason,
                seen_at,
            ),
        )
        ordinal += 1
    return prepared


def prepare(config_path: Path, config: dict, source: str) -> dict:
    if source == "scheduled" and config.get("stop_file") and Path(config["stop_file"]).exists():
        raise RuntimeError("LinkedIn stop control is active")
    run_id = str(uuid.uuid4())
    db_path = Path(config["database_path"])
    store.initialize(db_path)
    started = now_iso()
    with store.connect(db_path) as db:
        db.execute(
            """INSERT INTO runs(run_id, account_id, browser_profile_key, source, workflow,
                       requested_count, status, started_at)
               VALUES (?, NULL, NULL, ?, 'casual', ?, 'started', ?)""",
            (run_id, source, int(config["public_replies"]["per_run"]), started),
        )
        db.commit()
    scan, raw_path = invoke_browser(config_path, config, "scan", run_id)
    with store.connect(db_path) as db:
        bind_account(db, config, scan["identity"], started)
        db.execute(
            "UPDATE runs SET account_id=?, browser_profile_key=? WHERE run_id=?",
            (config["account_id"], config["browser"]["profile_key"], run_id),
        )
        comments = ingest_feed(db, config, scan, run_id, started)
        dms = ingest_dms(db, config, scan, run_id, started, len(comments))
        summary = {
            "identity": scan["identity"]["slug"],
            "feed_sort": scan.get("feed", {}).get("sortLabel"),
            "comment_candidates": len(comments),
            "eligible_comments": sum(1 for item in comments if item["eligibility"]["allowed"]),
            "unread_threads": len(scan.get("dms", {}).get("threads", [])),
            "eligible_dm_replies": sum(1 for item in dms if item["eligibility"]["allowed"]),
            "dm_needs_review": sum(
                1
                for item in dms
                if item["eligibility"]["reason"]
                in {"ambiguous_conversation", "sensitive_or_judgment_required"}
            ),
        }
        db.execute(
            "UPDATE runs SET status='complete', finished_at=?, summary_json=? WHERE run_id=?",
            (now_iso(), json.dumps(summary, sort_keys=True), run_id),
        )
        db.commit()
    prepared_path = Path(config["local_root"]) / "drafts" / run_id / "prepared.json"
    prepared_path.parent.mkdir(parents=True, exist_ok=True)
    prepared_path.write_text(
        json.dumps({"run_id": run_id, "comments": comments, "dm_replies": dms}, indent=2) + "\n"
    )
    return {
        "ok": True,
        "run_id": run_id,
        "prepared": str(prepared_path),
        "raw": str(raw_path),
        **summary,
    }


def lint_dm(text: str, max_words: int) -> list[str]:
    reasons = []
    if not text.strip():
        reasons.append("empty")
    if len(text.split()) > max_words:
        reasons.append("too_long")
    if re.search(r"https?://|@|#|[\u2013\u2014]", text):
        reasons.append("disallowed_character_or_link")
    if REVIEW_RE.search(text):
        reasons.append("sensitive_or_judgment_required")
    return reasons


def lint_comment(text: str, policy: dict) -> list[str]:
    reasons = []
    if not text.strip():
        reasons.append("empty")
    if len(text.split()) > int(policy.get("max_words", 24)):
        reasons.append("too_long")
    if text != text.lower():
        reasons.append("prose_must_be_lowercase")
    if re.search(r"https?://|@|#|[\u2013\u2014]", text):
        reasons.append("disallowed_character_or_link")
    if "\n" in text:
        reasons.append("single_line_required")
    return reasons


def apply(config_path: Path, config: dict, selected_path: Path) -> dict:
    if config.get("mode") != "live":
        raise RuntimeError("account policy is not in live mode")
    stop_file = config.get("stop_file")
    if stop_file and Path(stop_file).exists():
        raise RuntimeError("LinkedIn stop control is active")
    selected = json.loads(selected_path.read_text(encoding="utf-8"))
    run_id = selected["run_id"]
    prepared_path = Path(config["local_root"]) / "drafts" / run_id / "prepared.json"
    prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
    by_key = {}
    for item in prepared["comments"] + prepared["dm_replies"]:
        key = item.get("post_id") or item.get("inbound_message_id")
        by_key[key] = item
    requested = selected.get("actions", [])
    if sum(1 for item in requested if item.get("kind") == "casual_comment") > int(
        config["public_replies"]["per_run"]
    ):
        raise ValueError("per-run public reply limit exceeded")
    browser_actions = []
    reservations = {}
    with store.connect(Path(config["database_path"])) as db:
        for choice in requested:
            key = choice.get("post_id") or choice.get("inbound_message_id")
            item = by_key.get(key)
            if not item or not item["eligibility"]["allowed"] or choice.get("kind") != item["kind"]:
                continue
            if item["kind"] not in set(config.get("allowed_actions", [])):
                continue
            if item["kind"] == "casual_comment" and not config["public_replies"].get("enabled"):
                continue
            if item["kind"] == "dm_reply" and not config["dm_replies"].get("enabled"):
                continue
            text = choice.get("text", "").strip()
            reasons = (
                lint_comment(text, config["public_replies"])
                if item["kind"] == "casual_comment"
                else lint_dm(text, int(config["dm_replies"]["max_words"]))
            )
            if reasons:
                continue
            draft_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO draft_revisions(draft_id, revision, account_id, person_id, post_id,
                           conversation_id, workflow, message_kind, variant, body, source_message_id,
                           lint_profile, lint_status, editor, created_at)
                   VALUES (?, 1, ?, ?, ?, ?, ?, ?, 'selected', ?, ?, ?, 'passed', 'operator', ?)""",
                (
                    draft_id,
                    config["account_id"],
                    item["person_id"],
                    item.get("post_id"),
                    item.get("conversation_id"),
                    "casual" if item["kind"] == "casual_comment" else "reply_dm",
                    "comment" if item["kind"] == "casual_comment" else "reply",
                    text,
                    item.get("inbound_message_id"),
                    "linkedin_voice_lint" if item["kind"] == "casual_comment" else "dm_reply_lint",
                    now_iso(),
                ),
            )
            db.commit()
            reservation = store.reserve_action(
                db,
                account_id=config["account_id"],
                person_id=item["person_id"],
                profile_id=item["profile_id"],
                run_id=run_id,
                conversation_id=item.get("conversation_id"),
                draft_id=draft_id,
                action_type="comment" if item["kind"] == "casual_comment" else "reply",
                workflow="casual" if item["kind"] == "casual_comment" else "reply_dm",
                target_key=item.get("post_id") or item["inbound_message_id"],
                policy_scope="casual" if item["kind"] == "casual_comment" else "reply_dm",
                post_id=item.get("post_id"),
                in_reply_to_message_id=item.get("inbound_message_id"),
                cooldown_hours=configured_cooldown(config),
                daily_cap=int(config["public_replies"]["daily_cap"]),
                timezone_name=config["timezone"],
            )
            if not reservation["allowed"]:
                continue
            action_id = reservation["action_id"]
            reservations[action_id] = {"item": item, "text": text}
            browser_actions.append(
                {
                    "actionId": action_id,
                    "kind": item["kind"],
                    "text": text,
                    "urn": item.get("urn"),
                    "canonicalUrl": item.get("canonical_url"),
                    "authorSlug": item.get("author_slug"),
                    "bodySha256": item.get("body_sha256"),
                    "contentAnchor": item.get("content_anchor"),
                    "threadUrl": item.get("thread_url"),
                    "inboundEventUrn": item.get("inbound_event_urn"),
                    "inboundBodySha256": item.get("inbound_body_sha256"),
                }
            )
        db.commit()
    browser_input = Path(config["local_root"]) / "drafts" / run_id / "browser-input.json"
    browser_input.write_text(json.dumps({"actions": browser_actions}, indent=2) + "\n")
    if not browser_actions:
        return {
            "ok": True,
            "run_id": run_id,
            "attempted": 0,
            "confirmed": 0,
            "uncertain": 0,
            "failed": 0,
        }
    result, raw_path = invoke_browser(
        config_path, config, "apply", run_id, input_path=browser_input
    )
    counts = {"confirmed": 0, "uncertain": 0, "failed": 0}
    with store.connect(Path(config["database_path"])) as db:
        for outcome in result.get("results", []):
            action_id = outcome["actionId"]
            saved = reservations[action_id]
            item = saved["item"]
            if outcome["status"] == "confirmed":
                store.record_confirmed_send(
                    db,
                    action_id=action_id,
                    body=saved["text"],
                    message_kind="casual_comment"
                    if item["kind"] == "casual_comment"
                    else "dm_reply",
                    platform_reference=outcome["platformReference"],
                    confirmation_method=outcome["confirmationMethod"],
                    conversation_id=item.get("conversation_id"),
                    in_reply_to_message_id=item.get("inbound_message_id"),
                )
                counts["confirmed"] += 1
            else:
                state = "uncertain" if outcome["status"] == "uncertain" else "failed"
                store.record_action_result(
                    db, action_id=action_id, state=state, error_message=outcome.get("reason")
                )
                counts[state] += 1
    analytics = (
        sync_confirmed_analytics(config["database_path"])
        if counts["confirmed"]
        else {"status": "no_confirmed_sends"}
    )
    return {
        "ok": True,
        "run_id": run_id,
        "attempted": len(browser_actions),
        "raw": str(raw_path),
        "analytics": analytics,
        **counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument(
        "--source", choices=("interactive", "scheduled"), default="interactive"
    )
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--selected", required=True)
    sub.add_parser("status")
    args = parser.parse_args()
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    if args.command == "prepare":
        with account_lock(config):
            result = prepare(config_path, config, args.source)
    elif args.command == "apply":
        with account_lock(config):
            result = apply(config_path, config, Path(args.selected).expanduser().resolve())
    else:
        result = store.counts(Path(config["database_path"]))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
