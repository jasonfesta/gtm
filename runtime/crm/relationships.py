"""Human and agent relationships, contact arrays and factual engagement summaries.

No provider calls or sending. Legacy watcher history is read live, not copied.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from crm.cli import DEFAULT_DB, stable_id, utc_now
from crm.database import connect

HUMAN_CHANNELS = ("github", "reddit", "x", "linkedin", "email", "hacker-news")
AGENT_CHANNELS = (
    "agent_email",
    "agentdm",
    "masumi",
    "agentlist",
    "a2a",
    "agents_breakroom",
    "moltbook",
    "webmcp",
    "discord",
)
AUDIENCES = {
    "human": {
        "personal_agent_owners": "Personal agent owners",
        "assistant_developers": "Assistant developers",
        "early_adopters": "Early adopters",
    },
    "agent": {
        "personal_agents": "Personal agents",
        "assistant_agents": "Assistant agents",
        "research_agents": "Research agents",
    },
}
CHANNEL_LABELS = dict(
    zip(
        HUMAN_CHANNELS + AGENT_CHANNELS,
        (
            "GitHub",
            "Reddit",
            "X",
            "linkedin",
            "Email",
            "Hacker News",
            "Agent email / AgentMail",
            "AgentDM",
            "Masumi Agent Messenger",
            "AgentList messaging",
            "A2A",
            "Agents Breakroom",
            "Moltbook",
            "WebMCP",
            "Discord",
        ),
    )
)
CAPABILITIES = (
    "can_receive_requests",
    "can_call_external_apis",
    "can_connect_mcp",
    "can_install_integrations",
    "owner_approval_required",
)
KINDS = (
    "like",
    "comment",
    "reply",
    "outbound",
    "installed",
    "mcp_connected",
    "search",
    "repeat_usage",
    "referral",
)


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None or parsed > datetime.now(timezone.utc):
        raise ValueError("past or present timezone-aware timestamp required")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def required(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("nonempty evidence, identity and reviewer required")
    return value.strip()


def catalog():
    return [
        dict(
            entity_type=side,
            audience=key,
            label=label,
            channels=[dict(channel=c, label=CHANNEL_LABELS[c]) for c in channels(side, key)],
        )
        for side, choices in AUDIENCES.items()
        for key, label in choices.items()
    ]


def channels(side, audience=None):
    if side not in AUDIENCES:
        raise ValueError("entity_type must be human or agent")
    if side == "agent":
        return AGENT_CHANNELS
    return HUMAN_CHANNELS[1:] if audience == "early_adopters" else HUMAN_CHANNELS


def _identity(conn, entity_type, entity_id):
    channels(entity_type)
    table, key = ("people", "person_id") if entity_type == "human" else ("agents", "agent_id")
    if not conn.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (entity_id,)).fetchone():
        raise ValueError("unknown CRM identity")
    return entity_type + ":" + entity_id


def _ensure(conn, entity_type, entity_id, evidence, reviewer):
    profile_id = _identity(conn, entity_type, entity_id)
    conn.execute(
        "INSERT OR IGNORE INTO relationship_profiles "
        "(profile_id,person_id,agent_id,evidence,reviewer,updated_at) VALUES(?,?,?,?,?,?)",
        (
            profile_id,
            entity_id if entity_type == "human" else None,
            entity_id if entity_type == "agent" else None,
            required(evidence),
            required(reviewer),
            utc_now(),
        ),
    )
    return profile_id


def _known_time(value):
    try:
        return timestamp(value) if value else None
    except (ValueError, TypeError, AttributeError):
        return None


def _our_x_post(url, account):
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    return (
        parsed.hostname in ("x.com", "www.x.com", "twitter.com", "www.twitter.com")
        and len(parts) == 3
        and parts[1] == "status"
        and parts[0].casefold() == account.lstrip("@").casefold()
    )


def contact_columns(contacts):
    def first(*channels):
        matches = [r for r in contacts if r["channel"] in channels and r["address"]]
        matches.sort(
            key=lambda r: (
                r.get("availability") == "unavailable",
                -int(r.get("is_primary", 0)),
                r["contact_id"],
            )
        )
        return matches[0]["address"] if matches else None

    return dict(
        email=first("email", "agent_email"),
        x_url=first("x"),
        linkedin_url=first("linkedin"),
        github_url=first("github"),
        reddit_url=first("reddit"),
        moltbook_url=first("moltbook"),
        dm=[
            dict(channel=r["channel"], address=r["address"], availability=r["availability"])
            for r in contacts
            if r["address"]
            and (
                r.get("supports_dm") == "yes" or r["channel"] in ("agentdm", "masumi", "agentlist")
            )
        ],
        agentdm=first("agentdm"),
        masumi=first("masumi"),
        agentlist=first("agentlist"),
        a2a=first("a2a"),
        agents_breakroom=first("agents_breakroom"),
    )


def mirror_interaction(
    conn,
    *,
    event_id,
    person_id,
    kind,
    account_key,
    provider_id,
    observed_at,
    occurred_at=None,
    target_is_ours=True,
    target_url=None,
    interaction_url=None,
    evidence,
    reviewer,
):
    """Mirror a watcher receipt in the same transaction, preserving true event/observation times."""
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='relationship_events'"
    ).fetchone():
        return  # Compatibility with pre-upgrade offline fixtures only.
    observed = timestamp(observed_at)
    occurred = timestamp(occurred_at) if occurred_at else None
    if occurred and occurred > observed:
        raise ValueError("interaction occurrence cannot follow observation")
    profile = _ensure(conn, "human", person_id, evidence, reviewer)
    values = dict(
        event_id=event_id,
        profile_id=profile,
        channel="x",
        kind=kind,
        account_key=account_key,
        provider_id=provider_id,
        occurred_at=occurred,
        observed_at=observed,
        target_is_ours=int(target_is_ours),
        target_url=target_url,
        interaction_url=interaction_url,
        evidence=evidence,
        reviewer=reviewer,
    )
    prior = conn.execute(
        "SELECT * FROM relationship_events WHERE event_id=?", (event_id,)
    ).fetchone()
    if prior:
        if any(
            prior[k] != v
            for k, v in values.items()
            if k not in ("evidence", "reviewer", "observed_at")
        ):
            raise ValueError("conflicting watcher interaction receipt")
        return
    conn.execute(
        "INSERT INTO relationship_events ("
        + ",".join(values)
        + ") VALUES ("
        + ",".join("?" for _ in values)
        + ")",
        tuple(values.values()),
    )


def event_surface(event):
    if event["event_id"].startswith(("xdm_", "dm_x_")):
        return "dm"
    if (
        event.get("interaction_url")
        or event["kind"] in ("like", "comment")
        or event["event_id"].startswith("x_notification_")
    ):
        return "public"
    return "unknown"


def contact_entry(event):
    occurred = _known_time(event.get("occurred_at"))
    observed = _known_time(event.get("observed_at"))
    return {
        "event_id": event["event_id"],
        "channel": event["channel"],
        "kind": event["kind"],
        "direction": "outbound" if event["kind"] == "outbound" else "inbound",
        "occurred_at": occurred,
        "observed_at": observed,
        "effective_at": occurred or observed,
        "time_basis": "occurred" if occurred else "observed" if observed else "unknown",
        **{
            key: event.get(key)
            for key in (
                "account_key",
                "provider_id",
                "surface",
                "source",
                "target_url",
                "interaction_url",
                "external_reference",
            )
        },
    }


def summarize(events):
    result = {}
    for kind in KINDS:
        rows = [
            e
            for e in events
            if e["kind"] == kind
            and (kind not in ("like", "comment", "reply") or e["target_is_ours"])
        ]
        known = [e for e in rows if _known_time(e.get("occurred_at"))]
        latest = max(
            known, key=lambda e: (_known_time(e["occurred_at"]), e["event_id"]), default=None
        )
        result["last_" + kind + "_at"] = _known_time(latest["occurred_at"]) if latest else None
        result["last_" + kind] = latest
        result["last_" + kind + "_observed_at"] = max(
            (_known_time(e.get("observed_at")) for e in rows if _known_time(e.get("observed_at"))),
            default=None,
        )
        result[kind + "_unknown_time_count"] = sum(
            not _known_time(e.get("occurred_at")) for e in rows
        )
    entries = [
        contact_entry(e)
        for e in events
        if e["kind"] == "outbound"
        or (e["kind"] in ("like", "comment", "reply") and e["target_is_ours"])
    ]
    entries.sort(key=lambda e: (e["effective_at"] or "", e["event_id"]), reverse=True)
    # Keep complete history plus the latest of every channel/kind/direction/account/surface.
    result["contact_history"] = entries
    latest_by_route = {}
    for entry in entries:
        key = tuple(entry[k] for k in ("channel", "kind", "direction", "account_key", "surface"))
        latest_by_route.setdefault(key, entry)
    result["last_contact"] = list(latest_by_route.values())
    result["latest_contact"] = entries[0] if entries else None
    result["last_contact_effective_at"] = entries[0]["effective_at"] if entries else None
    result["last_contact_time_basis"] = entries[0]["time_basis"] if entries else None
    known = [e for e in entries if e["occurred_at"]]
    latest = known[0] if known else None
    result["last_contact_at"] = latest["occurred_at"] if latest else None
    result["last_contact_channel"] = latest["channel"] if latest else None
    result["last_outbound_contact_at"] = result["last_outbound_at"]
    return result


class Relationships:
    def __init__(self, db=DEFAULT_DB):
        self.db = db

    def classify(self, entity_type, entity_id, audience, evidence, reviewer, **capabilities):
        if audience not in AUDIENCES.get(entity_type, {}):
            raise ValueError("audience does not belong to this entity type")
        if set(capabilities) - set(CAPABILITIES) or any(
            v not in ("yes", "no", "unknown") for v in capabilities.values()
        ):
            raise ValueError("capabilities must be yes, no or unknown")
        with connect(self.db) as conn:
            profile = _ensure(conn, entity_type, entity_id, evidence, reviewer)
            values = dict(
                audience=audience,
                evidence=required(evidence),
                reviewer=required(reviewer),
                updated_at=utc_now(),
                **capabilities,
            )
            conn.execute(
                "UPDATE relationship_profiles SET "
                + ",".join(k + "=?" for k in values)
                + " WHERE profile_id=?",
                (*values.values(), profile),
            )
        return {"profile_id": profile, "audience": audience}

    def save_contact(
        self,
        entity_type,
        entity_id,
        channel,
        address,
        availability,
        source_url,
        reviewer,
        last_verified_at=None,
        provider=None,
        agent_card_url=None,
        authentication_requirements=None,
        supported_interactions=None,
        supports_dm="unknown",
        agent_operated=None,
        agent_operated_evidence_url=None,
        agent_operated_verified_at=None,
    ):
        if agent_operated is not None:
            if type(agent_operated) is not bool or entity_type != "agent":
                raise ValueError("agent_operated must be a boolean for an agent contact")
            proof = urlsplit(agent_operated_evidence_url or "")
            if proof.scheme != "https" or not proof.hostname or not agent_operated_verified_at:
                raise ValueError(
                    "agent operation evidence needs an HTTPS URL and verification time"
                )
            agent_operated_verified_at = timestamp(agent_operated_verified_at)
        elif agent_operated_evidence_url or agent_operated_verified_at:
            raise ValueError("agent operation evidence needs an explicit boolean")
        if channel not in HUMAN_CHANNELS + AGENT_CHANNELS:
            raise ValueError("channel does not belong to this entity type")
        if supports_dm not in ("yes", "no", "unknown"):
            raise ValueError("supports_dm must be yes, no or unknown")
        if availability not in ("unknown", "available", "unavailable"):
            raise ValueError("invalid availability")
        address = address.strip()
        if channel == "discord" and address:
            route = urlsplit(address)
            parts = route.path.strip("/").split("/")
            if (
                route.scheme != "https"
                or route.netloc != "discord.com"
                or len(parts) != 3
                or parts[0] != "channels"
                or not all(p.isdigit() for p in parts[1:])
                or route.query
                or route.fragment
            ):
                raise ValueError("Discord address must be a public server channel URL")
        when = timestamp(last_verified_at) if last_verified_at else None
        if availability == "available" and (not address or not when):
            raise ValueError("available contacts need an address and verification timestamp")
        required(source_url)
        with connect(self.db) as conn:
            profile = _ensure(conn, entity_type, entity_id, source_url, reviewer)
            legacy_id = None
            if entity_type == "human" and channel in ("email", "x", "linkedin") and address:
                # Existing senders/identity matchers continue to see their canonical contacts.
                existing = conn.execute(
                    "SELECT contact_id FROM contact_points WHERE person_id=? AND contact_type=? AND lower(value)=lower(?)",
                    (entity_id, channel, address),
                ).fetchone()
                legacy_id = (
                    existing[0]
                    if existing
                    else stable_id("rel_cp", entity_id, channel, address.casefold())
                )
                if not existing:
                    conn.execute(
                        "INSERT INTO contact_points(contact_id,person_id,contact_type,value,normalized_value,first_seen_at) VALUES(?,?,?,?,?,?)",
                        (legacy_id, entity_id, channel, address, address.casefold(), utc_now()),
                    )
                if availability == "available":
                    source = conn.execute(
                        "SELECT source_id FROM sources WHERE url=?", (source_url,)
                    ).fetchone()
                    source_id = source[0] if source else stable_id("rel_source", source_url)
                    if not source:
                        conn.execute(
                            "INSERT INTO sources(source_id,url,source_type,quality_tier,accessed_at) VALUES(?,?,'other',3,?)",
                            (source_id, source_url, when),
                        )
                    conn.execute(
                        "UPDATE contact_points SET verification_status='confirmed',verification_method='reviewed_relationship_contact',source_id=?,last_verified_at=? WHERE contact_id=?",
                        (source_id, when, legacy_id),
                    )
            contact_id = stable_id("rel_contact", profile, channel, address)
            conn.execute(
                "INSERT INTO relationship_contacts "
                "(contact_id,profile_id,channel,address,availability,provider,agent_card_url,authentication_requirements,supported_interactions,source_url,last_verified_at,legacy_contact_id,updated_at,supports_dm) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(contact_id) DO UPDATE SET "
                "supports_dm=excluded.supports_dm,availability=excluded.availability,provider=excluded.provider,agent_card_url=excluded.agent_card_url,"
                "authentication_requirements=excluded.authentication_requirements,supported_interactions=excluded.supported_interactions,"
                "source_url=excluded.source_url,last_verified_at=excluded.last_verified_at,updated_at=excluded.updated_at",
                (
                    contact_id,
                    profile,
                    channel,
                    address,
                    availability,
                    provider,
                    agent_card_url,
                    authentication_requirements,
                    supported_interactions,
                    source_url,
                    when,
                    legacy_id,
                    utc_now(),
                    supports_dm,
                ),
            )
            if agent_operated is not None:
                conn.execute(
                    "UPDATE relationship_contacts SET agent_operated=?,agent_operated_evidence_url=?,"
                    "agent_operated_verified_at=? WHERE contact_id=?",
                    (
                        int(agent_operated),
                        agent_operated_evidence_url,
                        agent_operated_verified_at,
                        contact_id,
                    ),
                )
        return {"contact_id": contact_id}

    def record(
        self,
        entity_type,
        entity_id,
        channel,
        kind,
        account_key,
        provider_id,
        observed_at,
        evidence,
        reviewer,
        occurred_at=None,
        target_is_ours=False,
        target_url=None,
        interaction_url=None,
        external_reference=None,
    ):
        if channel not in HUMAN_CHANNELS + AGENT_CHANNELS or kind not in KINDS:
            raise ValueError("unsupported channel or event kind")
        if type(target_is_ours) not in (bool, int) or target_is_ours not in (0, 1):
            raise ValueError("target_is_ours must be boolean")
        observed = timestamp(observed_at)
        occurred = timestamp(occurred_at) if occurred_at else None
        if occurred and occurred > observed:
            raise ValueError("occurrence cannot follow observation")
        if kind in ("like", "comment") and not target_url:
            raise ValueError("likes and comments need the exact target post URL")
        if kind == "outbound" and not occurred:
            raise ValueError("confirmed outbound contact needs its actual send timestamp")
        event_id = stable_id(
            "rel_event", channel, required(account_key), required(provider_id), kind
        )
        with connect(self.db) as conn:
            profile = _ensure(conn, entity_type, entity_id, evidence, reviewer)
            values = dict(
                event_id=event_id,
                profile_id=profile,
                channel=channel,
                kind=kind,
                account_key=account_key,
                provider_id=provider_id,
                occurred_at=occurred,
                observed_at=observed,
                target_is_ours=int(target_is_ours),
                target_url=target_url,
                interaction_url=interaction_url,
                external_reference=external_reference,
                evidence=required(evidence),
                reviewer=required(reviewer),
            )
            prior = conn.execute(
                "SELECT * FROM relationship_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if prior:
                # Private fields are sanitized remotely. Compare only immutable public facts.
                public = set(values) - {"evidence", "external_reference", "reviewer", "observed_at"}
                if any(prior[k] != values[k] for k in public):
                    raise ValueError("conflicting event receipt; original retained")
                return {"event_id": event_id, "created": False}
            conn.execute(
                "INSERT INTO relationship_events ("
                + ",".join(values)
                + ") VALUES("
                + ",".join("?" for _ in values)
                + ")",
                tuple(values.values()),
            )
            # Keep existing human cadence checks aware of these actual interactions.
            if (
                entity_type == "human"
                and channel in ("email", "x", "linkedin")
                and (
                    kind == "outbound" or (kind in ("like", "comment", "reply") and target_is_ours)
                )
            ):
                conn.execute(
                    "INSERT INTO outreach_events(event_id,person_id,channel,direction,occurred_at,outcome,external_reference,notes) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        entity_id,
                        channel,
                        "outbound" if kind == "outbound" else "inbound",
                        occurred or observed,
                        "sent" if kind == "outbound" else "replied",
                        external_reference,
                        json.dumps(
                            {
                                "interaction": kind,
                                "timestamp_basis": "occurred" if occurred else "observed",
                            }
                        ),
                    ),
                )
        return {"event_id": event_id, "created": True}

    def list(self, entity_type=None, entity_id=None, audience=None):
        if entity_type is not None:
            channels(entity_type)
        if entity_id is not None and entity_type is None:
            raise ValueError("entity_id requires entity_type")
        with connect(self.db) as conn:
            if entity_id is not None:
                _identity(conn, entity_type, entity_id)
            people = [dict(r) for r in conn.execute("SELECT * FROM people")]
            agents = [dict(r) for r in conn.execute("SELECT * FROM agents")]
            profiles = {
                r["profile_id"]: dict(r)
                for r in conn.execute("SELECT * FROM relationship_profiles")
            }
            routes = [dict(r) for r in conn.execute("SELECT * FROM relationship_contacts")]
            legacy_routes = [dict(r) for r in conn.execute("SELECT * FROM contact_points")]
            events = [
                dict(
                    r,
                    source="relationship_events",
                    surface=event_surface(dict(r)),
                )
                for r in conn.execute("SELECT * FROM relationship_events")
            ]
            x_events = [dict(r) for r in conn.execute("SELECT * FROM x_engagement_events")]
            outreach = [dict(r) for r in conn.execute("SELECT * FROM outreach_events")]
            tags = [dict(r) for r in conn.execute("SELECT * FROM current_person_tags")]
            companies = {
                r["company_id"]: r["canonical_name"]
                for r in conn.execute("SELECT * FROM companies")
            }
        # Remove watcher compatibility copies before classifying replies. Likes are not replies.
        specialized_ids = {e["event_id"] for e in events + x_events}
        mirrored_ids = {e["event_id"] for e in events}
        for e in x_events:
            if e["event_id"] in mirrored_ids:
                continue
            if e["person_id"] and e["identity_status"] == "matched":
                events.append(
                    dict(
                        event_id=e["event_id"],
                        source="x_engagement_events",
                        surface="public",
                        profile_id="human:" + e["person_id"],
                        channel="x",
                        kind=e["interaction"],
                        account_key=e["account"],
                        provider_id=e["provider_id"],
                        occurred_at=e["occurred_at"],
                        observed_at=e["created_at"],
                        target_is_ours=e["target_status"] == "registered"
                        or _our_x_post(e["target_url"], e["account"]),
                        target_url=e["target_url"],
                        interaction_url=e["interaction_url"],
                        external_reference=e["provider_id"],
                    )
                )
        for e in outreach:
            if not e["person_id"] or e["event_id"] in specialized_ids:
                continue
            kind = (
                "outbound"
                if e["direction"] == "outbound" and e["outcome"] in ("sent", "delivered")
                else None
            )
            if e["direction"] == "inbound" and e["outcome"] in ("replied", "declined", "opted_out"):
                kind = "reply"
            if not kind:
                continue
            # The DM watcher currently records detection time, not the provider send time.
            detected = e["event_id"].startswith(("xdm_in_", "xdm_out_"))
            events.append(
                dict(
                    event_id=e["event_id"],
                    source="outreach_events",
                    surface="dm" if e["event_id"].startswith(("xdm_", "dm_x_")) else "unknown",
                    account_key=e.get("account_key"),
                    provider_id=None,
                    profile_id="human:" + e["person_id"],
                    channel=e["channel"],
                    kind=kind,
                    occurred_at=None if detected else e["occurred_at"],
                    observed_at=e["occurred_at"] if detected else e["created_at"],
                    target_is_ours=True,
                    external_reference=e["external_reference"],
                )
            )
        output = []
        for side, entities, key, name in (
            ("human", people, "person_id", "full_name"),
            ("agent", agents, "agent_id", "canonical_name"),
        ):
            if entity_type and side != entity_type:
                continue
            for entity in entities:
                if entity_id and entity[key] != entity_id:
                    continue
                profile_id = side + ":" + entity[key]
                profile = profiles.get(profile_id, {})
                person_tags = [
                    t["tag_id"] for t in tags if side == "human" and t["person_id"] == entity[key]
                ]
                # Only existing explicit audience assignments can classify older people.
                category = profile.get("audience")
                supported = [t for t in person_tags if t in AUDIENCES[side]]
                if not category and len(supported) == 1:
                    category = supported[0]
                if audience and category != audience:
                    continue
                contacts = [r for r in routes if r["profile_id"] == profile_id]
                overlaid = {r["legacy_contact_id"] for r in contacts if r["legacy_contact_id"]}
                contacts.extend(
                    dict(
                        contact_id=r["contact_id"],
                        channel=r["contact_type"],
                        address=r["value"],
                        availability="unavailable"
                        if r["verification_status"] in ("invalid", "stale")
                        else "unknown",
                        is_primary=r["is_primary"],
                        verification_status=r["verification_status"],
                        last_verified_at=r["last_verified_at"],
                        source_id=r["source_id"],
                    )
                    for r in legacy_routes
                    if side == "human"
                    and r["person_id"] == entity[key]
                    and r["contact_id"] not in overlaid
                )
                history = [e for e in events if e["profile_id"] == profile_id]
                for event in history:
                    event["direction"] = (
                        "outbound"
                        if event["kind"] == "outbound"
                        else "inbound"
                        if event["kind"] in ("like", "comment", "reply")
                        else None
                    )
                company = (
                    entity.get("primary_company_id")
                    if side == "human"
                    else entity.get("company_id")
                )
                output.append(
                    dict(
                        entity_type=side,
                        entity_id=entity[key],
                        name=entity[name],
                        audience=category,
                        company_id=company,
                        company_name=companies.get(company),
                        **contact_columns(contacts),
                        current_role=entity.get("current_role"),
                        identity_status=entity.get("identity_status"),
                        research_status=entity["research_status"],
                        confidence=entity["confidence"],
                        founder_status=entity.get("founder_status"),
                        created_at=entity["created_at"],
                        updated_at=entity["updated_at"],
                        legacy_tags=person_tags,
                        owner_person_id=profile.get("owner_person_id"),
                        **{k: profile.get(k, "unknown") for k in CAPABILITIES},
                        channels_to_check=list(channels(side, category)),
                        contact_points=contacts,
                        contact_channels=sorted({r["channel"] for r in contacts if r["address"]}),
                        available_contact_channels=sorted(
                            {r["channel"] for r in contacts if r["availability"] == "available"}
                        ),
                        **summarize(history),
                    )
                )
        return sorted(
            output, key=lambda r: (r["entity_type"], r["name"].casefold(), r["entity_id"])
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DB)
    parser.add_argument("--type", choices=list(AUDIENCES))
    parser.add_argument("--id")
    parser.add_argument("--audience")
    parser.add_argument("--catalog", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            catalog()
            if args.catalog
            else Relationships(args.database).list(args.type, args.id, args.audience),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
