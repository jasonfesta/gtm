"""Apply simple operation-agent handoffs with the existing CRM package."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
CRM_ROOT = Path(os.environ.get("GTM_CRM_ROOT", ROOT / "runtime")).resolve()
if str(CRM_ROOT) not in sys.path:
    sys.path.insert(0, str(CRM_ROOT))

from crm.cli import initialize, stable_id, utc_now  # noqa: E402,F401
from crm.database import CANONICAL, connect, transaction  # noqa: E402
from crm.relationships import Relationships  # noqa: E402
from crm.tags import Tags  # noqa: E402

HUMAN_AUDIENCES = {
    "solo developer": "personal_agent_owners",
    "assistant developer": "assistant_developers",
    "technology-interested knowledge worker": "early_adopters",
}


def _required(payload, *names):
    missing = [name for name in names if payload.get(name) in (None, "")]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))


def _database(path):
    return CANONICAL if path is None else Path(path)


def _upsert_company(db, payload):
    _required(payload, "company_id", "canonical_name")
    normalized = payload.get("normalized_name") or payload["canonical_name"].casefold().strip()
    with connect(db) as connection:
        connection.execute(
            "INSERT INTO companies(company_id,canonical_name,normalized_name,legal_name,domain,"
            "website_url,linkedin_url,x_url,company_status,research_status,confidence,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(company_id) DO UPDATE SET "
            "canonical_name=excluded.canonical_name,normalized_name=excluded.normalized_name,"
            "legal_name=COALESCE(excluded.legal_name,companies.legal_name),"
            "domain=COALESCE(excluded.domain,companies.domain),"
            "website_url=COALESCE(excluded.website_url,companies.website_url),"
            "linkedin_url=COALESCE(excluded.linkedin_url,companies.linkedin_url),"
            "x_url=COALESCE(excluded.x_url,companies.x_url),"
            "company_status=excluded.company_status,research_status=excluded.research_status,"
            "confidence=excluded.confidence,updated_at=excluded.updated_at",
            (
                payload["company_id"],
                payload["canonical_name"],
                normalized,
                payload.get("legal_name"),
                payload.get("domain"),
                payload.get("website_url"),
                payload.get("linkedin_url"),
                payload.get("x_url"),
                payload.get("company_status", "unknown"),
                payload.get("research_status", "discovered"),
                payload.get("confidence", 0),
                utc_now(),
            ),
        )
    return {"company_id": payload["company_id"]}


def _upsert_human(db, payload):
    _required(payload, "person_id", "full_name")
    normalized = payload.get("normalized_name") or payload["full_name"].casefold().strip()
    with connect(db) as connection:
        connection.execute(
            "INSERT INTO people(person_id,primary_company_id,full_name,normalized_name,current_role,"
            "founder_status,identity_status,research_status,confidence,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(person_id) DO UPDATE SET "
            "primary_company_id=COALESCE(excluded.primary_company_id,people.primary_company_id),"
            "full_name=excluded.full_name,normalized_name=excluded.normalized_name,"
            "current_role=COALESCE(excluded.current_role,people.current_role),"
            "founder_status=excluded.founder_status,identity_status=excluded.identity_status,"
            "research_status=excluded.research_status,confidence=excluded.confidence,"
            "updated_at=excluded.updated_at",
            (
                payload["person_id"],
                payload.get("primary_company_id"),
                payload["full_name"],
                normalized,
                payload.get("current_role"),
                payload.get("founder_status", "unknown"),
                payload.get("identity_status", "unverified"),
                payload.get("research_status", "identity_pending"),
                payload.get("confidence", 0),
                utc_now(),
            ),
        )
    return {"person_id": payload["person_id"]}


def _upsert_agent(db, payload):
    if "canonical_name" not in payload and payload.get("name"):
        payload = {
            **payload,
            "canonical_name": payload["name"],
            "agent_kind": payload.get("kind")
            if payload.get("kind") in ("agent", "infrastructure_provider")
            else "unknown",
            "status": "inactive" if payload.get("kind") == "irrelevant" else "active",
        }
    _required(payload, "agent_id", "canonical_name")
    normalized = payload.get("normalized_name") or payload["canonical_name"].casefold().strip()
    with connect(db) as connection:
        connection.execute(
            "INSERT INTO agents(agent_id,company_id,canonical_name,normalized_name,agent_kind,"
            "description,website_url,status,research_status,confidence,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "company_id=COALESCE(excluded.company_id,agents.company_id),"
            "canonical_name=excluded.canonical_name,normalized_name=excluded.normalized_name,"
            "agent_kind=excluded.agent_kind,description=COALESCE(excluded.description,agents.description),"
            "website_url=COALESCE(excluded.website_url,agents.website_url),status=excluded.status,"
            "research_status=excluded.research_status,confidence=excluded.confidence,"
            "updated_at=excluded.updated_at",
            (
                payload["agent_id"],
                payload.get("company_id"),
                payload["canonical_name"],
                normalized,
                payload.get("agent_kind", "agent"),
                payload.get("description"),
                payload.get("website_url"),
                payload.get("status", "unknown"),
                payload.get("research_status", "discovered"),
                payload.get("confidence", 0),
                utc_now(),
            ),
        )
    return {"agent_id": payload["agent_id"]}


def _link_agent_owner(db, payload):
    if not payload.get("person_id") and payload.get("name"):
        payload = {
            **payload,
            "person_id": stable_id(
                "person", payload["name"].casefold().strip(), payload.get("source_url", "")
            ),
        }
        _upsert_human(
            db,
            {
                "person_id": payload["person_id"],
                "full_name": payload["name"],
                "identity_status": "single_source",
                "research_status": "review_needed",
            },
        )
    payload = {
        **payload,
        "evidence": payload.get("evidence") or payload.get("source_url"),
        "reviewer": payload.get("reviewer") or "agent discovery agent",
    }
    _required(payload, "agent_id", "person_id", "evidence", "reviewer")
    profile_id = "agent:" + payload["agent_id"]
    with connect(db) as connection:
        if not connection.execute(
            "SELECT 1 FROM agents WHERE agent_id=?", (payload["agent_id"],)
        ).fetchone():
            raise ValueError("unknown agent_id")
        if not connection.execute(
            "SELECT 1 FROM people WHERE person_id=?", (payload["person_id"],)
        ).fetchone():
            raise ValueError("unknown person_id")
        connection.execute(
            "INSERT INTO relationship_profiles(profile_id,agent_id,owner_person_id,evidence,reviewer,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET "
            "owner_person_id=excluded.owner_person_id,evidence=excluded.evidence,"
            "reviewer=excluded.reviewer,updated_at=excluded.updated_at",
            (
                profile_id,
                payload["agent_id"],
                payload["person_id"],
                payload["evidence"],
                payload["reviewer"],
                utc_now(),
            ),
        )
    return {"profile_id": profile_id, "owner_person_id": payload["person_id"]}


def _set_suppression(db, payload):
    _required(payload, "reason", "effective_at")
    if not any(payload.get(name) for name in ("person_id", "company_id", "contact_id")):
        raise ValueError("suppression needs person_id, company_id or contact_id")
    active = payload.get("is_active", True)
    if type(active) is not bool:
        raise ValueError("is_active must be a boolean")
    from crm.relationships import timestamp

    effective_at = timestamp(payload["effective_at"])
    with connect(db) as connection:
        route = connection.execute(
            "SELECT contact_id,legacy_contact_id,channel FROM relationship_contacts WHERE contact_id=?",
            (payload.get("contact_id"),),
        ).fetchone()
        legacy = connection.execute(
            "SELECT contact_id,contact_type FROM contact_points WHERE contact_id=?",
            (payload.get("contact_id"),),
        ).fetchone()
        if route and legacy and route["legacy_contact_id"] != legacy["contact_id"]:
            raise ValueError("ambiguous contact_id exists in both route namespaces")
        if payload.get("contact_id") and not route and not legacy:
            raise ValueError("suppression contact_id does not identify a known route")
        if legacy and payload.get("channel") and payload["channel"] != legacy["contact_type"]:
            raise ValueError("suppression channel differs from contact route")
        payload = dict(payload)
        table = "suppressions"
        if route:
            if payload.get("channel") and payload["channel"] != route["channel"]:
                raise ValueError("suppression channel differs from contact route")
            if payload.get("person_id") or payload.get("company_id"):
                raise ValueError("route suppression must target only its contact_id")
            if route["legacy_contact_id"]:
                payload["contact_id"] = route["legacy_contact_id"]
            else:
                table = "relationship_contact_suppressions"
        suppression_id = payload.get("suppression_id") or stable_id(
            "suppression",
            payload.get("person_id") or "",
            payload.get("company_id") or "",
            payload.get("contact_id") or "",
            payload.get("channel") or "",
            payload["reason"],
        )
        other_table = (
            "relationship_contact_suppressions" if table == "suppressions" else "suppressions"
        )
        if connection.execute(
            f"SELECT suppression_id FROM {other_table} WHERE suppression_id=?", (suppression_id,)
        ).fetchone():
            raise ValueError("suppression_id already identifies a different storage scope")
        fields = ["contact_id", "channel", "reason"]
        if table == "suppressions":
            fields = ["person_id", "company_id", *fields]
        existing = connection.execute(
            f"SELECT * FROM {table} WHERE suppression_id=?",
            (suppression_id,),
        ).fetchone()
        if existing and any(existing[field] != payload.get(field) for field in fields):
            raise ValueError("suppression_id already identifies a different scope or reason")
        columns = ["suppression_id", *fields, "is_active", "effective_at", "notes"]
        connection.execute(
            f"INSERT INTO {table}({','.join(columns)}) VALUES({','.join('?' for _ in columns)}) "
            "ON CONFLICT(suppression_id) DO UPDATE SET is_active=excluded.is_active,"
            "effective_at=excluded.effective_at,notes=excluded.notes",
            (
                suppression_id,
                *(payload.get(field) for field in fields),
                int(active),
                effective_at,
                payload.get("notes"),
            ),
        )
    return {"suppression_id": suppression_id, "is_active": active, "storage": table}


def _compact_event(payload, source_agent):
    target_human = payload.get("target_human_id") or payload.get("person_id")
    target_agent = payload.get("target_agent_id")
    if not target_human and not target_agent:
        return None, {"recorded": False, "reason": "unresolved_target"}
    status = str(payload.get("provider_status") or payload.get("state") or "").casefold()
    direction = str(payload.get("direction") or "outbound").casefold()
    action = str(
        payload.get("reaction_type")
        or payload.get("event_type")
        or payload.get("action_type")
        or payload.get("message_kind")
        or "outbound"
    ).casefold()
    if action in ("like", "reaction", "positive_reaction", "upvote"):
        kind = "like"
    elif direction == "inbound" or action in ("reply", "inbound_reply") or status == "replied":
        kind = "reply"
    else:
        kind = "outbound"
    confirmed = status in {"confirmed", "sent", "delivered", "replied", "posted", "published"}
    if kind != "outbound":
        confirmed = confirmed or status in {"observed", "detected", "recorded"}
    if not confirmed:
        return None, {"recorded": False, "provider_status": status or "unknown"}
    provider_id = (
        payload.get("reaction_id")
        or payload.get("provider_message_id")
        or payload.get("idempotency_key")
    )
    occurred_at = payload.get("occurred_at") or payload.get("attempted_at")
    if not occurred_at:
        raise ValueError("event_record needs attempted_at or occurred_at")
    observed_at = payload.get("observed_at") or occurred_at
    _required(payload, "channel", "sender_account")
    if not provider_id:
        raise ValueError("confirmed event_record needs provider_message_id or idempotency_key")
    evidence = (
        payload.get("evidence")
        or payload.get("permalink")
        or payload.get("parent_url")
        or payload.get("target_reference")
        or payload.get("conversation_id")
        or str(provider_id)
    )
    evidence_details = [
        ("reacted_to_provider_message_id", payload.get("reacted_to_provider_message_id")),
        ("resource_version", payload.get("resource_version")),
    ]
    for label, value in evidence_details:
        if value:
            evidence = f"{evidence} | {label}={value}"
    return {
        "entity_type": "human" if target_human else "agent",
        "entity_id": target_human or target_agent,
        "channel": payload["channel"],
        "kind": kind,
        "account_key": payload["sender_account"],
        "provider_id": str(provider_id),
        "observed_at": observed_at,
        "occurred_at": occurred_at,
        "evidence": str(evidence),
        "reviewer": source_agent or "operation agent",
        "target_is_ours": bool(payload.get("target_is_ours", kind == "like")),
        "target_url": payload.get("reacted_to_permalink") or payload.get("parent_url"),
        "interaction_url": payload.get("permalink"),
        "external_reference": payload.get("conversation_id")
        or payload.get("reacted_to_provider_message_id"),
    }, None


def _email_event(db, payload):
    person_id = payload.get("person_id")
    state = str(payload.get("state") or "uncertain").casefold()
    if not person_id:
        return {"recorded": False, "reason": "unresolved_target", "state": state}
    if state in {"prepared", "enrolled", "scheduled", "submitted", "failed", "uncertain"}:
        return {"recorded": False, "state": state}
    outcome = "opted_out" if state == "complained" else state
    if outcome not in {"sent", "delivered", "replied", "bounced", "opted_out"}:
        return {"recorded": False, "state": state}
    occurred_at = payload.get("occurred_at") or utc_now()
    provider_id = (
        payload.get("provider_message_id")
        or payload.get("provider_lead_id")
        or payload.get("idempotency_key")
    )
    _required(payload, "sender_account")
    if not provider_id:
        raise ValueError("email outcome needs a provider or idempotency ID")
    event_id = stable_id("email_event", person_id, str(provider_id), outcome)
    with connect(db) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO outreach_events(event_id,person_id,channel,direction,"
            "occurred_at,outcome,template_id,external_reference,notes) VALUES(?,?,'email',?,?,?,?,?,?)",
            (
                event_id,
                person_id,
                "inbound" if outcome == "replied" else "outbound",
                occurred_at,
                outcome,
                payload.get("template_id"),
                payload.get("provider_reference")
                or payload.get("conversation_id")
                or str(provider_id),
                json.dumps(
                    {
                        "provider": payload.get("provider"),
                        "campaign_id": payload.get("campaign_id"),
                        "sender_account": payload.get("sender_account"),
                        "crm_category": payload.get("crm_category"),
                    },
                    sort_keys=True,
                ),
            ),
        )
    suppression = None
    if outcome == "opted_out":
        suppression = _set_suppression(
            db,
            {
                "person_id": person_id,
                "channel": "email",
                "reason": "opt_out",
                "effective_at": occurred_at,
            },
        )
    return {"event_id": event_id, "outcome": outcome, "suppression": suppression}


def apply_operation(operation, *, database=None, source_agent=None):
    if not isinstance(operation, dict):
        raise ValueError("operation must be an object")
    action = operation.get("action")
    payload = operation.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("operation payload must be an object")
    db = _database(database)
    if action == "company_upsert":
        return _upsert_company(db, payload)
    if action == "human_upsert":
        if "person_id" not in payload and payload.get("identity_key"):
            results = []
            for nested in _human_discovery_operations({"records": [payload]}):
                results.append(
                    {
                        "action": nested["action"],
                        "result": apply_operation(
                            nested, database=database, source_agent=source_agent
                        ),
                    }
                )
            return {"operations": results}
        return _upsert_human(db, payload)
    if action == "agent_upsert":
        return _upsert_agent(db, payload)
    if action == "relationship_classify":
        if "entity_type" not in payload and payload.get("agent_id"):
            capabilities = payload.get("capabilities") or {}
            payload = {
                "entity_type": "agent",
                "entity_id": payload["agent_id"],
                "audience": payload["audience"],
                "evidence": payload.get("evidence")
                or payload.get("source_url")
                or payload["agent_id"],
                "reviewer": payload.get("reviewer") or source_agent or "agent discovery agent",
                **capabilities,
            }
        return Relationships(db).classify(**payload)
    if action == "contact_upsert":
        payload = dict(payload)
        evidence_url = payload.pop("agent_operated_source_url", None)
        payload.pop("agent_operated_evidence", None)
        if payload.get("agent_operated") is not None:
            payload["agent_operated_evidence_url"] = (
                payload.get("agent_operated_evidence_url") or evidence_url
            )
            payload["agent_operated_verified_at"] = (
                payload.get("agent_operated_verified_at")
                or payload.get("verified_at")
                or payload.get("last_verified_at")
            )
        if "entity_type" not in payload and payload.get("agent_id"):
            published = payload.get("verification_status") in ("published", "confirmed")
            payload = {
                **payload,
                "entity_type": "agent",
                "entity_id": payload["agent_id"],
                "availability": payload.get("availability")
                or ("available" if published and payload.get("verified_at") else "unknown"),
                "last_verified_at": payload.get("last_verified_at") or payload.get("verified_at"),
                "reviewer": payload.get("reviewer") or source_agent or "agent discovery agent",
            }
            payload.pop("agent_id", None)
            payload.pop("verification_status", None)
            payload.pop("verified_at", None)
        return Relationships(db).save_contact(**payload)
    if action == "event_record":
        properties = payload.get("properties")
        if (
            operation.get("controlled_test") is True
            or payload.get("controlled_test") is True
            or (isinstance(properties, dict) and properties.get("controlled_test") is True)
        ):
            raise ValueError("controlled_test events cannot enter the production CRM ledger")
        if payload.get("channel") == "email" and "state" in payload:
            return _email_event(db, payload)
        if "entity_type" not in payload:
            payload, result = _compact_event(payload, source_agent)
            if result is not None:
                return result
        return Relationships(db).record(**payload)
    if action == "tag_set":
        return Tags(db).set(**payload)
    if action == "agent_owner_link":
        return _link_agent_owner(db, payload)
    if action == "suppression_set":
        return _set_suppression(db, payload)
    raise ValueError("unknown CRM action: " + str(action))


def _human_discovery_operations(handoff):
    observed_at = handoff.get("observed_at") or utc_now()
    operations = []
    for record in handoff.get("records") or []:
        identity = str(record.get("identity_key") or "").strip()
        name = str(record.get("name") or "").strip()
        if not identity or not name:
            continue
        person_id = stable_id("person", identity)
        evidence_rows = record.get("sources") or []
        evidence_urls = [
            row.get("source_url") or row.get("profile_url")
            for row in evidence_rows
            if row.get("source_url") or row.get("profile_url")
        ]
        evidence = "; ".join(dict.fromkeys(evidence_urls)) or record.get("profile_url")
        relevance = str(record.get("darwin_relevance") or "").strip()
        if relevance:
            evidence = (evidence + " | " if evidence else "") + relevance
        operations.append(
            {
                "action": "human_upsert",
                "payload": {
                    "person_id": person_id,
                    "full_name": name,
                    "identity_status": "single_source",
                    "research_status": "review_needed",
                },
            }
        )
        audience = HUMAN_AUDIENCES.get(record.get("audience"))
        if audience and evidence:
            operations.append(
                {
                    "action": "relationship_classify",
                    "payload": {
                        "entity_type": "human",
                        "entity_id": person_id,
                        "audience": audience,
                        "evidence": evidence,
                        "reviewer": "human discovery agent",
                    },
                }
            )
        profile = str(record.get("profile_url") or "").strip()
        if profile:
            channel = next(
                (
                    row.get("source")
                    for row in evidence_rows
                    if row.get("profile_url") == profile
                    and row.get("source") in ("x", "reddit", "linkedin")
                ),
                None,
            )
            if channel:
                operations.append(
                    {
                        "action": "contact_upsert",
                        "payload": {
                            "entity_type": "human",
                            "entity_id": person_id,
                            "channel": channel,
                            "address": profile,
                            "availability": "unknown",
                            "source_url": evidence_urls[0] if evidence_urls else profile,
                            "reviewer": "human discovery agent",
                            "last_verified_at": observed_at,
                        },
                    }
                )
    return operations


def normalize_handoff(handoff):
    if handoff.get("handoff") == "human_discovery_to_crm":
        return {
            "handoff_id": handoff.get("run_id") or "human-discovery",
            "source_agent": "human discovery agent",
            "operations": _human_discovery_operations(handoff),
        }
    return handoff


def export_snapshot(
    *, database=None, entity_type=None, entity_id=None, audience=None, limit=200, offset=0
):
    """Return a bounded read-only CRM snapshot for another operation agent."""
    if entity_type not in ("human", "agent"):
        raise ValueError("entity_type must be human or agent")
    if limit < 1 or limit > 1000 or offset < 0:
        raise ValueError("limit must be 1..1000 and offset must be nonnegative")
    db = _database(database)
    records = Relationships(db).list(entity_type, entity_id, audience)
    with connect(db) as connection:
        agents = {
            row["agent_id"]: dict(row)
            for row in connection.execute(
                "SELECT agent_id,description,website_url,status FROM agents"
            )
        }
        suppressions = [
            dict(row)
            for row in connection.execute(
                "SELECT 'suppressions' AS storage,suppression_id,person_id,company_id,contact_id,channel,reason,effective_at "
                "FROM suppressions WHERE is_active=1 ORDER BY effective_at,suppression_id"
            )
        ]
        suppressions.extend(
            dict(row)
            for row in connection.execute(
                "SELECT 'relationship_contact_suppressions' AS storage,suppression_id,NULL AS person_id,NULL AS company_id,contact_id,channel,"
                "reason,effective_at FROM relationship_contact_suppressions WHERE is_active=1 "
                "ORDER BY effective_at,suppression_id"
            )
        )
    for record in records:
        _mark_x_chat_surface(record)
        history = record.get("contact_history") or []
        record["history_truncated"] = len(history) > 100
        record["contact_history"] = history[:100]
        if entity_type == "agent":
            details = agents.get(record["entity_id"], {})
            record.update(
                description=details.get("description"),
                website_url=details.get("website_url"),
                status=details.get("status"),
            )
        contacts = record.get("contact_points") or []

        def matches_route(suppression, contact):
            # Legacy and relationship IDs belong to separate namespaces. A human
            # overlay exposes its legacy ID; an unoverlaid legacy row has no profile.
            if suppression["storage"] == "relationship_contact_suppressions":
                route_id = contact.get("contact_id") if contact.get("profile_id") else None
            else:
                route_id = contact.get("legacy_contact_id")
                if not contact.get("profile_id"):
                    route_id = contact.get("contact_id")
            return bool(route_id) and suppression["contact_id"] == route_id

        applicable = [
            suppression
            for suppression in suppressions
            if (entity_type == "human" and suppression["person_id"] == record["entity_id"])
            or (suppression["company_id"] and suppression["company_id"] == record.get("company_id"))
            or any(matches_route(suppression, contact) for contact in contacts)
        ]
        record["active_suppressions"] = applicable
        record["suppression_context_complete"] = True
        for contact in contacts:
            if contact.get("agent_operated") is not None:
                contact["agent_operated"] = bool(contact["agent_operated"])
            contact["active_suppressions"] = [
                suppression
                for suppression in applicable
                if (not suppression["contact_id"] or matches_route(suppression, contact))
                and (not suppression["channel"] or suppression["channel"] == contact.get("channel"))
            ]
            contact["suppressed"] = bool(contact["active_suppressions"])
    page = records[offset : offset + limit]
    return {
        "schema_version": 1,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "audience": audience,
        "offset": offset,
        "limit": limit,
        "total": len(records),
        "count": len(page),
        "has_more": offset + len(page) < len(records),
        "records": page,
    }


def _mark_x_chat_surface(value):
    if isinstance(value, list):
        for item in value:
            _mark_x_chat_surface(item)
    elif isinstance(value, dict):
        url = value.get("interaction_url") or ""
        parsed = urlsplit(url)
        if parsed.hostname in ("x.com", "www.x.com", "twitter.com", "www.twitter.com") and (
            parsed.path.startswith("/i/chat/") or parsed.path.startswith("/messages/")
        ):
            if "surface" in value:
                value["surface"] = "dm"
        for item in value.values():
            if isinstance(item, (dict, list)):
                _mark_x_chat_surface(item)


def apply_handoff(handoff, *, database=None, dry_run=False):
    if not isinstance(handoff, dict):
        raise ValueError("handoff must be an object")
    controlled_test = handoff.get("controlled_test") is True
    handoff = normalize_handoff(handoff)
    _required(handoff, "handoff_id", "source_agent")
    operations = handoff.get("operations")
    if not isinstance(operations, list):
        raise ValueError("operations must be an array")
    digest = hashlib.sha256(
        json.dumps(handoff, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if dry_run:
        return {
            "handoff_id": handoff["handoff_id"],
            "source_agent": handoff["source_agent"],
            "status": "dry_run",
            "operation_count": len(operations),
            "handoff_sha256": digest,
        }
    results = []
    index = None
    applying = True
    try:
        if controlled_test:
            raise ValueError("controlled_test handoffs cannot enter the production CRM ledger")
        with transaction(_database(database)):
            for index, operation in enumerate(operations):
                result = apply_operation(
                    operation, database=database, source_agent=handoff["source_agent"]
                )
                results.append({"action": operation.get("action"), "result": result})
            applying = False
    except Exception as error:
        # A failed commit may have reached the remote database. Keep that distinct
        # from an operation failure whose transaction was rolled back.
        return {
            "handoff_id": handoff["handoff_id"],
            "source_agent": handoff["source_agent"],
            "status": "failed" if applying else "uncertain",
            "operation_count": len(operations),
            "completed_operations": 0 if applying else None,
            "failed_operation": index if applying else None,
            "rolled_back": applying,
            "error": type(error).__name__ + ": " + str(error),
            "results": [],
            "handoff_sha256": digest,
        }
    return {
        "handoff_id": handoff["handoff_id"],
        "source_agent": handoff["source_agent"],
        "status": "completed",
        "operation_count": len(operations),
        "results": results,
        "handoff_sha256": digest,
    }
