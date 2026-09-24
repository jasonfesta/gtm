from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "crm.sqlite3"
SCHEMA = ROOT / "sql" / "schema.sql"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def normalize_name(value: str) -> str:
    value = value.casefold().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()[:16]


def connect(path: Path):
    from crm.database import connect as backend_connect

    return backend_connect(path)


def initialize(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.executescript((ROOT / "sql/tags_schema.sql").read_text(encoding="utf-8"))
    conn.executescript((ROOT / "sql/relationships_schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    return conn


PILOT = [
    {
        "key": "orchid",
        "name": "Orchid",
        "company": "Orchid",
        "domain": "orchid.ai",
        "website": "https://orchid.ai/",
        "im_description": "Runs your day",
        "ab_description": "A personal assistant that lives in your messages",
        "category": "Productivity",
    },
    {
        "key": "folk",
        "name": "folk",
        "company": "folk",
        "domain": "folk.com",
        "website": "https://www.folk.com/",
        "im_description": "folk is your personal AI assistant",
        "ab_description": "The friend in your texts",
        "category": "Productivity",
    },
    {
        "key": "asmi",
        "name": "Asmi",
        "company": "Asmi",
        "domain": "asmiai.com",
        "website": "https://asmiai.com/",
        "im_description": "Asmi is your personal assistant",
        "ab_description": "AI that handles your chores in the physical world",
        "category": "Productivity",
    },
    {
        "key": "catch",
        "name": "Catch",
        "company": "Catch",
        "domain": "catchagent.ai",
        "website": "https://catchagent.ai/",
        "im_description": "The 24/7 AI executive assistant for busy professionals",
        "ab_description": "Your admin savior: scheduling, email and calls",
        "category": "Productivity",
    },
    {
        "key": "lucas",
        "name": "Lucas",
        "company": "Lucas",
        "domain": "meetlucas.ai",
        "website": "https://meetlucas.ai/",
        "im_description": "Assistant that texts first",
        "ab_description": "The assistant that texts you first",
        "category": "Productivity",
    },
]


TEMPLATES = [
    (
        "tpl_email_a",
        "email",
        "A",
        "Evidence-led introduction",
        "Idea for {{company_name}}",
        "Hi {{first_name}} — I found {{agent_name}} through {{source_name}} and noticed {{specific_observation}}. "
        "We help {{peer_group}} {{value_proposition}}. Would {{cta}} be useful?",
        [
            "first_name",
            "company_name",
            "agent_name",
            "source_name",
            "specific_observation",
            "peer_group",
            "value_proposition",
            "cta",
        ],
    ),
    (
        "tpl_email_b",
        "email",
        "B",
        "Outcome-led question",
        "{{specific_outcome}} for {{agent_name}}",
        "Hi {{first_name}} — quick question: is {{current_priority}} a focus for {{company_name}}? "
        "{{relevance_reason}}. We can help with {{specific_outcome}}. Open to {{cta}}?",
        [
            "first_name",
            "company_name",
            "agent_name",
            "current_priority",
            "relevance_reason",
            "specific_outcome",
            "cta",
        ],
    ),
    (
        "tpl_linkedin_a",
        "linkedin",
        "A",
        "Relevant connection note",
        None,
        "Hi {{first_name}} — came across {{agent_name}} via {{source_name}}. {{specific_observation}}. "
        "I work with {{peer_group}} on {{value_proposition}} and would enjoy connecting.",
        [
            "first_name",
            "agent_name",
            "source_name",
            "specific_observation",
            "peer_group",
            "value_proposition",
        ],
    ),
    (
        "tpl_linkedin_b",
        "linkedin",
        "B",
        "Founder-context connection note",
        None,
        "{{first_name}}, your work on {{agent_name}} caught my eye—especially {{specific_observation}}. "
        "I have an idea around {{specific_outcome}}. Happy to share if useful.",
        ["first_name", "agent_name", "specific_observation", "specific_outcome"],
    ),
    (
        "tpl_x_a",
        "x",
        "A",
        "Public-work opener",
        None,
        "Hey {{x_handle}} — saw {{specific_public_post_or_launch}} from {{agent_name}}. "
        "{{specific_observation}}. Curious whether {{current_priority}} is on the roadmap?",
        [
            "x_handle",
            "agent_name",
            "specific_public_post_or_launch",
            "specific_observation",
            "current_priority",
        ],
    ),
    (
        "tpl_x_b",
        "x",
        "B",
        "Concise value hypothesis",
        None,
        "{{x_handle}} — I have a concrete idea for helping {{agent_name}} {{specific_outcome}}, based on {{evidence}}. "
        "Worth sending the two-line version?",
        ["x_handle", "agent_name", "specific_outcome", "evidence"],
    ),
]


def seed_templates(conn: sqlite3.Connection) -> None:
    for template_id, channel, variant, name, subject, body, required in TEMPLATES:
        conn.execute(
            """
            INSERT INTO message_templates(
                template_id, channel, variant, name, subject_template, body_template,
                status, required_fields_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)
            ON CONFLICT(template_id) DO UPDATE SET
                subject_template=excluded.subject_template,
                body_template=excluded.body_template,
                required_fields_json=excluded.required_fields_json,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (template_id, channel, variant, name, subject, body, json.dumps(required)),
        )


def seed_pilot(conn: sqlite3.Connection) -> None:
    now = utc_now()
    for item in PILOT:
        agent_id = "agt_" + item["key"]
        company_id = "cmp_" + item["key"]
        conn.execute(
            """
            INSERT INTO companies(
                company_id, canonical_name, normalized_name, domain, website_url,
                research_status, confidence, notes
            ) VALUES (?, ?, ?, ?, ?, 'founder_research_pending', 90, ?)
            ON CONFLICT(company_id) DO UPDATE SET
                canonical_name=excluded.canonical_name,
                normalized_name=excluded.normalized_name,
                domain=excluded.domain,
                website_url=excluded.website_url,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                company_id,
                item["company"],
                normalize_name(item["company"]),
                item["domain"],
                item["website"],
                "Brand-level company identity supported by matching official domain on both directories; legal entity not yet verified.",
            ),
        )
        conn.execute(
            """
            INSERT INTO agents(
                agent_id, company_id, canonical_name, normalized_name, agent_kind,
                description, website_url, research_status, confidence, notes
            ) VALUES (?, ?, ?, ?, 'agent', ?, ?, 'complete', 95, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                company_id=excluded.company_id,
                canonical_name=excluded.canonical_name,
                normalized_name=excluded.normalized_name,
                description=excluded.description,
                website_url=excluded.website_url,
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                agent_id,
                company_id,
                item["name"],
                normalize_name(item["name"]),
                item["ab_description"],
                item["website"],
                "Pilot record deduplicated by exact normalized name plus identical official domain across both sources.",
            ),
        )

        source_rows = [
            (
                "src_ab_" + item["key"],
                "https://assistantbenchmark.com/agents/" + item["key"],
                item["name"] + " | Assistant Benchmark",
                "Assistant Benchmark",
                "directory",
                3,
            ),
            (
                "src_im_" + item["key"],
                "https://www.imessage.store/agent/" + item["key"],
                item["name"] + " — iMessage Agent Store",
                "iMessage Agent Store",
                "directory",
                3,
            ),
            (
                "src_official_" + item["key"],
                item["website"],
                item["company"] + " official website",
                item["company"],
                "official_site",
                1,
            ),
        ]
        for source_id, url, title, publisher, source_type, tier in source_rows:
            conn.execute(
                """
                INSERT INTO sources(source_id, url, title, publisher, source_type, quality_tier, accessed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET accessed_at=excluded.accessed_at
                """,
                (source_id, url, title, publisher, source_type, tier, now),
            )

        listings = [
            (
                "lst_ab_" + item["key"],
                "assistantbenchmark",
                item["key"],
                "src_ab_" + item["key"],
                item["name"].title() if item["name"] == "folk" else item["name"],
                item["ab_description"],
                "General",
            ),
            (
                "lst_im_" + item["key"],
                "imessage_store",
                item["key"],
                "src_im_" + item["key"],
                item["name"],
                item["im_description"],
                item["category"],
            ),
        ]
        for (
            listing_id,
            source_site,
            source_key,
            source_id,
            source_name,
            description,
            category,
        ) in listings:
            conn.execute(
                """
                INSERT INTO listings(
                    listing_id, source_site, source_key, agent_id, source_id, source_name,
                    source_description, source_category, official_url, listing_kind,
                    first_seen_at, last_seen_at, raw_record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'agent', ?, ?, ?)
                ON CONFLICT(source_site, source_key) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    source_id=excluded.source_id,
                    source_name=excluded.source_name,
                    source_description=excluded.source_description,
                    source_category=excluded.source_category,
                    official_url=excluded.official_url,
                    last_seen_at=excluded.last_seen_at,
                    is_active=1,
                    raw_record_json=excluded.raw_record_json
                """,
                (
                    listing_id,
                    source_site,
                    source_key,
                    agent_id,
                    source_id,
                    source_name,
                    description,
                    category,
                    item["website"],
                    now,
                    now,
                    json.dumps(
                        {
                            "pilot": True,
                            "name": source_name,
                            "description": description,
                            "official_url": item["website"],
                        },
                        sort_keys=True,
                    ),
                ),
            )
            claim_id = stable_id("clm", listing_id, "official_url", item["website"])
            conn.execute(
                """
                INSERT OR IGNORE INTO evidence_claims(
                    claim_id, entity_type, entity_id, field_name, claim_value, source_id,
                    evidence_relation, verification_status, confidence, excerpt, notes
                ) VALUES (?, 'agent', ?, 'website_url', ?, ?, 'supports', 'accepted', 95, ?, ?)
                """,
                (
                    claim_id,
                    agent_id,
                    item["website"],
                    source_id,
                    "The listing links to the same official domain used by the matching listing.",
                    "Directory evidence establishes product identity, not the legal company or founders.",
                ),
            )

        task_id = "tsk_founders_" + item["key"]
        conn.execute(
            """
            INSERT INTO research_tasks(
                task_id, entity_type, entity_id, task_type, status, priority, notes
            ) VALUES (?, 'company', ?, 'identify_founders', 'queued', 90, ?)
            ON CONFLICT(task_id) DO NOTHING
            """,
            (
                task_id,
                company_id,
                "Start with official team/about pages and announcements; do not infer identity from directory quotes alone.",
            ),
        )

    seed_templates(conn)
    run_id = "run_pilot_seed_20260909"
    conn.execute(
        """
        INSERT OR IGNORE INTO crawl_runs(
            run_id, source_site, mode, status, started_at, finished_at,
            records_discovered, records_changed, notes
        ) VALUES (?, 'research', 'pilot', 'complete', ?, ?, 5, 5, ?)
        """,
        (
            run_id,
            now,
            now,
            "Manual, reviewable seed from five cross-listed public agent pages. No full crawl and no outreach performed.",
        ),
    )
    conn.commit()


def rows_as_table(rows: Sequence[sqlite3.Row]) -> str:
    if not rows:
        return "(no rows)"
    headers = list(rows[0].keys())
    values = [["" if row[h] is None else str(row[h]) for h in headers] for row in rows]
    widths = [len(h) for h in headers]
    for row in values:
        for i, value in enumerate(row):
            widths[i] = min(60, max(widths[i], len(value)))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep = "  ".join("-" * widths[i] for i in range(len(headers)))
    rendered = [line, sep]
    for row in values:
        rendered.append(
            "  ".join(value[: widths[i]].ljust(widths[i]) for i, value in enumerate(row))
        )
    return "\n".join(rendered)


def command_init(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    conn.close()
    print("Initialized", args.db)
    return 0


def command_seed_pilot(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    seed_pilot(conn)
    counts = conn.execute(
        "SELECT (SELECT count(*) FROM agents) AS agents, (SELECT count(*) FROM companies) AS companies, "
        "(SELECT count(*) FROM listings) AS listings, (SELECT count(*) FROM message_templates) AS templates"
    ).fetchall()
    print(rows_as_table(counts))
    conn.close()
    return 0


def command_stats(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    tables = [
        "agents",
        "companies",
        "people",
        "contact_points",
        "listings",
        "sources",
        "discovery_candidates",
        "evidence_claims",
        "conflicts",
        "research_tasks",
        "outreach_events",
        "suppressions",
        "message_templates",
        "crawl_runs",
        "fetches",
    ]
    rows = []
    for table in tables:
        rows.append(
            {
                "table_name": table,
                "row_count": conn.execute("SELECT count(*) FROM " + table).fetchone()[0],
            }
        )
    print(rows_as_table([dict_row(row) for row in rows]))
    conn.close()
    return 0


def dict_row(value: Dict[str, object]) -> sqlite3.Row:
    class MappingRow(dict):
        def keys(self):  # type: ignore[override]
            return super().keys()

    return MappingRow(value)  # type: ignore[return-value]


def command_review_queue(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    rows = conn.execute(
        "SELECT queue_type, item_id, entity_type, entity_id, issue, status, priority, detail "
        "FROM v_review_queue ORDER BY priority DESC, created_at ASC"
    ).fetchall()
    print(rows_as_table(rows))
    conn.close()
    return 0


def command_leads(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    rows = conn.execute("SELECT * FROM v_contact_crm ORDER BY company_name, full_name").fetchall()
    print(rows_as_table(rows))
    conn.close()
    return 0


def command_integrity(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    errors: List[str] = []
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        errors.append("integrity_check: " + str(integrity))
    for row in conn.execute("PRAGMA foreign_key_check").fetchall():
        errors.append("foreign_key_check: " + repr(tuple(row)))
    bad_confirmed = conn.execute(
        "SELECT contact_id FROM contact_points WHERE verification_status='confirmed' "
        "AND (source_id IS NULL OR last_verified_at IS NULL)"
    ).fetchall()
    for row in bad_confirmed:
        errors.append("confirmed contact missing source or verification date: " + row[0])
    duplicates = conn.execute(
        "SELECT contact_type, normalized_value, count(*) n FROM contact_points "
        "GROUP BY contact_type, normalized_value HAVING COUNT(*) > 1"
    ).fetchall()
    for row in duplicates:
        errors.append("contact used by multiple people: " + repr(tuple(row)))
    if errors:
        print("FAILED")
        for error in errors:
            print("-", error)
        conn.close()
        return 1
    print("OK: database, foreign keys, and contact-verification invariants passed")
    conn.close()
    return 0


def command_export(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = args.output or (ROOT / "exports" / stamp)
    out_dir.mkdir(parents=True, exist_ok=False)
    objects = [
        "agents",
        "companies",
        "people",
        "company_people",
        "contact_points",
        "listings",
        "discovery_candidates",
        "sources",
        "evidence_claims",
        "conflicts",
        "research_tasks",
        "merge_events",
        "outreach_events",
        "suppressions",
        "message_templates",
        "draft_messages",
        "crawl_runs",
        "fetches",
        "v_contact_crm",
        "v_review_queue",
    ]
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='copy_revisions'"
    ).fetchone():
        objects.append("copy_revisions")
    for name in objects:
        cursor = conn.execute("SELECT * FROM " + name)
        headers = [col[0] for col in cursor.description]
        with (out_dir / (name + ".csv")).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows(cursor.fetchall())
    snapshot = out_dir / "crm.sqlite3"
    target = sqlite3.connect(str(snapshot))
    conn.backup(target)
    target.close()
    manifest = {
        "exported_at": utc_now(),
        "database": str(args.db),
        "files": sorted(p.name for p in out_dir.iterdir()),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(out_dir)
    conn.close()
    return 0


def command_log_contact(args: argparse.Namespace) -> int:
    conn = initialize(args.db)
    exists = conn.execute("SELECT 1 FROM people WHERE person_id=?", (args.person_id,)).fetchone()
    if not exists:
        print("Unknown person_id: " + args.person_id, file=sys.stderr)
        conn.close()
        return 2
    event_id = "evt_" + uuid.uuid4().hex[:16]
    occurred_at = args.occurred_at or utc_now()
    conn.execute(
        """
        INSERT INTO outreach_events(event_id, person_id, channel, direction, occurred_at, outcome, template_id, notes)
        VALUES (?, ?, ?, 'outbound', ?, ?, ?, ?)
        """,
        (
            event_id,
            args.person_id,
            args.channel,
            occurred_at,
            args.outcome,
            args.template_id,
            args.notes,
        ),
    )
    conn.commit()
    print(event_id)
    conn.close()
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Local SQLite CRM for AI agents and their makers")
    p.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite database path")
    sub = p.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Initialize or migrate the database")
    init_p.set_defaults(func=command_init)

    pilot_p = sub.add_parser("seed-pilot", help="Seed the reviewed five-agent pilot only")
    pilot_p.set_defaults(func=command_seed_pilot)

    stats_p = sub.add_parser("stats", help="Show table counts")
    stats_p.set_defaults(func=command_stats)

    review_p = sub.add_parser("review-queue", help="Show unresolved tasks and conflicts")
    review_p.set_defaults(func=command_review_queue)

    leads_p = sub.add_parser("leads", help="Show the contact CRM view")
    leads_p.set_defaults(func=command_leads)

    integrity_p = sub.add_parser("integrity", help="Run database and verification checks")
    integrity_p.set_defaults(func=command_integrity)

    export_p = sub.add_parser("export", help="Export normalized CSVs plus a SQLite snapshot")
    export_p.add_argument("--output", type=Path)
    export_p.set_defaults(func=command_export)

    contact_p = sub.add_parser(
        "log-contact", help="Log an already-sent contact event; never sends anything"
    )
    contact_p.add_argument("person_id")
    contact_p.add_argument("channel", choices=["email", "linkedin", "x"])
    contact_p.add_argument("--occurred-at", help="ISO-8601 timestamp; defaults to now")
    contact_p.add_argument(
        "--outcome",
        default="sent",
        choices=[
            "drafted",
            "sent",
            "delivered",
            "replied",
            "bounced",
            "declined",
            "opted_out",
            "unknown",
        ],
    )
    contact_p.add_argument("--template-id")
    contact_p.add_argument("--notes")
    contact_p.set_defaults(func=command_log_contact)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
