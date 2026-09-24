"""Shared CRM context and local append-only copy. No provider or model calls."""

import hashlib
import json
import re
import uuid
from pathlib import Path

from crm.cli import DEFAULT_DB, ROOT, utc_now
from crm.database import configured, private_connection
from crm.route_review import latest_review
from crm.tags import Tags

LIMITS = {"email": 60, "dm": 40, "reply_dm": 35}


class CopyBuilder:
    def __init__(self, db=DEFAULT_DB):
        self.db = Path(db)
        with self.connection() as conn:
            conn.executescript((ROOT / "sql/copy_schema.sql").read_text())
            if not configured(self.db):
                conn.executescript((ROOT / "sql/tags_schema.sql").read_text())

    def connection(self):
        return private_connection(self.db)

    def context(self, agent_id, person_id=None):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
            if not row:
                raise ValueError("unknown agent_id")
            agent = dict(row)
            person = None
            if person_id:
                row = conn.execute(
                    "SELECT * FROM people WHERE person_id=?", (person_id,)
                ).fetchone()
                if not row:
                    raise ValueError("unknown person_id")
                person = dict(row)
                linked = (
                    person["primary_company_id"] == agent["company_id"]
                    or conn.execute(
                        "SELECT 1 FROM company_people WHERE company_id=? AND person_id=?",
                        (agent["company_id"], person_id),
                    ).fetchone()
                )
                if not linked:
                    raise ValueError("person is not linked to this agent company")
            entities = [("agent", agent_id), ("company", agent["company_id"])]
            if person_id:
                entities.append(("person", person_id))
            evidence = []
            for kind, entity in entities:
                evidence += [
                    dict(r)
                    for r in conn.execute(
                        "SELECT e.*, s.url AS source_url FROM evidence_claims e JOIN sources s USING(source_id) "
                        "WHERE entity_type=? AND entity_id=? AND verification_status='accepted'",
                        (kind, entity),
                    )
                ]
            listings = [
                dict(r)
                for r in conn.execute(
                    "SELECT source_name,source_description,official_url,source_id FROM listings WHERE agent_id=?",
                    (agent_id,),
                )
            ]
            route_review = latest_review(conn, person_id) if person_id else None
        guidelines = (ROOT / "copy/copy.md").read_text()
        return {
            "copy_guidelines": guidelines,
            "copy_guidelines_sha256": hashlib.sha256(guidelines.encode()).hexdigest(),
            "daily_copy_workflow": (ROOT / "copy/WORKFLOW.md").read_text(),
            "utm_formation_rules": (ROOT / "UTM/FORMATION_RULES.md").read_text(),
            "voice_examples": (ROOT / "copy/drafts/voice-session/approved-examples.md").read_text()
            if (ROOT / "copy/drafts/voice-session/approved-examples.md").is_file()
            else "No approved voice examples available locally; calibration is pending.",
            "brief": (ROOT / "copy/brief.md").read_text(),
            "agent": agent,
            "person": person,
            "person_tags": Tags(self.db).list(person_id) if person_id else [],
            "evidence": evidence,
            "directory_context": listings,
            "outreach_review": route_review,
            "formats": {f: (ROOT / ("copy/templates/" + f + ".md")).read_text() for f in LIMITS},
            "instructions": "Read copy_guidelines, daily_copy_workflow, utm_formation_rules, brief and approved voice_examples before writing or revising. "
            "Confirm developer-only campaign fit. Use fresh same-session person context; "
            "match guide tonality, punctuation and character ranges. "
            "Generate, critique, rewrite, then save original prose with the operating LLM each daily run. "
            "Initial introductions identify sender, product and one supported offer, ending with one verified link. "
            "Missing verified destination or unsupported link delivery is a hold. "
            "Use only registered campaign identifiers; preserve existing aggregate utm_id semantics. "
            "If examples are pending, do not claim an approved voice match. "
            "Write original contextual prose, not fixed openings or A-question/B-example formulas. "
            "A/b are editorial labels. Write short lowercase prose. Use only supported facts. "
            "Do not imply a verified founder identity without person evidence. Do not invent an offer. "
            "Apply the latest outreach_review role and contact dispositions: original records may be historical or held. "
            "A route score is not outreach readiness or authorization. "
            "A private reply requires the actual incoming message; a public reply requires the actual post "
            "and remains local Markdown until supported storage exists. Save supported formats through copy_save.",
        }

    def save(
        self,
        format,
        variant,
        channel,
        agent_id,
        body,
        evidence_ids,
        editor,
        expected_revision,
        draft_id=None,
        person_id=None,
        subject="",
        incoming_message="",
    ):
        if format not in LIMITS or variant not in ("a", "b"):
            raise ValueError("invalid format/variant")
        if channel not in ("email", "x", "linkedin") or (format == "email") != (channel == "email"):
            raise ValueError("format/channel mismatch")
        if not body.strip() or not editor.strip():
            raise ValueError("body and editor required")
        if len(body.split()) > LIMITS[format]:
            raise ValueError("body exceeds short-copy word limit")
        if format == "email" and not (1 <= len(subject.split()) <= 6):
            raise ValueError("email subject requires 1–6 words")
        if format != "email" and subject:
            raise ValueError("DM formats do not have subjects")
        if format == "reply_dm" and not incoming_message.strip():
            raise ValueError("actual incoming message required")
        for value in (subject, body):
            prose = re.sub(r"https?://\S+|[\w.+-]+@[\w.-]+|@\w+", "", value)
            if prose != prose.lower():
                raise ValueError("prose and subject must be lowercase")
            if "{{" in value or "}}" in value:
                raise ValueError("replace all template placeholders")
        context = self.context(agent_id, person_id)
        available = {e["claim_id"] for e in context["evidence"]}
        if not evidence_ids or not set(evidence_ids).issubset(available):
            raise ValueError("provide accepted evidence claim IDs from copy_context")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        if draft_id and not re.fullmatch(r"cpy_[0-9a-f]{16}", draft_id):
            raise ValueError("invalid draft_id")
        draft_id = draft_id or "cpy_" + uuid.uuid4().hex[:16]
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT * FROM copy_revisions WHERE draft_id=? ORDER BY revision DESC LIMIT 1",
                (draft_id,),
            ).fetchone()
            actual = previous["revision"] if previous else 0
            if actual != expected_revision:
                raise ValueError("revision conflict: read the current draft before editing")
            if previous and (
                previous["agent_id"],
                previous["person_id"],
                previous["format"],
                previous["variant"],
                previous["channel"],
            ) != (agent_id, person_id, format, variant, channel):
                raise ValueError("draft identity cannot change; create a new draft")
            conn.execute(
                "INSERT INTO copy_revisions(draft_id,revision,format,variant,channel,agent_id,person_id,subject,body,incoming_message,evidence_ids_json,editor,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    actual + 1,
                    format,
                    variant,
                    channel,
                    agent_id,
                    person_id,
                    subject,
                    body,
                    incoming_message,
                    json.dumps(evidence_ids),
                    editor,
                    utc_now(),
                ),
            )
        return {"draft_id": draft_id, "revision": actual + 1, "status": "draft"}

    def list(self):
        with self.connection() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT draft_id,max(revision) AS revision,format,variant,channel,agent_id FROM copy_revisions GROUP BY draft_id ORDER BY draft_id"
                )
            ]

    def read(self, draft_id):
        with self.connection() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM copy_revisions WHERE draft_id=? ORDER BY revision", (draft_id,)
                )
            ]
        if not rows:
            raise ValueError("unknown draft_id")
        return rows

    def export(self, draft_id):
        rows = self.read(draft_id)
        paths = []
        for row in rows:
            target = ROOT / "copy/drafts" / (row["draft_id"] + "-r" + str(row["revision"]) + ".md")
            target.parent.mkdir(exist_ok=True)
            target.write_text(
                "# "
                + row["format"]
                + " / "
                + row["variant"]
                + "\n\n"
                + "status: draft\n\neditor: "
                + row["editor"]
                + "\n\n"
                + ("subject: " + row["subject"] + "\n\n" if row["subject"] else "")
                + row["body"]
                + "\n\nevidence: "
                + row["evidence_ids_json"]
                + "\n"
            )
            paths.append(str(target))
        return {"paths": paths}
