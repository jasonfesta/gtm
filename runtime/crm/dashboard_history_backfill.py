"""Idempotently backfill verified local message history into the shared CRM."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import psycopg

from crm.database import ROOT


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def linkedin_rows():
    path = ROOT / "linkedin/data/linkedin.sqlite3"
    sha = digest(path)
    with sqlite3.connect(path) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(
            """SELECT m.*,a.public_identifier,a.display_name,p.canonical_name,p.crm_person_id,
                      c.platform_thread_id,pr.profile_url
               FROM messages m JOIN accounts a USING(account_id)
               JOIN people p USING(person_id)
               LEFT JOIN conversations c USING(conversation_id)
               LEFT JOIN profiles pr ON pr.profile_id=(SELECT min(p2.profile_id) FROM profiles p2 WHERE p2.person_id=m.person_id)"""
        ).fetchall()
    for row in rows:
        yield (
            "linkedin:" + row["message_id"],
            str(path.relative_to(ROOT)),
            sha,
            "linkedin",
            row["public_identifier"] or row["display_name"],
            row["direction"],
            row["message_kind"],
            row["platform_message_id"],
            row["occurred_at"],
            row["body"],
            "captured",
            "LinkedIn local message store; source=" + row["source"],
            row["canonical_name"],
            row["crm_person_id"],
            row["platform_thread_id"] or row["conversation_id"],
            row["profile_url"],
        )


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def confirmed_x_rows():
    roots = [ROOT / "x", ROOT / "logs"]
    for root in roots:
        for path in sorted(root.rglob("*.json")):
            relative = str(path.relative_to(ROOT))
            if not any(
                token in relative.lower()
                for token in ("publish", "receipt", "readback", "observation")
            ):
                continue
            try:
                payload = json.loads(path.read_text())
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            sha = digest(path)
            for item in objects(payload):
                facts = item.get("facts") if isinstance(item.get("facts"), dict) else {}
                provider_id = (
                    text(item.get("provider_comment_id"))
                    or text(item.get("provider_id"))
                    or text(facts.get("provider_id"))
                )
                body = (
                    text(item.get("exact_text")) or text(item.get("text")) or text(item.get("body"))
                )
                channel = text(item.get("channel")) or text(facts.get("channel"))
                status = text(item.get("status")) or text(facts.get("status"))
                confirmation = text(item.get("confirmation")) or text(facts.get("confirmation"))
                is_confirmed = (
                    item.get("confirmed") is True
                    or status == "confirmed"
                    or confirmation == "provider_readback"
                )
                if channel not in (None, "x") or not (provider_id and body and is_confirmed):
                    continue
                occurred = (
                    text(item.get("posted_at"))
                    or text(facts.get("posted_at"))
                    or text(item.get("observed_at"))
                    or text(facts.get("observed_at"))
                )
                if not occurred:
                    continue
                url = text(item.get("reply_url")) or text(facts.get("reply_url"))
                person_id = text(item.get("person_id")) or text(facts.get("person_id"))
                account = text(item.get("account")) or text(facts.get("account")) or "jasonfesta"
                yield (
                    "x:" + provider_id,
                    relative,
                    sha,
                    "x",
                    account.removeprefix("x:"),
                    "outbound",
                    "public_reply",
                    provider_id,
                    occurred,
                    body,
                    "captured",
                    "Confirmed provider publication/readback",
                    text(item.get("name")) or text(item.get("recipient")),
                    person_id,
                    text(item.get("conversation_url")) or text(facts.get("conversation_url")),
                    url,
                )


def backfill(credential_path: Path):
    by_id = {}
    for row in [*linkedin_rows(), *confirmed_x_rows()]:
        prior = by_id.get(row[0])
        if prior is None or (row[6] == "public_reply" and row[15] and not prior[15]):
            by_id[row[0]] = row
    sql_path = ROOT / "sql/gtm_dashboard.postgres.sql"
    with psycopg.connect(credential_path.read_text().strip(), connect_timeout=20) as conn:
        conn.execute("SET LOCAL statement_timeout TO '90s'")
        conn.execute(sql_path.read_text())
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO crm_gtm.dashboard_message_backfill
                   (record_id,source_path,source_sha256,channel,account,direction,message_kind,
                    provider_id,occurred_at,body,body_status,evidence_status,counterparty,
                    person_id,conversation_id,evidence_url)
                   VALUES ("""
                + ",".join(["%s"] * 16)
                + ") ON CONFLICT (record_id) DO UPDATE SET "
                "source_path=EXCLUDED.source_path,source_sha256=EXCLUDED.source_sha256,account=EXCLUDED.account,"
                "occurred_at=EXCLUDED.occurred_at,body=EXCLUDED.body,evidence_status=EXCLUDED.evidence_status,"
                "counterparty=EXCLUDED.counterparty,person_id=EXCLUDED.person_id,conversation_id=EXCLUDED.conversation_id,"
                "evidence_url=EXCLUDED.evidence_url",
                list(by_id.values()),
            )
        conn.execute("GRANT SELECT ON crm_gtm.v_gtm_dashboard_history TO gtm_posthog_dashboard")
        counts = conn.execute(
            "SELECT channel,count(*),count(*) FILTER (WHERE body_status='captured') FROM crm_gtm.v_gtm_dashboard_history GROUP BY channel ORDER BY channel"
        ).fetchall()
        imported = conn.execute(
            "SELECT channel,count(*) FROM crm_gtm.dashboard_message_backfill GROUP BY channel ORDER BY channel"
        ).fetchall()
    return {"candidate_rows": len(by_id), "imported": imported, "history": counts}


if __name__ == "__main__":
    result = backfill(ROOT / "accounts/crm-migration-admin.url")
    print(json.dumps(result, default=str))
