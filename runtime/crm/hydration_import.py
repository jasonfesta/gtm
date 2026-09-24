"""Validate and import an explicitly reviewed saved-listing person manifest locally."""

import argparse
import hashlib
import json
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from crm.database import CANONICAL, connect


def uid(prefix, *parts):
    return prefix + "_" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:24]


def norm(value):
    return unicodedata.normalize("NFKC", value).casefold().strip()


def evidence(row):
    path = Path(row["evidence_file"])
    receipt = json.loads(path.read_text())
    if row.get("evidence_kind") == "indexed_primary":
        text = receipt["result"]
        if row["source_url"] not in text:
            raise ValueError("indexed source URL absent")
        return receipt, text
    if receipt.get("status") != 200 or receipt["url"] != row["source_url"]:
        raise ValueError("successful matching receipt required")
    archive = Path(receipt["archive"])
    if hashlib.sha256(archive.read_bytes()).hexdigest() != receipt["sha256"]:
        raise ValueError("archive hash mismatch")
    return receipt, receipt["text"]


def import_people(database, run, apply=False):
    rows = json.loads((run / "reviewed-people.json").read_text())
    manifest = json.loads((run / "people-manifest.json").read_text())
    digest = hashlib.sha256((run / "reviewed-people.json").read_bytes()).hexdigest()
    if digest != manifest["input_sha256"]:
        raise ValueError("review changed after cohort freeze")
    previous = run / "people-import-result.json"
    if previous.exists():
        result = json.loads(previous.read_text())
        if result.get("input_sha256") != digest:
            raise ValueError("completed run cannot be replaced; create a new run")
        return {"status": "already_applied", "input_sha256": digest}
    if sorted(x["person_id"] for x in rows) != sorted(manifest["person_ids"]):
        raise ValueError("frozen person manifest mismatch")
    if len(set(manifest["person_ids"])) != manifest["denominator"]:
        raise ValueError("duplicate person IDs")
    c = connect(database)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    names = set()
    for row in rows:
        if norm(row["name"]) in names:
            raise ValueError("duplicate reviewed name requires manual resolution")
        names.add(norm(row["name"]))
        for old in c.execute("SELECT person_id,full_name FROM people"):
            if norm(old["full_name"]) == norm(row["name"]) and old["person_id"] != row["person_id"]:
                raise ValueError("existing identity needs explicit merge: " + row["name"])
        if not c.execute("SELECT 1 FROM listings WHERE agent_id=?", (row["agent_id"],)).fetchone():
            raise ValueError("person outside canonical saved listings")
        receipt, text = evidence(row)
        if row["name_token"] not in text:
            raise ValueError("named evidence absent")
        for contact in row["contacts"]:
            if contact["kind"] not in ("email", "linkedin", "x"):
                raise ValueError("unexpected contact channel")
            proof, body = evidence(contact)
            exact = ("mailto:" if contact["kind"] == "email" else "") + contact["value"]
            if contact["status"] == "confirmed":
                if contact.get("evidence_kind") == "indexed_primary":
                    raise ValueError("search-only contact cannot be promoted")
                if exact not in proof["links"] and not (
                    contact.get("literal") and contact["literal"] in body
                ):
                    raise ValueError("exact reviewed contact absent")
            elif contact["status"] != "unverified":
                raise ValueError("unsupported import contact status")
    if not apply:
        c.close()
        return {"validated_people": len(rows), "contacts": sum(len(r["contacts"]) for r in rows)}
    now = datetime.now(timezone.utc).isoformat()
    backup = run / (
        "pre-people-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3"
    )
    with sqlite3.connect(backup) as dest:
        c.backup(dest)

    def source(row):
        old = c.execute(
            "SELECT source_id FROM sources WHERE url=?", (row["source_url"],)
        ).fetchone()
        if old:
            return old[0]
        receipt, _ = evidence(row)
        sid = uid("src", row["source_url"])
        c.execute(
            "INSERT INTO sources(source_id,url,title,publisher,source_type,quality_tier,accessed_at,content_sha256,archived_path,notes) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                sid,
                row["source_url"],
                "Hydration reviewed public evidence",
                urlparse(row["source_url"]).netloc,
                "other" if row.get("evidence_kind") else "official_site",
                3 if row.get("evidence_kind") else 1,
                now,
                receipt.get("sha256"),
                receipt.get("archive"),
                json.dumps({"run_id": run.name, "receipt": row["evidence_file"]}),
            ),
        )
        return sid

    def claim(kind, entity, field, value, sid, accepted=True):
        c.execute(
            "INSERT OR IGNORE INTO evidence_claims(claim_id,entity_type,entity_id,field_name,claim_value,source_id,verification_status,confidence,notes) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                uid("clm", run.name, entity, field, value),
                kind,
                entity,
                field,
                value,
                sid,
                "accepted" if accepted else "unreviewed",
                80 if accepted else 45,
                "Reviewed hydration; public ownership does not establish deliverability or outbound authority.",
            ),
        )

    before = {
        t: c.execute("SELECT count(*) FROM " + t).fetchone()[0]
        for t in ("people", "companies", "contact_points")
    }
    with c:
        for row in rows:
            pid = row["person_id"]
            agent = c.execute(
                "SELECT * FROM agents WHERE agent_id=?", (row["agent_id"],)
            ).fetchone()
            cid = agent["company_id"]
            if not cid:
                domain = (urlparse(row["website"]).hostname or "").removeprefix("www.")
                old = c.execute(
                    "SELECT company_id FROM companies WHERE lower(domain)=?", (domain.lower(),)
                ).fetchone()
                cid = old[0] if old else uid("co", domain)
                c.execute(
                    "INSERT OR IGNORE INTO companies(company_id,canonical_name,normalized_name,domain,website_url,company_status,research_status,confidence,notes) VALUES(?,?,?,?,?,'unknown','review_needed',75,?)",
                    (
                        cid,
                        row["product"],
                        norm(row["product"]),
                        domain,
                        row["website"],
                        "Product operator grouping; no legal entity name inferred. " + run.name,
                    ),
                )
                c.execute(
                    "UPDATE agents SET company_id=?,research_status='review_needed',updated_at=? WHERE agent_id=? AND company_id IS NULL",
                    (cid, now, row["agent_id"]),
                )
            sid = source(row)
            historical = bool(row.get("historical"))
            founder = (
                row["relationship"]
                if row["relationship"] in ("founder", "cofounder", "founding_team")
                else "unknown"
            )
            c.execute(
                "INSERT OR IGNORE INTO people(person_id,primary_company_id,full_name,normalized_name,current_role,founder_status,identity_status,research_status,confidence,notes) VALUES(?,?,?,?,?,?,'single_source','review_needed',75,?)",
                (
                    pid,
                    cid,
                    row["name"],
                    norm(row["name"]),
                    row["role"] + (" — historical; current role unverified" if historical else ""),
                    founder,
                    json.dumps(
                        {
                            "run_id": run.name,
                            "review": row["notes"],
                            "technical_contribution": row["technical"],
                            "indexed_primary": bool(row.get("evidence_kind")),
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            c.execute(
                "INSERT OR IGNORE INTO company_people(company_id,person_id,role,relationship_type,is_current,confidence) VALUES(?,?,?,?,?,75)",
                (
                    cid,
                    pid,
                    row["role"],
                    "former" if historical else row["relationship"],
                    int(not historical),
                ),
            )
            claim("person", pid, "company_role", row["product"] + " | " + row["role"], sid)
            claim("agent", row["agent_id"], "company_id", cid, sid)
            for contact in row["contacts"]:
                csid = source(contact)
                parsed = urlparse(contact["value"])
                key = (
                    norm(contact["value"])
                    if contact["kind"] == "email"
                    else ("linkedin.com" if contact["kind"] == "linkedin" else "x.com")
                    + parsed.path.rstrip("/").casefold()
                )
                cp = uid("cp", pid, contact["kind"], key)
                supported = contact["status"] == "confirmed"
                c.execute(
                    "INSERT OR IGNORE INTO contact_points(contact_id,person_id,contact_type,value,normalized_value,verification_status,verification_method,confidence,is_public,source_id,first_seen_at,last_verified_at,notes) VALUES(?,?,?,?,?,?,?,?,1,?,?,?,?)",
                    (
                        cp,
                        pid,
                        contact["kind"],
                        contact["value"],
                        key,
                        contact["status"],
                        "reviewed_public_association" if supported else "indexed_candidate",
                        85 if supported else 45,
                        csid,
                        now,
                        now if supported else None,
                        "Public association only; delivery and account availability not tested. "
                        + run.name,
                    ),
                )
                claim("contact_point", cp, "ownership", contact["value"], csid, supported)
        if c.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("foreign key check failed")
    after = {t: c.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in before}
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.close()
    return {
        "input_sha256": digest,
        "before": before,
        "after": after,
        "added": {t: after[t] - before[t] for t in before},
        "backup": str(backup),
        "completed_at": now,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--database", type=Path, default=CANONICAL)
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    result = import_people(args.database, args.run, args.apply)
    if args.apply and result.get("status") != "already_applied":
        (args.run / "people-import-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
