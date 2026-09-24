"""Small X-only planning core for the Codex timeline watcher."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from crm import database, outreach_rules

CRM_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = CRM_ROOT.parent
KEYWORDS = CRM_ROOT / "config/timeline-keywords.txt"
LEASE_DB = CRM_ROOT / "data/discovery-runtime.sqlite3"
PORTKEY_CREDENTIALS = WORKSPACE_ROOT / ".local-credentials/portkey.json"
PORTKEY_URL = "https://api.portkey.ai/v1/chat/completions"
MODEL = "gpt-4o-mini"
WINDOW_SECONDS = 5 * 60
LEASE_SECONDS = 10 * 60


def utc(value: str | dt.datetime | None = None) -> dt.datetime:
    if value is None:
        parsed = dt.datetime.now(dt.timezone.utc)
    elif isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(dt.timezone.utc)


def stamp(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def normalize_handle(value: str) -> str:
    value = value.strip().casefold()
    if "x.com/" in value or "twitter.com/" in value:
        value = value.split("/", 3)[-1].split("/", 1)[0]
    return value.lstrip("@").strip("/")


def fingerprint(handle: str, text: str, created_at: str, url: str = "") -> str:
    raw = "\n".join((normalize_handle(handle), " ".join(text.split()), created_at, url))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Post:
    id: str
    handle: str
    created_at: str
    text: str
    url: str
    lane: str
    fingerprint: str

    @classmethod
    def parse(cls, row: dict) -> Post:
        required = ("id", "handle", "created_at", "text", "lane")
        if any(not str(row.get(key, "")).strip() for key in required):
            raise ValueError("post missing compact DOM fields")
        handle = normalize_handle(str(row["handle"]))
        if not re.fullmatch(r"[a-z0-9_]{1,15}", handle):
            raise ValueError("invalid X handle")
        text = " ".join(str(row["text"]).split())
        url = str(row.get("url") or "").strip()
        if url and not re.fullmatch(
            rf"https://(?:www\.)?(?:x|twitter)\.com/{re.escape(handle)}/status/\d+",
            url,
            re.IGNORECASE,
        ):
            raise ValueError("X post URL does not match handle")
        created = stamp(utc(str(row["created_at"])))
        return cls(
            id=str(row["id"]),
            handle=handle,
            created_at=created,
            text=text,
            url=url,
            lane=str(row["lane"]).strip(),
            fingerprint=str(row.get("fingerprint") or fingerprint(handle, text, created, url)),
        )


def load_keywords(path: Path = KEYWORDS) -> tuple[str, ...]:
    terms = []
    for line in path.read_text().splitlines():
        value = line.strip().casefold()
        if value and not value.startswith("#"):
            terms.append(value)
    if not terms:
        raise ValueError("at least one X keyword is required")
    return tuple(dict.fromkeys(terms))


def compact_window(
    rows: list[dict], *, at: dt.datetime, seconds: int = WINDOW_SECONDS
) -> list[Post]:
    kept = {}
    for row in rows:
        post = Post.parse(row)
        age = (at - utc(post.created_at)).total_seconds()
        if age < 0 or age > seconds:
            continue
        key = post.url or post.fingerprint
        kept.setdefault(key, post)
    return sorted(kept.values(), key=lambda post: post.created_at, reverse=True)


def abusive_handles(posts: list[Post]) -> set[str]:
    reads = [post for post in posts if post.lane.casefold().startswith("reads_")]
    counts = Counter(post.handle for post in reads)
    total = len(reads)
    return {
        handle
        for handle, count in counts.items()
        if count >= 4 and total and (count / total) > 0.75
    }


class PortkeyClient:
    def __init__(self, *, api_key=None, config_id=None, opener=None):
        saved = {}
        if PORTKEY_CREDENTIALS.exists():
            saved = json.loads(PORTKEY_CREDENTIALS.read_text())
        self.api_key = api_key or os.getenv("PORTKEY_API_KEY") or saved.get("PORTKEY_API_KEY")
        self.config_id = (
            config_id or os.getenv("PORTKEY_CONFIG_ID") or saved.get("PORTKEY_CONFIG_ID")
        )
        self.provider = os.getenv("PORTKEY_PROVIDER") or saved.get("PORTKEY_PROVIDER")
        self.opener = opener or urllib.request.urlopen
        if not self.api_key:
            raise RuntimeError("Portkey API key unavailable")

    def complete(self, system: str, payload: dict) -> dict:
        body = json.dumps(
            {
                "model": MODEL,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(payload, separators=(",", ":")),
                    },
                ],
            }
        ).encode()
        headers = {
            "content-type": "application/json",
            "x-portkey-api-key": self.api_key,
        }
        if self.config_id:
            headers["x-portkey-config"] = self.config_id
        if self.provider:
            headers["x-portkey-provider"] = self.provider
        request = urllib.request.Request(PORTKEY_URL, body, headers)
        try:
            with self.opener(request, timeout=30) as response:
                result = json.loads(response.read())
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError("Portkey request failed closed") from exc
        observed = str(result.get("model") or "")
        if not observed.startswith(MODEL):
            raise RuntimeError(f"Portkey model mismatch: {observed or 'missing'}")
        try:
            content = result["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Portkey returned invalid structured output") from exc
        if not isinstance(parsed, dict):
            raise TypeError("Portkey output must be an object")
        return parsed

    def classify(self, posts: list[Post]) -> set[str]:
        if not posts:
            return set()
        answer = self.complete(
            'Select X posts genuinely about building, finding, or integrating AI agent/assistant tools. Return JSON only: {"ids":[...]}. No explanation.',
            {"posts": [{"id": p.id, "text": p.text} for p in posts]},
        )
        ids = answer.get("ids")
        allowed = {post.id for post in posts}
        if not isinstance(ids, list) or any(item not in allowed for item in ids):
            raise RuntimeError("Portkey classification IDs invalid")
        return set(ids)

    def draft(self, posts: list[Post]) -> dict[str, str]:
        if not posts:
            return {}
        answer = self.complete(
            'Write one natural lowercase X reply per post. Stay in the post\'s context; make no invented claims, links, offers, or relationships. Return JSON only: {"replies":[{"id":"...","reply":"..."}]}',
            {"posts": [{"id": p.id, "text": p.text} for p in posts]},
        )
        rows = answer.get("replies")
        if not isinstance(rows, list):
            raise TypeError("Portkey replies invalid")
        allowed = {post.id for post in posts}
        result = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("id") not in allowed:
                raise RuntimeError("Portkey reply ID invalid")
            reply = " ".join(str(row.get("reply") or "").split())
            if not reply or "http://" in reply or "https://" in reply:
                raise RuntimeError("Portkey reply text invalid")
            result[row["id"]] = reply
        if set(result) != allowed:
            raise RuntimeError("Portkey must return one reply per eligible post")
        return result


class CRMGuard:
    """One fail-closed shared-CRM batch with no local-authority fallback."""

    def __init__(self, connection_factory=database.connect):
        self.connection_factory = connection_factory

    def check(self, handles: set[str], *, at: dt.datetime) -> dict[str, dict]:
        result = {h: {"allowed": False, "reason": "identity_missing"} for h in handles}
        if not handles:
            return result
        try:
            connection = self.connection_factory()
            try:
                contacts = connection.execute(
                    "SELECT cp.person_id,cp.value,cp.verification_status,p.identity_status,p.research_status "
                    "FROM contact_points cp JOIN people p ON p.person_id=cp.person_id "
                    "WHERE cp.contact_type='x'"
                ).fetchall()
                by_handle = defaultdict(list)
                for row in contacts:
                    handle = normalize_handle(row["value"])
                    if handle in handles:
                        by_handle[handle].append(dict(row))
                person_ids = sorted({r["person_id"] for rows in by_handle.values() for r in rows})
                events, suppressions, conflicts, tasks = (
                    defaultdict(list),
                    set(),
                    set(),
                    set(),
                )
                if person_ids:
                    marks = ",".join("?" for _ in person_ids)
                    for row in connection.execute(
                        f"SELECT * FROM outreach_events WHERE person_id IN ({marks})",
                        person_ids,
                    ):
                        events[row["person_id"]].append(dict(row))
                    suppressions = {
                        row["person_id"]
                        for row in connection.execute(
                            f"SELECT person_id FROM suppressions WHERE is_active=1 AND person_id IN ({marks})",
                            person_ids,
                        )
                    }
                    conflicts = {
                        row["entity_id"]
                        for row in connection.execute(
                            f"SELECT entity_id FROM conflicts WHERE status='open' AND entity_type='person' AND entity_id IN ({marks})",
                            person_ids,
                        )
                    }
                    tasks = {
                        row["entity_id"]
                        for row in connection.execute(
                            f"SELECT entity_id FROM research_tasks WHERE status IN ('queued','blocked','needs_review','in_progress') AND entity_type='person' AND entity_id IN ({marks})",
                            person_ids,
                        )
                    }
            finally:
                connection.close()
        except Exception:  # noqa: BLE001 - every shared CRM failure must hold all outreach.
            return {h: {"allowed": False, "reason": "shared_crm_unavailable"} for h in handles}

        for handle in handles:
            rows = by_handle.get(handle, [])
            if not rows:
                key = hashlib.sha256(handle.encode()).hexdigest()[:24]
                result[handle] = {
                    "allowed": True,
                    "reason": "eligible_new_crm_absent",
                    "person_id": f"person_x_{key}",
                    "new_contact": True,
                }
                continue
            if len({row["person_id"] for row in rows}) != 1:
                result[handle] = {
                    "allowed": False,
                    "reason": "identity_conflicting",
                }
                continue
            row = rows[0]
            person_id = row["person_id"]
            blockers = []
            if row["verification_status"] != "confirmed" or row["identity_status"] != "verified":
                blockers.append("identity_unverified")
            if row["research_status"] != "complete" or person_id in conflicts or person_id in tasks:
                blockers.append("history_reconciliation_required")
            if person_id in suppressions:
                blockers.append("suppressed")
            assessment = outreach_rules.assess("x", events[person_id], now=at)
            blockers.extend(assessment["blockers"])
            result[handle] = {
                "allowed": not blockers,
                "reason": ",".join(sorted(set(blockers))) if blockers else "eligible",
                "person_id": person_id,
                "new_contact": False,
            }
        return result


def validate_capture(capture: dict) -> list[dict]:
    """Require proof that every visible X Pro column reached the cutoff."""
    if not isinstance(capture, dict):
        raise TypeError("capture must include all-column scan metadata")
    if normalize_handle(str(capture.get("account") or "")) != "jasonfesta":
        raise ValueError("capture account must be @jasonfesta")
    expected = capture.get("expected_columns")
    columns = capture.get("columns")
    rows = capture.get("posts")
    if not isinstance(expected, list) or not expected:
        raise ValueError("expected_columns must list every visible X Pro column")
    if not isinstance(columns, list) or not isinstance(rows, list):
        raise TypeError("capture must include columns and posts lists")
    expected_names = [str(name).strip() for name in expected]
    if any(not name for name in expected_names) or len(set(expected_names)) != len(expected_names):
        raise ValueError("expected_columns must be non-empty and unique")
    observed = {}
    for column in columns:
        if not isinstance(column, dict):
            raise TypeError("column scan metadata must be objects")
        name = str(column.get("name") or "").strip()
        if not name or name in observed:
            raise ValueError("column scan names must be non-empty and unique")
        observed[name] = column
    if set(observed) != set(expected_names):
        missing = sorted(set(expected_names) - set(observed))
        extra = sorted(set(observed) - set(expected_names))
        raise ValueError(f"all columns required; missing={missing}, extra={extra}")
    incomplete = sorted(
        name
        for name, column in observed.items()
        if column.get("scanned") is not True or column.get("cutoff_reached") is not True
    )
    if incomplete:
        raise ValueError(f"all columns must reach the five-minute cutoff: {incomplete}")
    return rows


def build_plan(capture, *, at, portkey, guard, handled_handles=None, keywords=None):
    rows = validate_capture(capture)
    posts = compact_window(rows, at=at)
    abuse = abusive_handles(posts)
    handled = {normalize_handle(value) for value in (handled_handles or set())}
    remaining = [p for p in posts if p.handle not in abuse and p.handle not in handled]
    checks = guard.check({post.handle for post in remaining}, at=at)
    remaining = [post for post in remaining if checks[post.handle]["allowed"]]
    terms = keywords or load_keywords()
    exact, contextual = [], []
    for post in remaining:
        if post.lane.casefold().startswith("reads_") or any(
            term in post.text.casefold() for term in terms
        ):
            exact.append(post)
        else:
            contextual.append(post)
    selected_ids = portkey.classify(contextual) if contextual else set()
    candidates = exact + [post for post in contextual if post.id in selected_ids]
    newest = {}
    for post in sorted(candidates, key=lambda item: item.created_at, reverse=True):
        newest.setdefault(post.handle, post)
    eligible = list(newest.values())
    replies = portkey.draft(eligible) if eligible else {}
    return {
        "created_at": stamp(at),
        "window_seconds": WINDOW_SECONDS,
        "abusive_handles": sorted(abuse),
        "held": {
            handle: check["reason"] for handle, check in checks.items() if not check["allowed"]
        },
        "items": [
            {
                **asdict(post),
                "person_id": checks[post.handle]["person_id"],
                "new_contact": checks[post.handle].get("new_contact", False),
                "reply": replies[post.id],
            }
            for post in eligible
        ],
    }


def record_confirmed(plan: dict, confirmations: list[dict], connection_factory=database.connect):
    by_id = {item["id"]: item for item in plan.get("items", [])}
    if len(by_id) != len(plan.get("items", [])):
        raise ValueError("plan IDs must be unique")
    written = []
    for confirmation in confirmations:
        item = by_id.get(str(confirmation.get("id")))
        if not item:
            raise ValueError("confirmation is not in the plan")
        required = (
            "reply_url",
            "provider_id",
            "account",
            "account_profile_url",
            "observed_at",
        )
        if confirmation.get("status") != "confirmed" or any(
            not str(confirmation.get(key) or "").strip() for key in required
        ):
            raise ValueError("provider-confirmed X reply required before CRM write")
        if confirmation["account"] != "jasonfesta":
            raise ValueError("wrong X account")
        if confirmation["account_profile_url"] != "https://x.com/jasonfesta":
            raise ValueError("wrong X account profile")
        expected = f"https://x.com/jasonfesta/status/{confirmation['provider_id']}"
        if confirmation["reply_url"] != expected:
            raise ValueError("exact X reply permalink required")
        observed = stamp(utc(confirmation["observed_at"]))
        public = {
            "reply_url": confirmation["reply_url"],
            "parent_url": item["url"],
            "handle": item["handle"],
            "lane": item["lane"],
            "fingerprint": item["fingerprint"],
            "observed_at": observed,
            "account_profile_url": confirmation["account_profile_url"],
        }
        key = hashlib.sha256(confirmation["reply_url"].encode()).hexdigest()[:24]
        event_id, source_id, claim_id = f"evt_x_{key}", f"src_x_{key}", f"reply_x_{key}"
        connection = connection_factory()
        try:
            with connection:
                person_exists = connection.execute(
                    "SELECT 1 FROM people WHERE person_id=?", (item["person_id"],)
                ).fetchone()
                if not person_exists and item.get("new_contact"):
                    profile_url = f"https://x.com/{item['handle']}"
                    profile_key = hashlib.sha256(profile_url.encode()).hexdigest()[:24]
                    profile_source = f"src_x_profile_{profile_key}"
                    connection.execute(
                        "INSERT OR IGNORE INTO sources(source_id,url,source_type,quality_tier,accessed_at) VALUES (?,?,'x',2,?)",
                        (profile_source, profile_url, observed),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO people(person_id,full_name,normalized_name,identity_status,research_status,confidence) VALUES (?,?,?,'verified','complete',100)",
                        (item["person_id"], f"@{item['handle']}", item["handle"]),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO contact_points(contact_id,person_id,contact_type,value,normalized_value,verification_status,verification_method,confidence,is_primary,is_public,source_id,first_seen_at,last_verified_at) VALUES (?,?,'x',?,?,'confirmed','observed_x_post',100,1,1,?,?,?)",
                        (
                            f"contact_x_{profile_key}",
                            item["person_id"],
                            profile_url,
                            item["handle"],
                            profile_source,
                            observed,
                            observed,
                        ),
                    )
                elif not person_exists:
                    raise ValueError("CRM person disappeared before receipt write")
                existing = connection.execute(
                    "SELECT event_id FROM outreach_events WHERE event_id=?", (event_id,)
                ).fetchone()
                if not existing:
                    connection.execute(
                        "INSERT OR IGNORE INTO sources(source_id,url,source_type,quality_tier,accessed_at) VALUES (?,?,'x',2,?)",
                        (source_id, confirmation["reply_url"], observed),
                    )
                    connection.execute(
                        "INSERT INTO evidence_claims(claim_id,entity_type,entity_id,field_name,claim_value,source_id,evidence_relation,verification_status,confidence) VALUES (?,'person',?,'public_reply_location',?,?,'mentions','accepted',100)",
                        (
                            claim_id,
                            item["person_id"],
                            json.dumps(public, sort_keys=True),
                            source_id,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO outreach_events(event_id,person_id,channel,direction,occurred_at,outcome,external_reference,notes) VALUES (?,?,'x','outbound',?,'sent',?,?)",
                        (
                            event_id,
                            item["person_id"],
                            observed,
                            confirmation["reply_url"],
                            json.dumps(public, sort_keys=True),
                        ),
                    )
            written.append(event_id)
        finally:
            connection.close()
    return written


class BrowserLease:
    def __init__(self, path=LEASE_DB, *, scope="social-browser"):
        self.path = Path(path)
        if not scope or not scope.strip():
            raise ValueError("Browser lease scope must not be empty")
        self.scope = scope

    def acquire(self, owner: str, *, at=None, ttl=LEASE_SECONDS):
        now = utc(at)
        token = str(uuid.uuid4())
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM browser_lease WHERE scope=?", (self.scope,)
            ).fetchone()
            if row and utc(row["expires_at"]) > now:
                return {
                    "acquired": False,
                    "owner": row["owner"],
                    "expires_at": row["expires_at"],
                }
            fence = connection.execute(
                "SELECT last_fence FROM lease_fence WHERE scope=?", (self.scope,)
            ).fetchone()
            fence = (fence[0] if fence else 0) + 1
            connection.execute(
                "INSERT INTO lease_fence(scope,last_fence) VALUES (?,?) "
                "ON CONFLICT(scope) DO UPDATE SET last_fence=excluded.last_fence",
                (self.scope, fence),
            )
            connection.execute("DELETE FROM browser_lease WHERE scope=?", (self.scope,))
            connection.execute(
                "INSERT INTO browser_lease(scope,token,owner,started_at,renewed_at,expires_at,fence,read_budget_seconds) VALUES (?,?,?,?,?,?,?,480)",
                (
                    self.scope,
                    token,
                    owner,
                    stamp(now),
                    stamp(now),
                    stamp(now + dt.timedelta(seconds=ttl)),
                    fence,
                ),
            )
        return {"acquired": True, "token": token, "owner": owner, "fence": fence}

    def release(self, owner: str, token: str, fence: int):
        with sqlite3.connect(self.path, timeout=10) as connection:
            cursor = connection.execute(
                "DELETE FROM browser_lease WHERE scope=? AND owner=? AND token=? AND fence=?",
                (self.scope, owner, token, fence),
            )
        return {"released": cursor.rowcount == 1}


def read_json(path):
    return json.loads(Path(path).read_text())


def emit(value, output=None):
    rendered = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if output:
        Path(output).write_text(rendered)
    else:
        print(rendered, end="")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--capture", required=True)
    plan.add_argument("--at")
    plan.add_argument("--output")
    record = sub.add_parser("record")
    record.add_argument("--plan", required=True)
    record.add_argument("--confirmations", required=True)
    lease = sub.add_parser("lease")
    lease_sub = lease.add_subparsers(dest="lease_command", required=True)
    acquire = lease_sub.add_parser("acquire")
    acquire.add_argument("--owner", required=True)
    acquire.add_argument("--scope", default="social-browser")
    release = lease_sub.add_parser("release")
    release.add_argument("--owner", required=True)
    release.add_argument("--scope", default="social-browser")
    release.add_argument("--token", required=True)
    release.add_argument("--fence", required=True, type=int)
    args = parser.parse_args(argv)
    if args.command == "plan":
        capture = read_json(args.capture)
        emit(
            build_plan(capture, at=utc(args.at), portkey=PortkeyClient(), guard=CRMGuard()),
            args.output,
        )
    elif args.command == "record":
        confirmations = read_json(args.confirmations)
        if not isinstance(confirmations, list):
            raise ValueError("confirmations must be a list")
        emit({"recorded": record_confirmed(read_json(args.plan), confirmations)})
    elif args.lease_command == "acquire":
        emit(BrowserLease(scope=args.scope).acquire(args.owner))
    else:
        emit(BrowserLease(scope=args.scope).release(args.owner, args.token, args.fence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
