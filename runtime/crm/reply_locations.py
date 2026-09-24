"""Confirmed public replies: shared CRM locations and private revisit history."""

from urllib.parse import unquote, urlsplit

from crm.human_inbox import Watcher, digest, encode, now, required, stamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS public_reply_locations (
 location_id TEXT PRIMARY KEY, facts TEXT NOT NULL, evidence TEXT NOT NULL,
 shared_state TEXT NOT NULL DEFAULT 'pending', shared_source_id TEXT,
 shared_claim_id TEXT, UNIQUE(shared_claim_id));
CREATE TABLE IF NOT EXISTS public_reply_visits (
 visit_id TEXT PRIMARY KEY, location_id TEXT NOT NULL, checked_at TEXT NOT NULL,
 outcome TEXT NOT NULL, reply_events TEXT NOT NULL, evidence TEXT NOT NULL);
"""


def public_url(value, channel):
    required(value)
    parsed = urlsplit(value)
    hosts = {
        "x": {"x.com", "www.x.com", "twitter.com", "www.twitter.com"},
        "linkedin": {"www.linkedin.com", "linkedin.com"},
    }
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts[channel]
        or parsed.username
        or parsed.password
    ):
        raise ValueError("exact public platform URL required")
    if channel == "linkedin" and "/messaging" in parsed.path:
        raise ValueError("private conversation is not a public reply")
    return value


class ReplyLocations:
    def __init__(self, watcher=None):
        self.watcher = watcher or Watcher()
        with self.watcher.connection() as c:
            c.executescript(SCHEMA)

    def track(self, facts, evidence, *, shared_factory=None):
        """Read back our published reply first. Drafts/attempts cannot enter this list.

        Public locations use existing shared sources/evidence claims. Observed time
        is not invented posting time; detailed readbacks and visit history stay local.
        """
        from crm.database import configured, connect

        required(evidence)
        for key in (
            "channel",
            "account",
            "account_profile_url",
            "author_profile_url",
            "provider_id",
            "reply_url",
            "parent_url",
            "conversation_url",
            "person_id",
            "observed_at",
        ):
            required(facts.get(key))
        if facts["channel"] not in {"x", "linkedin"}:
            raise ValueError("public social channel required")
        if facts.get("confirmation") != "provider_readback" or facts.get("status") != "confirmed":
            raise ValueError("confirmed published reply required")
        if not facts.get("task_evidence"):
            raise ValueError("confirmed connection to this AI outreach task required")
        if facts["account_profile_url"] != facts["author_profile_url"]:
            raise ValueError("wrong author account")
        stamp(facts["observed_at"])
        if facts.get("posted_at"):
            stamp(facts["posted_at"])
        for key in ("reply_url", "parent_url", "conversation_url", "account_profile_url"):
            public_url(facts[key], facts["channel"])
        if facts["provider_id"] not in unquote(facts["reply_url"]):
            raise ValueError("provider ID must appear in exact reply permalink")
        if facts["channel"] == "x":
            author_path = urlsplit(facts["account_profile_url"]).path.rstrip("/")
            if urlsplit(facts["reply_url"]).path != author_path + "/status/" + facts["provider_id"]:
                raise ValueError("X permalink must identify our exact reply and author")
        key = digest([facts["channel"], facts["account"], facts["provider_id"]])
        with self.watcher.connection() as c:
            old = c.execute(
                "SELECT * FROM public_reply_locations WHERE location_id=?", (key,)
            ).fetchone()
            if old and (old["facts"] != encode(facts) or old["evidence"] != evidence):
                raise ValueError("location conflict; preserve original and reconcile")
            c.execute(
                "INSERT OR IGNORE INTO public_reply_locations(location_id,facts,evidence) VALUES (?,?,?)",
                (key, encode(facts), evidence),
            )
        if shared_factory is None:
            if not configured():
                raise RuntimeError("shared CRM required; no legacy fallback")
            shared_factory = connect
        public_facts = {
            k: facts.get(k)
            for k in (
                "channel",
                "account_profile_url",
                "provider_id",
                "reply_url",
                "parent_url",
                "conversation_url",
                "posted_at",
                "observed_at",
                "task_evidence",
            )
        }
        claim_id = "reply_location_" + key[:24]
        shared = shared_factory()
        try:
            with shared:
                if not shared.execute(
                    "SELECT 1 FROM people WHERE person_id=?", (facts["person_id"],)
                ).fetchone():
                    raise ValueError("related person must already exist in shared CRM")
                existing = shared.execute(
                    "SELECT * FROM evidence_claims WHERE claim_id=?", (claim_id,)
                ).fetchone()
                if existing and (
                    existing["entity_id"] != facts["person_id"]
                    or existing["claim_value"] != encode(public_facts)
                ):
                    raise ValueError("shared reply location conflict")
                if old and old["shared_state"] == "pending" and not existing:
                    raise RuntimeError("reconcile prior pending shared commit before retry")
                source = shared.execute(
                    "SELECT source_id FROM sources WHERE url=?", (facts["reply_url"],)
                ).fetchone()
                source_id = source["source_id"] if source else "reply_location_src_" + key[:24]
                if not existing:
                    if not source:
                        shared.execute(
                            "INSERT INTO sources(source_id,url,source_type,quality_tier,accessed_at) VALUES (?,?,?,?,?)",
                            (
                                source_id,
                                facts["reply_url"],
                                facts["channel"],
                                2,
                                facts["observed_at"],
                            ),
                        )
                    shared.execute(
                        "INSERT INTO evidence_claims(claim_id,entity_type,entity_id,field_name,claim_value,source_id,evidence_relation,verification_status,confidence) VALUES (?,'person',?,'public_reply_location',?,?,'mentions','accepted',100)",
                        (claim_id, facts["person_id"], encode(public_facts), source_id),
                    )
            with shared:
                check = shared.execute(
                    "SELECT claim_value,source_id FROM evidence_claims WHERE claim_id=?",
                    (claim_id,),
                ).fetchone()
                if not check or check["claim_value"] != encode(public_facts):
                    raise RuntimeError("shared location readback not confirmed")
                source_id = check["source_id"]
        finally:
            shared.close()
        with self.watcher.connection() as c:
            c.execute(
                "UPDATE public_reply_locations SET shared_state='confirmed',shared_source_id=?,shared_claim_id=? WHERE location_id=?",
                (source_id, claim_id, key),
            )
        self.watcher.register_thread(
            facts["channel"], facts["account"], facts["provider_id"], evidence
        )
        return key

    def due(self, *, at=None, interval_seconds=900, limit=25):
        """Oldest unchecked first, including previously replied threads; no automatic closure."""
        import json

        if interval_seconds < 1 or not 1 <= limit <= 100:
            raise ValueError("bounded revisit required")
        when = stamp(at or now())
        with self.watcher.connection() as c:
            rows = c.execute(
                "SELECT l.*, MAX(v.checked_at) AS last_checked FROM public_reply_locations l LEFT JOIN public_reply_visits v USING(location_id) WHERE shared_state='confirmed' GROUP BY l.location_id ORDER BY COALESCE(MAX(v.checked_at),''),l.location_id"
            ).fetchall()
        return [
            dict(
                location_id=r["location_id"],
                **json.loads(r["facts"]),
                last_checked=r["last_checked"],
            )
            for r in rows
            if json.loads(r["facts"]).get("task_evidence")
            and (
                not r["last_checked"]
                or (when - stamp(r["last_checked"])).total_seconds() >= interval_seconds
            )
        ][:limit]

    def visit(
        self,
        location_id,
        *,
        visit_id,
        checked_at,
        observed_account,
        observed_url,
        outcome,
        reply_events,
        evidence,
    ):
        """Record direct readback. Empty visible UI is partial unless all replies loaded."""
        import json

        for value in (visit_id, checked_at, observed_account, observed_url, evidence):
            required(value)
        checked_at = stamp(checked_at).isoformat()
        if outcome not in {"no_replies", "replies", "partial", "unavailable", "deleted"}:
            raise ValueError("explicit read outcome required")
        if not isinstance(reply_events, list) or len(reply_events) != len(set(reply_events)):
            raise ValueError("distinct recorded incoming event IDs required")
        if (
            outcome == "replies"
            and not reply_events
            or outcome in {"no_replies", "unavailable", "deleted"}
            and reply_events
        ):
            raise ValueError("outcome contradicts reply evidence")
        with self.watcher.connection() as c:
            row = c.execute(
                "SELECT facts FROM public_reply_locations WHERE location_id=?", (location_id,)
            ).fetchone()
            if not row:
                raise ValueError("unknown tracked reply")
            facts = json.loads(row[0])
            if observed_account != facts["account"] or observed_url != facts["reply_url"]:
                raise ValueError("must revisit exact reply under correct account")
            for event_id in reply_events:
                m, _, _, _ = self.watcher._state(c, event_id)
                if (
                    m["channel"] != facts["channel"]
                    or m["account"] != facts["account"]
                    or m.get("root_id") != facts["provider_id"]
                ):
                    raise ValueError("incoming reply belongs to another location")
            values = (visit_id, location_id, checked_at, outcome, encode(reply_events), evidence)
            old = c.execute(
                "SELECT * FROM public_reply_visits WHERE visit_id=?", (visit_id,)
            ).fetchone()
            if old and tuple(old) != values:
                raise ValueError("immutable visit conflict")
            c.execute("INSERT OR IGNORE INTO public_reply_visits VALUES (?,?,?,?,?,?)", values)
