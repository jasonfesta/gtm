"""Import bounded provider evidence into shared CRM for PostHog live queries.

Only the reviewed local evidence blob is imported. No messages are sent. Raw text
is included by explicit operator request for this runtime/dashboard integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from crm.database import ROOT


def normalize_handle(value):
    value = value.strip().lower()
    value = re.sub(r"^https?://(?:www\.)?(?:x|twitter)\.com/", "", value)
    return value.split("?")[0].strip("/").lstrip("@")


def prepare_records(blob, contacts):
    """Preserve missing bodies, approximate times and unconfirmed IDs explicitly."""
    records = []
    seen = set()
    for row in blob["records"]:
        if row["record_id"] in seen:
            raise ValueError("duplicate evidence record ID")
        seen.add(row["record_id"])
        if row["account"] != blob["account"] or row["channel"] != "x":
            raise ValueError("evidence account/channel mismatch")
        if row["provider_id_status"] == "confirmed" and not row.get("provider_id"):
            raise ValueError("confirmed evidence requires a provider ID")
        who = row.get("contact_handle") or row.get("actor_handle") or row.get("parent_author")
        matches = contacts.get(normalize_handle(who or ""), set())
        person = next(iter(matches)) if len(matches) == 1 else None
        body = row.get("body")
        records.append(
            (
                row["record_id"],
                row["area"],
                row["channel"],
                row["account"],
                row["direction"],
                row.get("provider_id"),
                row["provider_id_status"],
                row["occurred_at"],
                row.get("timestamp_source") or "provider evidence",
                body,
                row.get("body_status") or ("captured" if body is not None else "not_captured"),
                who,
                person,
                row.get("conversation_id"),
                row.get("url") or row.get("conversation_url"),
                row.get("in_reply_to_url"),
            )
        )
    return records


def import_evidence(blob_path, credential_path):
    import psycopg

    raw = Path(blob_path).read_bytes()
    blob = json.loads(raw)
    digest = hashlib.sha256(raw).hexdigest()
    import_id = "x-audit-" + digest
    with psycopg.connect(Path(credential_path).read_text().strip(), connect_timeout=15) as conn:
        conn.execute("SET LOCAL search_path TO crm_gtm,public")
        conn.execute("SET LOCAL statement_timeout TO '30s'")
        contacts = {}
        rows = conn.execute(
            "SELECT person_id,value FROM contact_points WHERE contact_type='x'"
        ).fetchall()
        rows += conn.execute(
            "SELECT rp.person_id,rc.address FROM relationship_contacts rc JOIN relationship_profiles rp ON rp.profile_id=rc.profile_id WHERE rc.channel='x' AND rp.person_id IS NOT NULL"
        ).fetchall()
        for person, address in rows:
            contacts.setdefault(normalize_handle(address), set()).add(person)
        records = prepare_records(blob, contacts)
        prior = conn.execute(
            "SELECT source_sha256 FROM dashboard_evidence_imports WHERE import_id=%s", (import_id,)
        ).fetchone()
        if prior:
            count = conn.execute(
                "SELECT count(*) FROM dashboard_message_evidence WHERE import_id=%s", (import_id,)
            ).fetchone()[0]
            if prior[0] != digest or count != len(records):
                raise ValueError("existing evidence import does not match its source")
            return {"import_id": import_id, "records": count, "created": False}
        conn.execute(
            "INSERT INTO dashboard_evidence_imports(import_id,source_account,observed_at,source_sha256,coverage) VALUES (%s,%s,%s,%s,%s)",
            (
                import_id,
                blob["account"],
                blob["captured_at"],
                digest,
                json.dumps(blob["coverage"], sort_keys=True),
            ),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO dashboard_message_evidence(import_id,record_id,area,channel,account,direction,provider_id,provider_id_status,occurred_at,timestamp_source,body,body_status,counterparty_handle,person_id,conversation_id,url,in_reply_to_url) VALUES ("
                + ",".join(["%s"] * 17)
                + ")",
                [(import_id, *r) for r in records],
            )
    return {"import_id": import_id, "records": len(records), "created": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blob", type=Path, default=ROOT / "data/x-gtm-latest.json")
    parser.add_argument("--credential-file", type=Path, default=ROOT / "accounts/crm-runtime.url")
    args = parser.parse_args()
    print(json.dumps(import_evidence(args.blob, args.credential_file)))


if __name__ == "__main__":
    main()
