"""Private draft ledger with configured CRM reads. Never sends or schedules."""

import argparse
import datetime as dt
import json
import re
import uuid
from pathlib import Path

from crm.cli import DEFAULT_DB, ROOT, utc_now
from crm.database import configured, private_connection
from crm.outreach_rules import person_assessment
from crm.route_review import latest_review


def validate_body_links(body):
    """Allow one verified plain-text call destination; other links remain held."""
    urls = re.findall(r"https?://[^\s<>]+", body, re.I)
    if not urls and not re.search(r"www\.|utm_", body, re.I):
        return
    if (
        len(urls) != 1
        or urls[0].rstrip(".,!?") != "https://darwin.so/call"
        or re.search(r"www\.|utm_|[<>]|\]\(", body, re.I)
    ):
        raise ValueError("only one plain-text approved call link is supported")


def address_key(value):
    return value.strip().casefold()


def fresh(value, hours=24):
    try:
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        return 0 <= age.total_seconds() <= hours * 3600
    except (ValueError, TypeError):
        return False


class EmailWorkflow:
    def __init__(self, db=DEFAULT_DB):
        self.db = Path(db)
        if not self.db.is_file() and not configured(self.db):
            raise ValueError("existing initialized CRM database required")

    def connection(self):
        return private_connection(self.db)

    def initialize(self):
        with self.connection() as c:
            c.executescript((ROOT / "sql/email_schema.sql").read_text())

    def blockers(self, c, contact_id):
        r = c.execute(
            "SELECT cp.*,p.primary_company_id,p.identity_status FROM contact_points cp "
            "JOIN people p USING(person_id) WHERE contact_id=?",
            (contact_id,),
        ).fetchone()
        if not r:
            return ["unknown_contact"]
        reasons = person_assessment(c, r["person_id"], "email")["blockers"]
        route = latest_review(c, r["person_id"])
        if route and route["readiness"] != "ready_for_preparation":
            reasons.append("route_review_" + route["readiness"])
        if r["contact_type"] != "email" or not re.fullmatch(
            r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", r["value"]
        ):
            reasons.append("invalid_email")
        if r["verification_status"] != "confirmed" or not r["is_public"] or not r["source_id"]:
            reasons.append("public_ownership_unconfirmed")
        if r["identity_status"] in ("unverified", "conflicting"):
            reasons.append("identity_unresolved")
        if c.execute(
            "SELECT 1 FROM conflicts WHERE status='open' AND entity_id IN (?,?,?)",
            (r["person_id"], r["primary_company_id"], contact_id),
        ).fetchone():
            reasons.append("open_conflict")
        if c.execute(
            "SELECT 1 FROM suppressions WHERE is_active=1 AND (channel IS NULL OR channel='email') "
            "AND (person_id=? OR company_id=? OR contact_id IN "
            "(SELECT contact_id FROM contact_points WHERE contact_type='email' AND lower(trim(value))=?))",
            (r["person_id"], r["primary_company_id"], address_key(r["value"])),
        ).fetchone():
            reasons.append("suppressed")
        if c.execute(
            "SELECT 1 FROM outreach_events WHERE (person_id=? OR company_id=?) AND outcome<>'drafted'",
            (r["person_id"], r["primary_company_id"]),
        ).fetchone():
            reasons.append("communication_history_needs_review")
        return reasons

    def audit(self):
        with self.connection() as c:
            c.execute("BEGIN")
            counts = {
                t: c.execute("SELECT count(*) FROM " + t).fetchone()[0]
                for t in (
                    "people",
                    "companies",
                    "agents",
                    "contact_points",
                    "outreach_events",
                    "suppressions",
                    "conflicts",
                )
            }
            emails = [
                dict(r)
                for r in c.execute(
                    "SELECT cp.contact_id,cp.person_id,cp.value,cp.verification_status,p.full_name "
                    "FROM contact_points cp JOIN people p USING(person_id) WHERE cp.contact_type='email'"
                )
            ]
            for r in emails:
                r["blockers"] = self.blockers(c, r["contact_id"])
                r["deliverability"] = "not_checked"
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='email_reviews'").fetchone():
                    review = c.execute(
                        "SELECT decision,reason,reviewed_at,history_result,history_checked_at FROM email_reviews "
                        "WHERE contact_id=? ORDER BY rowid DESC LIMIT 1",
                        (r["contact_id"],),
                    ).fetchone()
                    r["latest_review"] = dict(review) if review else None
                    if not review:
                        r["blockers"].append("individual_review_required")
                    elif review["decision"] == "hold":
                        r["blockers"].append("review_hold")
                    elif not fresh(review["reviewed_at"]) or not fresh(
                        review["history_checked_at"]
                    ):
                        r["blockers"].append("review_expired")
                    elif review["history_result"] != "no_match":
                        r["blockers"].append("gmail_history_needs_review")
            states = []
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='email_preparations'").fetchone():
                states = [
                    dict(r)
                    for r in c.execute(
                        "SELECT state,variant,utm_enabled,count(*) AS count "
                        "FROM email_preparations GROUP BY state,variant,utm_enabled"
                    )
                ]
            return dict(
                generated_at=utc_now(),
                counts=counts,
                email_classes=[
                    dict(r)
                    for r in c.execute(
                        "SELECT verification_status,count(*) AS count FROM contact_points "
                        "WHERE contact_type='email' GROUP BY verification_status"
                    )
                ],
                integrity=[r[0] for r in c.execute("PRAGMA integrity_check")],
                foreign_key_errors=[tuple(r) for r in c.execute("PRAGMA foreign_key_check")],
                people_without_email=c.execute(
                    "SELECT count(*) FROM people p WHERE NOT EXISTS "
                    "(SELECT 1 FROM contact_points cp WHERE cp.person_id=p.person_id AND contact_type='email')"
                ).fetchone()[0],
                duplicate_email_groups=[
                    dict(r)
                    for r in c.execute(
                        "SELECT lower(trim(value)) AS address_key,count(*) AS count "
                        "FROM contact_points WHERE contact_type='email' GROUP BY lower(trim(value)) HAVING count(*)>1"
                    )
                ],
                email_contacts=emails,
                preparation_counts=states,
                scope="Full local structural scan; factual review and external history remain per-contact checks. No send authorization.",
            )

    def prepare(
        self,
        *,
        campaign,
        account,
        contact_id,
        review_id,
        draft_id,
        revision,
        selection_reason,
        utm_reason,
        batch_day=None,
        target=100,
    ):
        if not 1 <= target <= 200 or not selection_reason.strip() or not utm_reason.strip():
            raise ValueError("target must be 1–200 and decision reasons are required")
        batch_day = (
            batch_day
            or dt.datetime.now(dt.timezone.utc)
            .astimezone(__import__("zoneinfo").ZoneInfo("America/New_York"))
            .date()
            .isoformat()
        )
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            issues = self.blockers(c, contact_id)
            if issues:
                raise ValueError(", ".join(issues))
            cp = c.execute(
                "SELECT cp.*,p.primary_company_id FROM contact_points cp JOIN people p USING(person_id) "
                "WHERE contact_id=?",
                (contact_id,),
            ).fetchone()
            review = c.execute(
                "SELECT * FROM email_reviews WHERE review_id=?", (review_id,)
            ).fetchone()
            latest = c.execute(
                "SELECT review_id FROM email_reviews WHERE contact_id=? ORDER BY rowid DESC LIMIT 1",
                (contact_id,),
            ).fetchone()
            if (
                not review
                or review["contact_id"] != contact_id
                or review["exact_address"] != cp["value"]
                or review["decision"] != "draft_ok"
                or review["history_result"] != "no_match"
                or review["gmail_account"] != account
                or not latest
                or latest["review_id"] != review_id
                or not fresh(review["reviewed_at"])
                or not fresh(review["history_checked_at"])
            ):
                raise ValueError(
                    "fresh exact-address review and same-account history check required"
                )
            copy = c.execute(
                "SELECT * FROM copy_revisions WHERE draft_id=? AND revision=?", (draft_id, revision)
            ).fetchone()
            if (
                not copy
                or copy["person_id"] != cp["person_id"]
                or copy["format"] != "email"
                or copy["channel"] != "email"
            ):
                raise ValueError("recipient email copy revision mismatch")
            validate_body_links(copy["body"])
            prior = c.execute(
                "SELECT * FROM email_preparations WHERE campaign=? AND account=? AND "
                "(person_id=? OR address_key=?)",
                (campaign, account, cp["person_id"], address_key(cp["value"])),
            ).fetchone()
            if prior:
                if (prior["contact_id"], prior["draft_id"], prior["revision"]) == (
                    contact_id,
                    draft_id,
                    revision,
                ):
                    return dict(prior)
                raise ValueError("recipient already reserved; review existing preparation")
            if c.execute(
                "SELECT 1 FROM email_preparations WHERE account=? AND "
                "(person_id=? OR address_key=?)",
                (account, cp["person_id"], address_key(cp["value"])),
            ).fetchone():
                raise ValueError("recipient already reserved in another campaign")
            if c.execute(
                "SELECT 1 FROM email_preparations WHERE campaign=? AND company_id=?",
                (campaign, cp["primary_company_id"]),
            ).fetchone():
                raise ValueError(
                    "company already reserved; one lead per company in initial campaign"
                )
            if (
                c.execute(
                    "SELECT count(*) FROM email_preparations WHERE account=? AND batch_day=?",
                    (account, batch_day),
                ).fetchone()[0]
                >= target
            ):
                raise ValueError("daily preparation target reached")
            key = "email_" + uuid.uuid4().hex
            now = utc_now()
            c.execute(
                "INSERT INTO email_preparations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    campaign,
                    account,
                    batch_day,
                    cp["person_id"],
                    cp["primary_company_id"],
                    contact_id,
                    cp["value"],
                    address_key(cp["value"]),
                    review_id,
                    draft_id,
                    revision,
                    copy["variant"],
                    "editorial",
                    selection_reason,
                    0,
                    utm_reason,
                    "prepared",
                    None,
                    None,
                    None,
                    now,
                    now,
                ),
            )
            return dict(
                c.execute(
                    "SELECT * FROM email_preparations WHERE preparation_id=?", (key,)
                ).fetchone()
            )

    def reserve_gmail_create(self, preparation_id):
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute(
                "SELECT * FROM email_preparations WHERE preparation_id=?", (preparation_id,)
            ).fetchone()
            if not r or r["state"] != "prepared":
                raise ValueError("creation already attempted; reconcile Gmail before retrying")
            issues = self.blockers(c, r["contact_id"])
            if issues:
                raise ValueError(", ".join(issues))
            review = c.execute(
                "SELECT * FROM email_reviews WHERE review_id=?", (r["review_id"],)
            ).fetchone()
            latest = c.execute(
                "SELECT review_id FROM email_reviews WHERE contact_id=? ORDER BY rowid DESC LIMIT 1",
                (r["contact_id"],),
            ).fetchone()
            current_address = c.execute(
                "SELECT value FROM contact_points WHERE contact_id=?", (r["contact_id"],)
            ).fetchone()[0]
            if (
                latest["review_id"] != r["review_id"]
                or current_address != r["exact_address"]
                or not fresh(review["reviewed_at"])
                or not fresh(review["history_checked_at"])
            ):
                raise ValueError("address changed or review expired; review again")
            c.execute(
                "UPDATE email_preparations SET state='create_pending',updated_at=? WHERE preparation_id=?",
                (utc_now(), preparation_id),
            )

    def record_gmail_draft(self, preparation_id, draft_id, message_id, thread_id):
        if not all((draft_id, message_id, thread_id)):
            raise ValueError("complete Gmail receipt required")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute(
                "SELECT * FROM email_preparations WHERE preparation_id=?", (preparation_id,)
            ).fetchone()
            if (
                r
                and r["state"] == "gmail_draft"
                and (r["gmail_draft_id"], r["gmail_message_id"], r["gmail_thread_id"])
                == (draft_id, message_id, thread_id)
            ):
                return
            if not r or r["state"] != "create_pending":
                raise ValueError("no pending Gmail creation")
            c.execute(
                "UPDATE email_preparations SET state='gmail_draft',gmail_draft_id=?,gmail_message_id=?,"
                "gmail_thread_id=?,updated_at=? WHERE preparation_id=?",
                (draft_id, message_id, thread_id, utc_now(), preparation_id),
            )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--output", type=Path)
    p.add_argument("--init", action="store_true")
    args = p.parse_args()
    workflow = EmailWorkflow(args.db)
    if args.init:
        workflow.initialize()
    result = json.dumps(workflow.audit(), indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result + "\n")
    else:
        print(result)


if __name__ == "__main__":
    main()
