"""Import a reviewed local developer pass; no network calls or outreach.

This uses the existing CRM schema. Source membership remains in the run files.
Default is validation only; --apply creates a consistent backup and imports.
"""

import argparse
import hashlib
import json
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

from crm.database import connect


def uid(prefix, *values):
    return prefix + "_" + hashlib.sha256("\0".join(values).encode()).hexdigest()[:24]


def normalized(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def contact_key(kind, value):
    if kind == "email":
        return normalized(value)
    parsed = urlparse(value)
    return ("linkedin.com" if kind == "linkedin" else "x.com") + unquote(parsed.path).rstrip(
        "/"
    ).casefold()


def import_rows(database, run, apply=False):
    rows = json.loads((run / "reviewed-developers.json").read_text())
    frozen = json.loads((run / "new-developer-cohort.json").read_text())
    assert len(rows) == frozen["denominator"]
    assert {r["person_id"] for r in rows} == {r["person_id"] for r in frozen["rows"]}
    observed = frozen["frozen_at"]
    db = connect(database)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    companies = {
        r["canonical_name"]: r["company_id"] for r in db.execute("SELECT * FROM companies")
    }
    for row in rows:
        assert row["company"] in companies
        assert row["identity_status"] in ("single_source", "unverified")
        for found in db.execute(
            "SELECT person_id FROM people WHERE normalized_name=?", (normalized(row["name"]),)
        ):
            if found["person_id"] != row["person_id"]:
                raise ValueError(
                    "Existing person requires explicit duplicate resolution: " + row["name"]
                )
        for contact in row["contacts"]:
            assert contact["kind"] in ("email", "linkedin", "x")
            assert contact["status"] in ("confirmed", "unverified")
            assert contact["source_url"].startswith("https://")
            if contact["status"] == "confirmed":
                evidence = Path(contact["evidence_file"])
                if not evidence.is_absolute():
                    evidence = run.parents[4] / evidence
                receipt = json.loads(evidence.read_text())
                assert receipt.get("status") == 200
                links = [x["href"] if isinstance(x, dict) else x for x in receipt["links"]]
                expected = ("mailto:" if contact["kind"] == "email" else "") + contact["value"]
                assert expected in links
    if not apply:
        db.close()
        return {
            "validated_people": len(rows),
            "validated_contacts": sum(len(r["contacts"]) for r in rows),
        }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = run / ("pre-import-" + stamp + ".sqlite3")
    with sqlite3.connect(backup) as target:
        db.backup(target)
    before = db.total_changes

    def source(url, title, direct=False, evidence=None, content_hash=None):
        previous = db.execute("SELECT source_id FROM sources WHERE url=?", (url,)).fetchone()
        if previous:
            return previous[0]
        sid = uid("src", url)
        db.execute(
            "INSERT INTO sources(source_id,url,title,publisher,source_type,quality_tier,accessed_at,content_sha256,notes) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                sid,
                url,
                title,
                urlparse(url).netloc,
                "official_site" if direct else "other",
                1 if direct else 4,
                observed,
                content_hash,
                json.dumps(
                    {
                        "run_id": run.name,
                        "evidence_file": evidence,
                        "direct_page_checked": direct,
                        "limitation": "Public association is distinct from current employment, account availability, and email delivery.",
                    }
                ),
            ),
        )
        return sid

    def claim(entity_type, entity_id, field, value, sid, accepted, note):
        db.execute(
            "INSERT OR IGNORE INTO evidence_claims(claim_id,entity_type,entity_id,field_name,claim_value,source_id,verification_status,confidence,notes) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                uid("claim", entity_id, field, value, sid),
                entity_type,
                entity_id,
                field,
                value,
                sid,
                "accepted" if accepted else "unreviewed",
                80 if accepted else 45,
                note,
            ),
        )

    with db:
        for row in rows:
            pid, cid = row["person_id"], companies[row["company"]]
            direct = row["role_evidence"].startswith("direct")
            notes = json.dumps(
                {
                    "run_id": run.name,
                    "audience": row["category"],
                    "role_evidence": row["role_evidence"],
                    "review": row["review_notes"],
                    "role_current_confirmed": row["is_current"] and direct,
                },
                ensure_ascii=False,
            )
            role = row["role"] + (
                " (company listing; current role under review)" if not row["is_current"] else ""
            )
            founder = "founding_team" if row["relationship"] == "founding_team" else "unknown"
            db.execute(
                "INSERT OR IGNORE INTO people(person_id,primary_company_id,full_name,normalized_name,current_role,founder_status,identity_status,research_status,confidence,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    cid,
                    row["name"],
                    normalized(row["name"]),
                    role,
                    founder,
                    row["identity_status"],
                    "review_needed",
                    80 if direct else 45,
                    notes,
                ),
            )
            db.execute(
                "INSERT OR IGNORE INTO company_people(company_id,person_id,role,relationship_type,is_current,confidence) VALUES(?,?,?,?,?,?)",
                (
                    cid,
                    pid,
                    row["role"],
                    row["relationship"],
                    int(row["is_current"] and direct),
                    80 if direct else 45,
                ),
            )
            sid = source(
                row["source_url"],
                row["name"] + " — role evidence",
                direct,
                row.get("evidence_file"),
                row.get("source_hash"),
            )
            claim(
                "person",
                pid,
                "company_role",
                row["company"] + " | " + row["role"],
                sid,
                direct,
                notes,
            )
            for contact in row["contacts"]:
                confirmed = contact["status"] == "confirmed"
                csid = source(
                    contact["source_url"],
                    row["name"] + " — professional contact evidence",
                    confirmed,
                    contact.get("evidence_file"),
                )
                key = contact_key(contact["kind"], contact["value"])
                contact_id = uid("contact", pid, contact["kind"], key)
                db.execute(
                    "INSERT OR IGNORE INTO contact_points(contact_id,person_id,contact_type,value,normalized_value,verification_status,verification_method,confidence,is_primary,is_public,source_id,first_seen_at,last_verified_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        contact_id,
                        pid,
                        contact["kind"],
                        contact["value"],
                        key,
                        contact["status"],
                        contact["method"],
                        90 if confirmed else 45,
                        int(confirmed),
                        1,
                        csid,
                        observed,
                        observed if confirmed else None,
                        contact["note"] + " Run: " + run.name,
                    ),
                )
                claim(
                    "contact_point",
                    contact_id,
                    "public_association",
                    contact["value"],
                    csid,
                    confirmed,
                    contact["note"],
                )
            db.execute(
                "INSERT OR IGNORE INTO research_tasks(task_id,entity_type,entity_id,task_type,status,priority,notes) VALUES(?,?,?,?,?,?,?)",
                (
                    uid("task", run.name, pid, "review"),
                    "person",
                    pid,
                    "quality_review",
                    "needs_review",
                    85 if row["review_notes"] else 50,
                    notes,
                ),
            )
            for kind, outcome in row["channel_outcomes"].items():
                if outcome != "supported":
                    db.execute(
                        "INSERT OR IGNORE INTO research_tasks(task_id,entity_type,entity_id,task_type,status,priority,notes) VALUES(?,?,?,?,?,?,?)",
                        (
                            uid("task", run.name, pid, kind),
                            "person",
                            pid,
                            "find_email" if kind == "email" else "find_profiles",
                            "needs_review" if outcome == "candidate" else "not_found",
                            50,
                            json.dumps(
                                {
                                    "channel": kind,
                                    "bounded_pass_outcome": outcome,
                                    "run_id": run.name,
                                    "next_action": "Review candidate ownership or research another permitted primary source; no paid approval.",
                                }
                            ),
                        ),
                    )
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not db.execute("PRAGMA foreign_key_check").fetchall()
    changes = db.total_changes - before
    db.close()
    return {
        "people_in_reviewed_input": len(rows),
        "database_changes": changes,
        "backup": str(backup),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--database", type=Path, default=Path("data/crm.sqlite3"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(import_rows(args.database.resolve(), args.run.resolve(), args.apply), indent=2)
    )


if __name__ == "__main__":
    main()
