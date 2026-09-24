"""Private LinkedIn/X timeline discovery ledger. No network reader, composer, or send capability.

Input is a reviewed capture from supported browser tools, an authorized API or manual observation.
The operating Codex writes original replies; this module validates and records them.
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import database, outreach_rules
from .contact_normalization import norm as normalize_contact

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "x/data/timeline-watcher/ledger.sqlite3"
TOPICS = re.compile(
    r"\b(tool discovery|ai discovery|agent discovery|agentic payments|"
    r"agent payments|agent search|ai search|tool search|openclaw|mcp|x402|ai|agents?|agentic|artificial intelligence|"
    r"assistant (?:devs?|developers?|engineers?|builders?|sdk)|building an assistant|"
    r"(?:coding|voice) assistants?|conversational ai|llm(?:s| (?:devs?|developers?|engineers?|apps?|applications?))?|"
    r"model context protocol|tool calling|function calling|open[- ]source (?:assistants?|mcp))\b",
    re.I,
)
SEARCH_CATALOG = json.loads((ROOT / "config/timeline-search-keywords.json").read_text())
SEARCH_TERMS = {
    entry[channel] for entry in SEARCH_CATALOG["entries"] for channel in SEARCH_CATALOG["channels"]
}
# Preserve legacy matches while admitting the expanded, versioned discovery vocabulary.
# A topic match still cannot bypass identity, context, qualification or history checks.
TOPICS = re.compile(
    TOPICS.pattern
    + r"|\b(?:"
    + "|".join(re.escape(term) for term in sorted(SEARCH_TERMS, key=lambda t: (-len(t), t)))
    + r")\b",
    re.I,
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def age(value, at):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("timezone required")
    return (datetime.fromisoformat(at.replace("Z", "+00:00")) - stamp).total_seconds()


def fresh(value, at, limit):
    if not 0 <= age(value, at) <= limit:
        raise ValueError("stale or future evidence")


def copy_context():
    paths = [
        ROOT / "copy/copy.md",
        ROOT / "copy/brief.md",
        ROOT / "copy/WORKFLOW.md",
        ROOT / "UTM/FORMATION_RULES.md",
    ]
    return {
        p.name: {"text": p.read_text(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        for p in paths
    }


def binding(capture, expected, at):
    b = capture["binding"]
    if b["account"] != expected or (
        not b.get("user_id") and capture.get("route") != "browser_observation"
    ):
        raise ValueError("wrong or unknown operator account")
    if b["method"] not in (
        "api_users_me",
        "authorized_api_self",
        "manual_self_observation",
        "browser_self_observation",
    ):
        raise ValueError("unsupported identity proof")
    if not b.get("evidence"):
        raise ValueError("account identity receipt missing")
    fresh(b["observed_at"], at, 900)
    return b


def validate_post(p, at, *, research=False):
    if not re.fullmatch(r"[0-9]+", p["id"]) or not p["author_id"]:
        raise ValueError("stable post and author IDs required")
    if p["channel"] == "x":
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", p["handle"]):
            raise ValueError("invalid exact handle")
        if p["url"] != f"https://x.com/{p['handle']}/status/{p['id']}":
            raise ValueError("post target mismatch")
        if p["profile_url"] != f"https://x.com/{p['handle']}":
            raise ValueError("profile target mismatch")
    elif p["channel"] == "linkedin":
        if p["url"] != f"https://www.linkedin.com/feed/update/urn:li:activity:{p['id']}/":
            raise ValueError("exact LinkedIn activity permalink required")
        if not re.fullmatch(r"https://www\.linkedin\.com/in/[^/?#]+/", p["profile_url"]):
            raise ValueError("exact personal LinkedIn profile required")
    else:
        raise ValueError("unsupported channel")
    if not p["text"].strip() or p.get("context_complete") is not True:
        raise ValueError("actual complete post, thread and media review required")
    if p.get("available") is not True:
        raise ValueError("deleted, protected or unavailable post")
    fresh(p["created_at"], at, 30 * 86400 if research else 900)
    fresh(p["observed_at"], at, 86400 if research else 300)


QUALIFICATION_GATES = (
    "suppression_identity_conflict",
    "person_identity",
    "bot",
    "fake",
    "spam",
    "crypto",
    "relevance",
)
SCORE_ANCHORS = {
    "relevance": (0, 10, 20, 30),
    "identity": (0, 10, 20, 25),
    "original_contribution": (0, 5, 10, 15, 20),
    "professional_fit": (0, 5, 10, 15),
    "recent_activity": (0, 5, 10),
}


def validate_qualification(review, post, at):
    q = review.get("qualification", {})
    if q.get("policy") != "social-discovery-v3" or not q.get("reviewer"):
        raise ValueError("complete social-discovery-v3 review required")
    fresh(q["reviewed_at"], at, 86400)
    evidence = q.get("evidence", {})

    def supported(item):
        refs = item.get("evidence_ids", [])
        return bool(
            item.get("reason")
            and refs
            and all(
                ref in evidence
                and evidence[ref].get("url", "").startswith("https://")
                and evidence[ref].get("text")
                for ref in refs
            )
        )

    gates = q.get("gates", {})
    for gate in QUALIFICATION_GATES:
        g = gates.get(gate, {})
        if g.get("status") != "pass" or not supported(g):
            raise ValueError("qualification gate held: " + gate)
    scores = q.get("scores", {})
    if set(scores) != set(SCORE_ANCHORS):
        raise ValueError("exactly five qualification dimensions required")
    for dimension, anchors in SCORE_ANCHORS.items():
        d = scores.get(dimension, {})
        if type(d.get("value")) is not int or d["value"] not in anchors or not supported(d):
            raise ValueError("incomplete score: " + dimension)
    values = {k: v["value"] for k, v in scores.items() if k in SCORE_ANCHORS}
    if (
        sum(values.values()) < 75
        or values["identity"] < 20
        or values["relevance"] < 20
        or values["professional_fit"] < 10
    ):
        raise ValueError("qualification score below admission floor")
    sample = q.get("authored_sample", [])
    if not 3 <= len(sample) <= 10 or len({x.get("url") for x in sample}) != len(sample):
        raise ValueError("three to ten distinct authored items required")
    if post["url"] not in {x["url"] for x in sample}:
        raise ValueError("authored sample must include triggering post")
    dates = set()
    recent = False
    for item in sample:
        if (
            item.get("author_id") != post["author_id"]
            or item.get("substantive") is not True
            or not supported(item)
        ):
            raise ValueError("authored sample identity or evidence missing")
        fresh(item["created_at"], at, 30 * 86400)
        dates.add(item["created_at"][:10])
        recent |= item.get("relevant") is True and age(item["created_at"], at) <= 7 * 86400
    if len(dates) < 2 or not recent:
        raise ValueError("authored sample needs two dates and relevant activity within seven days")
    hydration = q.get("hydration", {})
    if hydration.get("workflow") != "hydrate/SOCIAL_DISCOVERY.md" or not hydration.get("receipt"):
        raise ValueError("shared hydration workflow receipt required")
    if set(hydration.get("channel_outcomes", {})) != {"x", "linkedin", "email"} or not all(
        hydration["channel_outcomes"].values()
    ):
        raise ValueError("hydration channel outcomes required")


class Watcher:
    def __init__(self, path=LEDGER):
        path = Path(path)
        self.reports = path.parent / "records"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS posts(id TEXT PRIMARY KEY, current_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS observations(hash TEXT PRIMARY KEY, post_id TEXT NOT NULL,
            payload TEXT NOT NULL, disposition TEXT NOT NULL, reason TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS drafts(id TEXT PRIMARY KEY, post_id TEXT NOT NULL,
            observation_hash TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, draft_id TEXT NOT NULL,
            payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS imports(post_id TEXT PRIMARY KEY, state TEXT NOT NULL,
            person_id TEXT, error TEXT);
        """)

    def report(self, kind, payload):
        self.reports.mkdir(exist_ok=True, mode=0o700)
        target = self.reports / (kind + "-" + digest(payload) + ".md")
        if not target.exists():
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as out:
                out.write(
                    "# Timeline watcher " + kind + "\n\nPrivate operating record. "
                    "A draft or approval is not a send.\n\n"
                )
                out.write(
                    "    "
                    + json.dumps(payload, indent=2, ensure_ascii=False).replace("\n", "\n    ")
                    + "\n"
                )
        return str(target)

    def close(self):
        self.db.close()

    def ingest(self, capture, expected, at=None):
        at = at or now()
        binding(capture, expected, at)
        if capture["route"] not in (
            "manual_capture",
            "authorized_api_capture",
            "browser_observation",
        ):
            raise ValueError("unsupported capture route; use reviewed browser_observation receipts")
        if capture["route"] == "browser_observation":
            source = capture.get("source", {})
            if (
                capture["binding"]["method"] != "browser_self_observation"
                or not source.get("url", "").startswith("https://")
                or source.get("sort")
                not in ("Latest", "Recent", "newest_first_by_original_timestamp")
                or not source.get("evidence")
                or not source.get("query_or_column")
            ):
                raise ValueError("browser source, ordering and operator receipts required")
        if not 0 < len(capture["posts"]) <= 25:
            raise ValueError("freeze at most 25 posts per run")
        if len({p["id"] for p in capture["posts"]}) != len(capture["posts"]):
            raise ValueError("duplicate IDs in frozen cohort")
        run_id = digest(capture)
        outcomes = []
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO runs VALUES (?,?)", (run_id, json.dumps(capture))
            )
            for original in capture["posts"]:
                p = {
                    **original,
                    "_account": expected,
                    "_route": capture["route"],
                    "_source": capture.get("source"),
                }
                key = p["channel"] + ":" + p["id"]
                reason = ""
                try:
                    validate_post(p, at, research=capture["route"] == "browser_observation")
                    if p["profile_url"] == expected or (
                        p.get("author_id") and p["author_id"] == capture["binding"].get("user_id")
                    ):
                        raise ValueError("self post")
                    if not TOPICS.search(p["text"]) and not p.get("related_topic_reason"):
                        raise ValueError("no supported topic match")
                    state = "needs_identity_review"
                except ValueError as exc:
                    state, reason = "held", str(exc)
                h = digest(p)
                old = self.db.execute(
                    "SELECT current_hash FROM posts WHERE id=?", (key,)
                ).fetchone()
                self.db.execute(
                    "INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?)",
                    (h, key, json.dumps(p), state, reason),
                )
                self.db.execute(
                    "INSERT INTO posts VALUES (?,?) ON CONFLICT(id) DO UPDATE "
                    "SET current_hash=excluded.current_hash",
                    (key, h),
                )
                outcomes.append(
                    {
                        "post_id": key,
                        "observation_hash": h,
                        "disposition": state,
                        "reason": reason,
                        "duplicate": bool(old),
                        "changed": bool(old and old[0] != h),
                    }
                )
        result = {"run_id": run_id, "denominator": len(outcomes), "outcomes": outcomes}
        result["markdown"] = self.report("scan", result)
        return result

    def post(self, post_id, at, *, research=False):
        row = self.db.execute(
            "SELECT o.* FROM posts p JOIN observations o ON p.current_hash=o.hash WHERE p.id=?",
            (post_id,),
        ).fetchone()
        if not row or row["disposition"] == "held":
            raise ValueError("post missing or held")
        p = json.loads(row["payload"])
        validate_post(p, at, research=research)
        return p, row["hash"]

    def qualify(self, post_id, review, at, *, research=False):
        p, h = self.post(post_id, at, research=research)
        if (
            review["observation_hash"] != h
            or review["author_id"] != p["author_id"]
            or review["account"] != p["_account"]
        ):
            raise ValueError("review is not bound to current author/context")
        if review.get("identity_status") != "verified" or not review.get("relevance_reason"):
            raise ValueError("unverified or unrelated identity")
        if review.get("is_person") is not True or review.get("conflicts") != []:
            raise ValueError("organization or conflicting identity")
        proof = review["identity_proof"]
        if (
            proof.get("kind") != "official_self_link"
            or proof.get("profile_url") != p["profile_url"]
            or proof.get("name") != review["full_name"]
            or p["profile_url"] not in proof.get("links", [])
            or review["full_name"] not in proof.get("text", "")
            or not proof.get("source_url", "").startswith("https://")
        ):
            raise ValueError("named primary identity and exact self link required")
        fresh(proof["observed_at"], at, 86400)
        if set(review["channels"]) != {"x", "linkedin", "email"}:
            raise ValueError("all channel outcomes required")
        if review["channels"][p["channel"]] != "supported_association":
            raise ValueError("source channel association must be supported")
        validate_qualification(review, p, at)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO reviews VALUES (?,?)", (digest(review), json.dumps(review))
            )
        return p

    def prepare(self, post_id, review, variants, critique, at=None):
        at = at or now()
        self.qualify(post_id, review, at)
        if set(variants) != {"a", "b"} or not critique.strip():
            raise ValueError("two Codex-authored alternatives and critique required")
        for text in variants.values():
            if not text.strip() or len(text) > 280 or len(text.split()) > 30:
                raise ValueError("reply length outside local limit")
            if re.search(r"https?://|www\.|utm_", text, re.I):
                raise ValueError("linked introduction held pending destination validation")
        if variants["a"] == variants["b"]:
            raise ValueError("alternatives must differ")
        _, h = self.post(post_id, at)
        payload = {
            "account": review["account"],
            "target_post_id": post_id,
            "target_url": self.post(post_id, at)[0]["url"],
            "observation_hash": h,
            "review": review,
            "variants": variants,
            "critique": critique,
            "guides": {k: v["sha256"] for k, v in copy_context().items()},
            "created_at": at,
            "authoring": "Codex",
            "calibration": "finalized_guide_no_example_calibration_claim",
            "state": "awaiting_exact_human_approval",
            "send_capable": False,
            "counts": {
                k: {"words": len(v.split()), "characters": len(v)} for k, v in variants.items()
            },
        }
        did = digest(payload)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO drafts VALUES (?,?,?,?)",
                (did, post_id, h, json.dumps(payload)),
            )
        result = {"draft_id": did, **payload}
        result["markdown"] = self.report("draft", result)
        return result

    def record_approval(self, draft_id, approval, at=None):
        """Record a real user decision, never synthesize it. This cannot enable sending."""
        at = at or now()
        row = self.db.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if not row:
            raise ValueError("unknown draft")
        d = json.loads(row["payload"])
        _, h = self.post(row["post_id"], at)
        if h != row["observation_hash"]:
            raise ValueError("context changed; new draft and approval required")
        if d["guides"] != {k: v["sha256"] for k, v in copy_context().items()}:
            raise ValueError("copy guides changed")
        if (
            approval.get("actor") != "human"
            or not approval.get("decision_reference")
            or approval.get("account") != d["account"]
            or approval.get("target_url") != d["target_url"]
            or approval.get("target_post_id") != d["target_post_id"]
            or approval.get("exact_text") not in d["variants"].values()
            or approval.get("decision") not in ("approved", "rejected")
        ):
            raise ValueError("individual exact-target/text human decision required")
        fresh(approval["decided_at"], at, 900)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO approvals VALUES (?,?,?)",
                (digest(approval), draft_id, json.dumps(approval)),
            )
        self.report("approval", {"draft_id": draft_id, **approval})
        return {"recorded": True, "send_capable": False}

    def import_lead(self, post_id, review, at=None, *, fixture_connection=None):
        at = at or now()
        p = self.qualify(post_id, review, at, research=True)
        previous = self.db.execute("SELECT * FROM imports WHERE post_id=?", (post_id,)).fetchone()
        if previous:
            if previous["state"] != "confirmed":
                raise RuntimeError("uncertain CRM import; reconcile adapter journal before retry")
            return previous["person_id"]
        if fixture_connection is None and not database.configured():
            raise RuntimeError("shared PostgreSQL required; no legacy fallback")
        with self.db:
            self.db.execute("INSERT INTO imports VALUES (?, ?, NULL, NULL)", (post_id, "pending"))
        c = None
        try:
            c = fixture_connection or database.connect()
            person_id = upsert_lead(c, p, review, at)
            c.commit()
            receipt = c.execute(
                "SELECT full_name FROM people WHERE person_id=?", (person_id,)
            ).fetchone()
            if not receipt or receipt["full_name"] != review["full_name"]:
                raise RuntimeError("CRM commit not confirmed by readback")
        except Exception as exc:
            if c is not None:
                c.rollback()
            with self.db:
                self.db.execute(
                    "UPDATE imports SET state=?,error=? WHERE post_id=?",
                    ("uncertain", type(exc).__name__, post_id),
                )
            raise
        finally:
            if fixture_connection is None and c is not None:
                c.close()
        with self.db:
            self.db.execute(
                "UPDATE imports SET state=?,person_id=? WHERE post_id=?",
                ("confirmed", person_id, post_id),
            )
        self.report(
            "crm-import",
            {"post_id": post_id, "person_id": person_id, "state": "confirmed", "observed_at": at},
        )
        return person_id


def upsert_lead(c, p, review, at):
    """Append provenance, reuse exact contact identities; never merge on names."""
    normalized = normalize_contact(p["channel"], p["profile_url"])
    matches = c.execute(
        "SELECT person_id FROM contact_points WHERE contact_type=? AND normalized_value=?",
        (p["channel"], normalized),
    ).fetchall()
    ids = {r["person_id"] for r in matches}
    alias = p["channel"] + "-user-id:" + p["author_id"]
    ids.update(
        r["entity_id"]
        for r in c.execute(
            "SELECT entity_id FROM aliases WHERE entity_type='person' AND normalized_alias=?",
            (alias,),
        )
    )
    if len(ids) > 1:
        raise ValueError("conflicting CRM identity mapping")
    pid = next(iter(ids), "person_" + digest(alias)[:24])
    if ids:
        existing = c.execute("SELECT full_name FROM people WHERE person_id=?", (pid,)).fetchone()
        if existing["full_name"].casefold().strip() != review["full_name"].casefold().strip():
            raise ValueError("existing contact name conflict requires reconciliation")
        old_aliases = c.execute(
            "SELECT normalized_alias FROM aliases WHERE entity_type='person' AND entity_id=?",
            (pid,),
        ).fetchall()
        prefix = p["channel"] + "-user-id:"
        if any(
            r["normalized_alias"].startswith(prefix) and r["normalized_alias"] != alias
            for r in old_aliases
        ):
            raise ValueError("reassigned handle or conflicting stable author ID")
    if not ids:
        same_name = c.execute(
            "SELECT person_id FROM people WHERE normalized_name=?",
            (review["full_name"].casefold().strip(),),
        ).fetchone()
        if same_name:
            raise ValueError("existing namesake needs identity reconciliation")
    if ids:
        assessment = outreach_rules.person_assessment(c, pid, p["channel"])
        if assessment["blockers"]:
            raise ValueError(
                "shared suppression/history hold: " + ", ".join(assessment["blockers"])
            )
    c.execute(
        "INSERT OR IGNORE INTO people "
        "(person_id,full_name,normalized_name,identity_status,research_status) VALUES "
        "(?,?,?,'verified','review_needed')",
        (pid, review["full_name"], review["full_name"].casefold().strip()),
    )
    source_ids = []
    for url, kind in [
        (p["url"], p["channel"]),
        (review["identity_proof"]["source_url"], "official_site"),
    ]:
        sid = "source_" + digest(url)[:24]
        c.execute(
            "INSERT OR IGNORE INTO sources "
            "(source_id,url,source_type,quality_tier,accessed_at) VALUES (?,?,?,?,?)",
            (sid, url, kind, 1, at),
        )
        sid = c.execute("SELECT source_id FROM sources WHERE url=?", (url,)).fetchone()["source_id"]
        source_ids.append(sid)
    c.execute(
        "INSERT OR IGNORE INTO aliases "
        "(alias_id,entity_type,entity_id,alias,normalized_alias,source_id) "
        "VALUES (?,'person',?,?,?,?)",
        ("alias_" + digest([pid, alias])[:24], pid, alias, alias, source_ids[0]),
    )
    c.execute(
        "INSERT OR IGNORE INTO contact_points "
        "(contact_id,person_id,contact_type,value,normalized_value,verification_status,"
        "verification_method,source_id,first_seen_at,last_verified_at) "
        "VALUES (?,?,?,?,?,'confirmed','official_self_link',?,?,?)",
        (
            "contact_" + digest([pid, normalized])[:24],
            pid,
            p["channel"],
            p["profile_url"],
            normalized,
            source_ids[1],
            at,
            at,
        ),
    )
    c.execute(
        "INSERT OR IGNORE INTO evidence_claims "
        "(claim_id,entity_type,entity_id,field_name,claim_value,source_id,"
        "verification_status,confidence) VALUES "
        "(?,'person',?,'social_author_id',?,?,'accepted',100)",
        ("claim_" + digest([pid, p["id"]])[:24], pid, p["author_id"], source_ids[0]),
    )
    return pid


def readiness():
    return {
        "ready": True,
        "readiness_scope": "local browser capture/review interface; live account and source checked each run",
        "mode": "bounded_browser_observation_and_review",
        "send_capable": False,
        "interval_minutes": 15,
        "blockers": [],
        "per_run_requirements": [
            "Verify signed-in operator and latest source ordering through supported browser tools",
            "Hold incomplete context, unknown stable author IDs and incomplete qualification",
            "Use shared hydration review and read-back-confirmed CRM imports",
        ],
        "coverage": "No live coverage inferred from status; each browser run supplies its own receipts",
        "local_path": "browser_observation -> private evidence -> hydration review -> qualified CRM import -> chat approval",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "context", "ingest", "review"])
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--write-md", action="store_true")
    parser.add_argument("--channel", choices=["x", "linkedin"], default="x")
    args = parser.parse_args()
    if args.command == "status":
        result = readiness()
        if args.write_md:
            watcher = Watcher()
            try:
                result["markdown"] = watcher.report("readiness", {"checked_at": now(), **result})
            finally:
                watcher.close()
    elif args.command == "context":
        result = copy_context()
    else:
        operator = json.loads((ROOT / "accounts/operator.json").read_text())
        watcher = Watcher()
        try:
            expected = operator["accounts"][args.channel]["expected_profile_url"]
            if args.command == "ingest":
                result = watcher.ingest(json.loads(args.capture.read_text()), expected)
            else:
                data = json.loads(args.review.read_text())
                if data["review"]["account"] != expected:
                    raise ValueError("review operator mismatch")
                person_id = watcher.import_lead(data["post_id"], data["review"])
                result = watcher.prepare(
                    data["post_id"], data["review"], data["variants"], data["critique"]
                )
                result["person_id"] = person_id
        finally:
            watcher.close()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
