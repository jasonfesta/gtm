"""Private daily preparation and shared history contract. No sending or scheduling."""

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from crm.database import CANONICAL, ROOT, configured, connect, public_value
from crm.outreach_rules import load_policy, person_assessment

CHANNELS = ("email", "x", "linkedin")


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def moment(value):
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed.astimezone(dt.timezone.utc)


class CRMHistory:
    """Canonical facts through the maintained adapter; explicit fixtures only in tests."""

    def __init__(self, db=CANONICAL, *, synthetic=False):
        self.db = Path(db)
        if not synthetic and not configured(self.db):
            raise RuntimeError("configured shared CRM required; no legacy fallback")

    def snapshot(self, person_id):
        with closing(connect(self.db)) as conn:
            person = conn.execute("SELECT * FROM people WHERE person_id=?", (person_id,)).fetchone()
            if not person:
                raise ValueError("unknown fetched person")
            contacts = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM contact_points WHERE person_id=?", (person_id,)
                )
            ]
            review = conn.execute(
                "SELECT * FROM latest_route_reviews WHERE person_id=?", (person_id,)
            ).fetchone()
            return {
                "person_id": person_id,
                "company_id": person["primary_company_id"],
                "contacts": contacts,
                "review": dict(review) if review else None,
                "channels": {ch: person_assessment(conn, person_id, ch) for ch in CHANNELS},
            }

    def record(self, event):
        """Stable receipt ID is replay-safe. Shared payload excludes private sender/evidence."""
        columns = (
            "event_id",
            "person_id",
            "channel",
            "direction",
            "occurred_at",
            "outcome",
            "external_reference",
        )
        values = tuple(event[k] for k in columns)
        with closing(connect(self.db)) as conn:
            with conn:
                prior = conn.execute(
                    "SELECT * FROM outreach_events WHERE event_id=?", (event["event_id"],)
                ).fetchone()
                if prior:
                    if tuple(prior[k] for k in columns) != tuple(
                        public_value("outreach_events", k, event[k])
                        if configured(self.db)
                        else event[k]
                        for k in columns
                    ):
                        raise ValueError("receipt identity conflict")
                    return
                conn.execute(
                    "INSERT INTO outreach_events ("
                    + ",".join(columns)
                    + ") VALUES (?,?,?,?,?,?,?)",
                    values,
                )


class DailyOutbound:
    def __init__(self, history, path=ROOT / "data/daily-outbound.sqlite3"):
        self.history = history
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS batches (
                    batch_id TEXT PRIMARY KEY, owner TEXT NOT NULL, day TEXT NOT NULL,
                    manifest TEXT NOT NULL, UNIQUE(owner,day));
                CREATE TABLE IF NOT EXISTS selections (
                    batch_id TEXT NOT NULL, person_id TEXT NOT NULL, snapshot TEXT NOT NULL,
                    hold TEXT NOT NULL, PRIMARY KEY(batch_id,person_id));
                CREATE TABLE IF NOT EXISTS claims (
                    identity TEXT PRIMARY KEY, batch_id TEXT NOT NULL, owner TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                    batch_id TEXT NOT NULL, person_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    payload TEXT NOT NULL, PRIMARY KEY(batch_id,person_id,revision));
                CREATE TABLE IF NOT EXISTS drafts (
                    batch_id TEXT NOT NULL, person_id TEXT NOT NULL, channel TEXT NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(batch_id,person_id,channel,revision));
                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history_journal (
                    event_id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL);
            """)
            for table in ("batches", "selections", "claims", "reviews", "drafts", "plans"):
                for action in ("UPDATE", "DELETE"):
                    conn.execute(
                        f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action} "
                        f"BEFORE {action} ON {table} BEGIN "
                        "SELECT RAISE(ABORT,'append-only daily receipt'); END"
                    )
        self.path.chmod(0o600)

    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return LocalConnection(conn)

    def freeze(self, manifest):
        """Accept upstream fetch receipt; never crawl or pad the CRM pool."""
        batch, owner, day = (manifest[k] for k in ("batch_id", "owner", "day"))
        dt.date.fromisoformat(day)
        people = manifest["person_ids"]
        if not batch or not owner or not manifest.get("source_refs"):
            raise ValueError("batch, sender owner and source receipt required")
        if not 1 <= len(people) <= 25 or len(set(people)) != len(people):
            raise ValueError("fetch requires 1–25 unique people")
        if len(people) != 25 and not manifest.get("short_batch_reason"):
            raise ValueError("short batch requires explicit reason; never pad")
        raw = encoded(manifest)
        with self.connection() as conn:
            prior = conn.execute(
                "SELECT manifest FROM batches WHERE batch_id=?", (batch,)
            ).fetchone()
            if prior:
                if prior[0] != raw:
                    raise ValueError("frozen batch conflict")
                return self.report(batch)
        snapshots = [self.history.snapshot(p) for p in people]
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Concurrent identical restarts may arrive after the initial read.
            prior = conn.execute(
                "SELECT manifest FROM batches WHERE batch_id=?", (batch,)
            ).fetchone()
            if prior:
                if prior[0] != raw:
                    raise ValueError("frozen batch conflict")
            else:
                conn.execute("INSERT INTO batches VALUES (?,?,?,?)", (batch, owner, day, raw))
                for snapshot in snapshots:
                    identities = ["person:" + snapshot["person_id"]]
                    if snapshot["company_id"]:
                        identities.append("company:" + snapshot["company_id"])
                    identities += [
                        "contact:" + c["contact_type"] + ":" + c["value"].strip().lower()
                        for c in snapshot["contacts"]
                    ]
                    conflict = any(
                        conn.execute("SELECT 1 FROM claims WHERE identity=?", (i,)).fetchone()
                        for i in identities
                    )
                    hold = "previous_person_contact_or_company_selection" if conflict else ""
                    conn.execute(
                        "INSERT INTO selections VALUES (?,?,?,?)",
                        (batch, snapshot["person_id"], encoded(snapshot), hold),
                    )
                    if not conflict:
                        for identity in identities:
                            conn.execute(
                                "INSERT OR IGNORE INTO claims VALUES (?,?,?)",
                                (identity, batch, owner),
                            )
        return self.report(batch)

    def review(self, batch, person, payload):
        """Save full selected-person research, including unavailable channel outcomes."""
        if set(payload["channels"]) != set(CHANNELS):
            raise ValueError("review all three channels")
        moment(payload["researched_at"])
        if any(type(payload.get(k)) is not bool for k in ("identity_confirmed", "developer_fit")):
            raise ValueError("explicit identity and campaign-fit decisions required")
        for key in ("identity_evidence", "relevance_evidence", "history_receipt", "route_reason"):
            if not payload.get(key):
                raise ValueError("research evidence required: " + key)
        if payload["route"] not in ("contact", "location", "social_email", "email", "provisional"):
            raise ValueError("unsupported route")
        for channel, item in payload["channels"].items():
            if type(item.get("available")) is not bool:
                raise ValueError("explicit channel availability required")
            if not item.get("reason") or type(item.get("score")) is not int:
                raise ValueError("each channel needs score and reason")
            if item.get("available"):
                if not item.get("exact_target") or not item.get("evidence_refs"):
                    raise ValueError("supported exact target and evidence required")
                if item.get("format") not in (
                    ("email",) if channel == "email" else ("dm", "public_reply")
                ):
                    raise ValueError("unsupported action format")
                if item["format"] == "public_reply" and not all(
                    item.get(k) for k in ("post_url", "post_text")
                ):
                    raise ValueError("actual post required")
        return self._append("reviews", batch, person, payload)

    def _append(self, table, batch, person, payload, channel=None):
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM selections WHERE batch_id=? AND person_id=?", (batch, person)
            ).fetchone():
                raise ValueError("person outside frozen batch")
            where, args = "batch_id=? AND person_id=?", [batch, person]
            if channel:
                where += " AND channel=?"
                args.append(channel)
            prior = conn.execute(
                f"SELECT revision,payload FROM {table} WHERE {where} "
                "ORDER BY revision DESC LIMIT 1",
                args,
            ).fetchone()
            if prior and prior["payload"] == encoded(payload):
                return prior["revision"]
            revision = prior["revision"] + 1 if prior else 1
            args += [revision, encoded(payload)]
            conn.execute(f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in args) + ")", args)
            return revision

    def save_draft(self, batch, person, channel, payload):
        """Store exact private revision; never create provider drafts."""
        if channel not in CHANNELS:
            raise ValueError("invalid channel")
        review = self._latest_review(batch, person)
        item = review["channels"][channel]
        if not item.get("available"):
            raise ValueError("channel unavailable")
        if not payload.get("body", "").strip() or payload.get("variant") not in ("a", "b"):
            raise ValueError("exact body and selected variant required")
        for key in ("evidence_refs", "critique", "reviewer"):
            if not payload.get(key):
                raise ValueError("draft review required: " + key)
        moment(payload["reviewed_at"])
        data = dict(payload)
        data.update(
            format=item["format"],
            exact_target=item["exact_target"],
            research_sha256=digest(review),
            provisional=True,
            body_characters=len(payload["body"].strip()),
            body_words=len(payload["body"].split()),
        )
        if item["format"] == "public_reply":
            data.update(post_url=item["post_url"], post_text=item["post_text"])
        data["guides"] = {
            str(p.relative_to(ROOT.parent)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "copy/copy.md", ROOT / "UTM/FORMATION_RULES.md")
        }
        return self._append("drafts", batch, person, data, channel)

    def _latest_review(self, batch, person):
        with self.connection() as conn:
            row = conn.execute(
                "SELECT payload FROM reviews WHERE batch_id=? AND person_id=? "
                "ORDER BY revision DESC LIMIT 1",
                (batch, person),
            ).fetchone()
        if not row:
            raise ValueError("selected person research required")
        return json.loads(row[0])

    def observe(self, payload):
        """All strategies call this with verified receipts; pending commits block planning."""
        for key in (
            "event_id",
            "person_id",
            "external_reference",
            "account",
            "strategy",
            "evidence_ref",
        ):
            if not payload.get(key):
                raise ValueError("receipt field required: " + key)
        if payload["strategy"] not in ("daily_outbound", "timeline_engagement", "incoming_replies"):
            raise ValueError("unknown strategy")
        if payload["channel"] not in CHANNELS or payload["direction"] not in (
            "inbound",
            "outbound",
        ):
            raise ValueError("invalid receipt channel/direction")
        if payload["outcome"] not in (
            "sent",
            "delivered",
            "replied",
            "bounced",
            "declined",
            "opted_out",
            "unknown",
        ):
            raise ValueError("actual outcome required; plans are not receipts")
        moment(payload["occurred_at"])
        raw = encoded(payload)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT * FROM history_journal WHERE event_id=?", (payload["event_id"],)
            ).fetchone()
            if previous and previous["payload"] != raw:
                raise ValueError("immutable receipt conflict")
            if previous and previous["state"] == "confirmed":
                return "confirmed"
            conn.execute(
                "INSERT OR IGNORE INTO history_journal VALUES (?,?,?)",
                (payload["event_id"], raw, "pending"),
            )
        self.history.record(payload)
        with self.connection() as conn:
            conn.execute(
                "UPDATE history_journal SET state='confirmed' WHERE event_id=?",
                (payload["event_id"],),
            )
        return "confirmed"

    def consume_incoming(self, watcher, *, limit=100):
        """Bridge reviewed reply-watcher signals; acknowledge only after shared commit."""
        consumer = "daily_outbound_shared_history_v1"
        result = {"confirmed": 0, "automated": 0, "held": 0}
        outcomes = {
            "human": "replied",
            "engagement": "replied",
            "opt_out": "opted_out",
            "decline": "declined",
            "bounce": "bounced",
        }
        for signal in watcher.pending(consumer, limit=limit):
            classification = signal["classification"]
            if classification == "automated":
                watcher.acknowledge(consumer, signal["signal_id"])
                result["automated"] += 1
                continue
            if not signal.get("person_id") or classification not in outcomes:
                result["held"] += 1
                continue
            payload = dict(
                event_id="inbound_" + digest([signal["event_id"], classification]),
                person_id=signal["person_id"],
                channel="email" if signal["channel"] == "gmail" else signal["channel"],
                direction="inbound",
                occurred_at=signal["occurred_at"],
                outcome=outcomes[classification],
                external_reference=signal["provider_id"],
                account=signal["account"],
                strategy="incoming_replies",
                evidence_ref=signal["evidence"],
            )
            self.observe(payload)
            watcher.acknowledge(consumer, signal["signal_id"])
            result["confirmed"] += 1
        return result

    def reconcile(self):
        with self.connection() as conn:
            pending = [
                json.loads(r[0])
                for r in conn.execute("SELECT payload FROM history_journal WHERE state='pending'")
            ]
        for payload in pending:
            self.observe(payload)
        return len(pending)

    def plan(self, batch, *, now, capacities):
        """Recompute a review queue from current shared facts, never send eligibility."""
        current = moment(now)
        report = self.report(batch)
        remaining = dict(capacities)
        remaining["linkedin:public_reply"] = min(20, remaining.get("linkedin:public_reply", 0))
        output = []
        for selection in report["people"]:
            person = selection["person_id"]
            try:
                research = self._latest_review(batch, person)
            except ValueError:
                output.append({"person_id": person, "holds": ["research_required"]})
                continue
            snapshot = self.history.snapshot(person)
            common = [selection["hold"]] if selection["hold"] else []
            if report["pending_history"]:
                common.append("shared_history_reconciliation_required")
            if not research.get("developer_fit") or not research.get("identity_confirmed"):
                common.append("campaign_identity_or_fit_hold")
            if research["route"] in ("contact", "location") and not research.get("route_evidence"):
                common.append("contact_or_location_evidence_required")
            if research["route"] == "provisional":
                common.append("route_provisional")
            if not snapshot["review"] or snapshot["review"]["readiness"] != "ready_for_preparation":
                common.append("crm_readiness_hold")
            if research.get("owner") != report["owner"]:
                common.append("sender_ownership_hold")
            age = current - moment(research["researched_at"])
            if age < dt.timedelta(0) or age >= dt.timedelta(hours=24):
                common.append("refresh_context_and_review")
            channels = CHANNELS
            next_selected = False
            for rank, channel in enumerate(channels, 1):
                item = research["channels"][channel]
                holds = list(common)
                holds += snapshot["channels"][channel]["blockers"]
                if not item.get("available"):
                    holds.append("channel_unavailable")
                elif not any(
                    c["contact_type"] == channel
                    and c["value"] == item["exact_target"]
                    and c["verification_status"] == "confirmed"
                    for c in snapshot["contacts"]
                ):
                    holds.append("canonical_contact_ownership_required")
                if channel != "email" and research["route"] == "social_email":
                    try:
                        activity_age = current - moment(item["activity_at"])
                        if not item.get("activity_url") or not dt.timedelta(
                            0
                        ) <= activity_age <= dt.timedelta(days=30):
                            holds.append("recent_authored_activity_required")
                    except (KeyError, ValueError, TypeError):
                        holds.append("recent_authored_activity_required")
                with self.connection() as conn:
                    draft = conn.execute(
                        "SELECT revision,payload FROM drafts WHERE batch_id=? "
                        "AND person_id=? AND channel=? ORDER BY revision DESC LIMIT 1",
                        (batch, person, channel),
                    ).fetchone()
                if not draft:
                    holds.append("draft_required")
                else:
                    data = json.loads(draft["payload"])
                    if data["research_sha256"] != digest(research):
                        holds.append("draft_context_changed")
                    reviewed_age = current - moment(data["reviewed_at"])
                    if reviewed_age < dt.timedelta(0) or reviewed_age >= dt.timedelta(hours=24):
                        holds.append("refresh_context_and_review")
                    for name, expected in data["guides"].items():
                        if (
                            hashlib.sha256((ROOT.parent / name).read_bytes()).hexdigest()
                            != expected
                        ):
                            holds.append("guide_changed")
                capacity_key = channel + ":" + item.get("format", "unavailable")
                if next_selected:
                    holds.append("later_step_requires_prior_receipt_and_cooldown")
                if remaining.get(capacity_key, 0) <= 0:
                    holds.append("account_capacity_hold")
                state = "held"
                if not holds:
                    state = "due_review"
                    remaining[capacity_key] -= 1
                    next_selected = True
                output.append(
                    dict(
                        person_id=person,
                        channel=channel,
                        rank=rank,
                        planned_day_offset=(rank - 1) * load_policy()["minimum_gap_hours"] / 24,
                        state=state,
                        holds=sorted(set(holds)),
                        draft_revision=draft["revision"] if draft else None,
                        research_sha256=digest(research),
                        score=item["score"],
                        reason=item["reason"],
                        next_cadence_at=snapshot["channels"][channel]["next_cadence_at"],
                    )
                )
        result = {
            "batch_id": batch,
            "prepared_at": now,
            "remaining_capacities": capacities,
            "steps": output,
            "schedule_time": None,
            "send_authorized": False,
            "cross_machine_reservations": "not_implemented",
        }

        plan_id = digest(result)
        with self.connection() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO plans VALUES (?,?,?)", (plan_id, batch, encoded(result))
            )
        return dict(result, plan_id=plan_id)

    def report(self, batch):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch,)).fetchone()
            if not row:
                raise ValueError("unknown batch")
            people = [
                dict(r)
                for r in conn.execute(
                    "SELECT person_id,hold FROM selections WHERE batch_id=? ORDER BY rowid",
                    (batch,),
                )
            ]
            pending = conn.execute(
                "SELECT count(*) FROM history_journal WHERE state='pending'"
            ).fetchone()[0]
        return dict(
            batch_id=batch,
            owner=row["owner"],
            manifest=json.loads(row["manifest"]),
            people=people,
            pending_history=pending,
        )


class LocalConnection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *args):
        try:
            return self.connection.__exit__(*args)
        finally:
            self.connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("freeze", "report", "review", "draft", "plan", "reconcile")
    )
    parser.add_argument("--input", type=Path)
    parser.add_argument("--batch")
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/daily-outbound.sqlite3")
    args = parser.parse_args()
    workflow = DailyOutbound(CRMHistory(), args.ledger)
    data = json.loads(args.input.read_text()) if args.input else {}
    if args.action == "freeze":
        result = workflow.freeze(data)
    elif args.action == "report":
        result = workflow.report(args.batch)
    elif args.action == "review":
        result = workflow.review(args.batch, data["person_id"], data["research"])
    elif args.action == "draft":
        result = workflow.save_draft(args.batch, data["person_id"], data["channel"], data["draft"])
    elif args.action == "plan":
        result = workflow.plan(args.batch, now=data["now"], capacities=data["remaining_capacities"])
    else:
        result = workflow.reconcile()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
