"""Import a reviewed cohort into the configured CRM. No outreach or provider calls."""

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from crm.cli import DEFAULT_DB, ROOT, stable_id
from crm.database import connect


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def latest_review(c, person_id):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='latest_route_reviews'").fetchone():
        return None
    row = c.execute(
        "SELECT decision_json FROM latest_route_reviews WHERE person_id=?", (person_id,)
    ).fetchone()
    return json.loads(row[0]) if row else None


def validate(c, data):
    manifest, reviews = data["manifest"], data["reviews"]
    ids = manifest["person_ids"]
    if len(ids) != len(set(ids)) or len(ids) != manifest["denominator"]:
        raise ValueError("invalid frozen denominator")
    if sorted(r["person_id"] for r in reviews) != sorted(ids):
        raise ValueError("review must cover frozen cohort exactly once")
    for r in reviews:
        pid = r["person_id"]
        if not c.execute("SELECT 1 FROM people WHERE person_id=?", (pid,)).fetchone():
            raise ValueError("unknown person")
        if not all(
            r.get(k) for k in ("qualification_reason", "route_reason", "next_action", "source_refs")
        ):
            raise ValueError("reason, next action and sources required")
        score = r["outreach_score"]
        if score is not None and (type(score) is not int or score not in (1, 2, 3)):
            raise ValueError("score must be 1, 2, 3 or unassigned")
        if (score is None) != (r["score_status"] == "unassigned"):
            raise ValueError("score/status mismatch")
        # Technical contribution is descriptive, not the audience gate. Relevant
        # founders and product/team members may qualify without personally coding.
        fit = r.get(
            "audience_fit", "eligible" if r["qualification"] == "qualified" else "needs_review"
        )
        if fit not in ("eligible", "needs_review", "out_of_scope"):
            raise ValueError("invalid audience fit")
        if "audience_fit" in r and not r.get("audience_reason"):
            raise ValueError("audience decision requires a reason")
        if fit != "eligible" and score is not None:
            raise ValueError("unresolved audience fit must remain unassigned")
        if r["score_status"] == "supported":
            if score == 3:
                triggers = r.get("meeting_triggers", [])
                if not any(
                    t.get("source_url")
                    and (
                        t.get("type") == "sf_local"
                        and t.get("subject") == "person"
                        and t.get("city") == "San Francisco"
                        or t.get("type") in ("shared_investor", "known_connector")
                        and t.get("our_evidence")
                        and t.get("their_evidence")
                    )
                    for t in triggers
                ):
                    raise ValueError("supported meeting needs person-level trigger")
            elif score == 2:
                activity = r.get("social_activity", {})
                try:
                    age = dt.date.fromisoformat(data["reviewed_at"][:10]) - dt.date.fromisoformat(
                        activity["date"]
                    )
                    valid = (
                        0 <= age.days <= 30
                        and activity.get("url")
                        and activity.get("authorship_supported")
                    )
                except (KeyError, ValueError):
                    valid = False
                if not valid:
                    raise ValueError("supported social needs dated authored activity")
            elif not r.get("fallback_checks_complete"):
                raise ValueError("unknown meeting/social checks require provisional email")
        if set(r["channels"]) != {"email", "linkedin", "x"}:
            raise ValueError("three channel outcomes required")
        for channel, review in r["channels"].items():
            actual = {
                x["contact_id"]: dict(x)
                for x in c.execute(
                    "SELECT * FROM contact_points WHERE person_id=? AND contact_type=?",
                    (pid, channel),
                )
            }
            supplied = review["contacts"]
            if sorted(x["contact_id"] for x in supplied) != sorted(actual):
                raise ValueError(
                    "channel must cover exact current contacts, without cross-person mappings"
                )
            for contact in supplied:
                if contact["before"] != actual[contact["contact_id"]]:
                    raise ValueError("contact changed since review; review again")
            if review["outcome"] == "association_supported" and not any(
                x["ownership"] == "supported" for x in supplied
            ):
                raise ValueError("unsupported channel promotion")
            for contact in supplied:
                before = contact["before"]
                if (
                    contact["ownership"] == "supported"
                    and before["verification_status"] != "confirmed"
                ):
                    proof = contact.get("new_public_evidence", {})
                    if not all(
                        proof.get(k)
                        for k in ("source_url", "exact_link", "association_reason", "receipt")
                    ):
                        raise ValueError(
                            "new ownership support requires exact public link and receipt"
                        )
                    if channel != "linkedin":
                        raise ValueError("this importer supports reviewed LinkedIn upgrades only")
                    from urllib.parse import urlparse

                    old, new = urlparse(before["value"]), urlparse(proof["exact_link"])
                    if old.hostname != new.hostname or old.path.rstrip("/") != new.path.rstrip("/"):
                        raise ValueError("public link must match reviewed contact")


def apply_review(db, data, dry_run=False):
    digest = hashlib.sha256(encoded(data).encode()).hexdigest()
    run = data["manifest"]["run_id"]
    with connect(db) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='route_review_runs'").fetchone():
            old = c.execute(
                "SELECT input_sha256 FROM route_review_runs WHERE run_id=?", (run,)
            ).fetchone()
            if old:
                if old[0] != digest:
                    raise ValueError(
                        "run already exists with different decisions; create a new review run"
                    )
                return {"status": "already_applied", "run_id": run}
        if dry_run:
            validate(c, data)
            return {"status": "valid", "people": len(data["reviews"])}
        c.executescript((ROOT / "sql/route_review_schema.sql").read_text())
        c.execute("BEGIN IMMEDIATE")
        validate(c, data)
        c.execute(
            "INSERT INTO route_review_runs VALUES(?,?,?,?,?)",
            (run, digest, encoded(data["manifest"]), data["reviewed_at"], data["reviewer"]),
        )

        def audit(table, key, before, after):
            c.execute(
                "INSERT INTO route_review_changes(run_id,table_name,record_id,before_json,after_json) VALUES(?,?,?,?,?)",
                (run, table, key, encoded(before) if before else None, encoded(after)),
            )

        for r in data["reviews"]:
            pid = r["person_id"]
            c.execute(
                "INSERT INTO route_reviews(run_id,person_id,qualification,role_status,outreach_score,score_status,readiness,decision_json) VALUES(?,?,?,?,?,?,?,?)",
                (
                    run,
                    pid,
                    r["qualification"],
                    r["role_status"],
                    r["outreach_score"],
                    r["score_status"],
                    r["readiness"],
                    encoded(r),
                ),
            )
            for channel, review in r["channels"].items():
                c.execute(
                    "INSERT INTO channel_reviews VALUES(?,?,?,?,?)",
                    (run, pid, channel, review["outcome"], encoded(review)),
                )
                for contact in review["contacts"]:
                    proof = contact.get("new_public_evidence")
                    if not proof:
                        continue
                    source = c.execute(
                        "SELECT source_id FROM sources WHERE url=?", (proof["source_url"],)
                    ).fetchone()
                    source_id = source[0] if source else stable_id("src", proof["source_url"])
                    if not source:
                        c.execute(
                            "INSERT INTO sources(source_id,url,title,source_type,quality_tier,accessed_at,notes) VALUES(?,?,?,?,?,?,?)",
                            (
                                source_id,
                                proof["source_url"],
                                "Reviewed personal profile link",
                                "founder_bio",
                                1,
                                data["reviewed_at"],
                                encoded(proof),
                            ),
                        )
                    before = contact["before"]
                    c.execute(
                        "UPDATE contact_points SET verification_status='confirmed',verification_method='self_link',source_id=?,last_verified_at=?,notes=? WHERE contact_id=?",
                        (
                            source_id,
                            data["reviewed_at"],
                            (before.get("notes") or "") + " Reviewed self-link: " + encoded(proof),
                            contact["contact_id"],
                        ),
                    )
                    after = dict(
                        c.execute(
                            "SELECT * FROM contact_points WHERE contact_id=?",
                            (contact["contact_id"],),
                        ).fetchone()
                    )
                    audit("contact_points", contact["contact_id"], before, after)
                    c.execute(
                        "INSERT INTO evidence_claims(claim_id,entity_type,entity_id,field_name,claim_value,source_id,verification_status,confidence,notes) VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            stable_id("clm", run, contact["contact_id"]),
                            "contact_point",
                            contact["contact_id"],
                            "public_association",
                            proof["exact_link"],
                            source_id,
                            "accepted",
                            90,
                            encoded(proof),
                        ),
                    )
            for issue in r.get("issues", []):
                if issue.get("existing_conflict_id"):
                    existing = c.execute(
                        "SELECT 1 FROM conflicts WHERE conflict_id=? AND entity_id=?",
                        (issue["existing_conflict_id"], pid),
                    ).fetchone()
                    if not existing:
                        raise ValueError("retained issue must belong to the reviewed person")
                    continue
                cid = stable_id("conflict", run, pid, issue["field"])
                c.execute(
                    "INSERT INTO conflicts(conflict_id,entity_type,entity_id,field_name,value_a,value_b,status,resolution,resolved_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        "person",
                        pid,
                        issue["field"],
                        issue["value_a"],
                        issue["value_b"],
                        issue["status"],
                        encoded(issue),
                        data["reviewed_at"] if issue["status"] == "resolved" else None,
                    ),
                )
            for task in c.execute(
                "SELECT * FROM research_tasks WHERE entity_type='person' AND entity_id=? AND task_type='quality_review' AND status<>'complete'",
                (pid,),
            ).fetchall():
                before = dict(task)
                c.execute(
                    "UPDATE research_tasks SET status='complete',attempts=attempts+1,last_attempt_at=?,updated_at=?,blocker=NULL,notes=? WHERE task_id=?",
                    (
                        data["reviewed_at"],
                        data["reviewed_at"],
                        (task["notes"] or "")
                        + " Review pass complete: "
                        + run
                        + ". Follow-up: "
                        + r["next_action"],
                        task["task_id"],
                    ),
                )
                audit(
                    "research_tasks",
                    task["task_id"],
                    before,
                    dict(
                        c.execute(
                            "SELECT * FROM research_tasks WHERE task_id=?", (task["task_id"],)
                        ).fetchone()
                    ),
                )
            # Replace this review system's old queue item, retaining its audit.
            for old_run in c.execute(
                "SELECT run_id FROM route_reviews WHERE person_id=? AND run_id<>?", (pid, run)
            ).fetchall():
                old_id = stable_id("tsk", old_run[0], pid, "followup")
                old_task = c.execute(
                    "SELECT * FROM research_tasks WHERE task_id=?", (old_id,)
                ).fetchone()
                if old_task and old_task["status"] != "complete":
                    c.execute(
                        "UPDATE research_tasks SET status='complete',updated_at=?,notes=? WHERE task_id=?",
                        (
                            data["reviewed_at"],
                            old_task["notes"]
                            + " Superseded by review "
                            + run
                            + "; factual gaps remain in its successor.",
                            old_id,
                        ),
                    )
                    audit(
                        "research_tasks",
                        old_id,
                        dict(old_task),
                        dict(
                            c.execute(
                                "SELECT * FROM research_tasks WHERE task_id=?", (old_id,)
                            ).fetchone()
                        ),
                    )
            if r["readiness"] != "ready_for_preparation":
                tid = stable_id("tsk", run, pid, "followup")
                c.execute(
                    "INSERT INTO research_tasks(task_id,entity_type,entity_id,task_type,status,priority,blocker,notes) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        tid,
                        "person",
                        pid,
                        "resolve_conflict"
                        if r.get("issues")
                        else "verify_identity"
                        if r["qualification"] != "qualified"
                        else "find_profiles",
                        "blocked",
                        70 if r["outreach_score"] == 3 else 50,
                        r["readiness"],
                        r["next_action"] + " Review: " + run,
                    ),
                )
        for decision in data.get("founder_task_decisions", []):
            before = dict(
                c.execute(
                    "SELECT * FROM research_tasks WHERE task_id=?", (decision["task_id"],)
                ).fetchone()
            )
            c.execute(
                "UPDATE research_tasks SET status=?,attempts=attempts+1,last_attempt_at=?,updated_at=?,blocker=?,notes=? WHERE task_id=?",
                (
                    decision["status"],
                    data["reviewed_at"],
                    data["reviewed_at"],
                    decision.get("blocker"),
                    (before["notes"] or "") + " " + decision["reason"],
                    decision["task_id"],
                ),
            )
            audit(
                "research_tasks",
                decision["task_id"],
                before,
                dict(
                    c.execute(
                        "SELECT * FROM research_tasks WHERE task_id=?", (decision["task_id"],)
                    ).fetchone()
                ),
            )
        return {
            "status": "applied",
            "run_id": run,
            "people": len(data["reviews"]),
            "channels": 3 * len(data["reviews"]),
        }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("review", type=Path)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    print(encoded(apply_review(a.db, json.loads(a.review.read_text()), dry_run=not a.apply)))


if __name__ == "__main__":
    main()
