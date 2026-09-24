"""Prepare Agent DM work and normalize confirmed provider receipts.

This module has no provider client and performs no CRM or PostHog writes. It is
the deterministic boundary around the contextual Portkey step: callers supply
the reviewed draft, and this code decides whether a verified agent-operated
route can be used. Confirmed receipts produce two separate handoffs for the CRM
task. Their requests and receipts remain distinct.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from urllib.parse import urlsplit

from crm.cli import stable_id
from crm.relationships import AGENT_CHANNELS

POSITIVE_REACTIONS = {
    "+1",
    "celebrate",
    "clap",
    "fire",
    "heart",
    "hooray",
    "like",
    "love",
    "rocket",
    "thumbs_up",
    "upvote",
}
ROUTE_ORDER = (
    "agent_email",
    "masumi",
    "a2a",
    "agentdm",
    "agentlist",
    "agents_breakroom",
    "moltbook",
)


def _required(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty")
    return value.strip()


def _time(value, field):
    try:
        parsed = datetime.fromisoformat(_required(value, field).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be an ISO timestamp") from error
    if parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _https(value):
    try:
        parsed = urlsplit(value)
    except (TypeError, ValueError):
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def _tokens(value):
    if isinstance(value, list):
        return {str(item).strip().casefold() for item in value if str(item).strip()}
    if not isinstance(value, str):
        return set()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, list):
        return _tokens(decoded)
    return {
        token.strip().casefold() for token in value.replace(";", ",").split(",") if token.strip()
    }


def _resource(resource):
    if not resource:
        return None
    result = {
        "title": _required(resource.get("title"), "resource.title"),
        "version": _required(resource.get("version"), "resource.version"),
        "sha256": _required(resource.get("sha256"), "resource.sha256").casefold(),
        "url": resource.get("url") or None,
        "attachment_name": resource.get("attachment_name") or None,
    }
    if len(result["sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in result["sha256"]
    ):
        raise ValueError("resource.sha256 must be a SHA-256 hex digest")
    if result["url"] and not _https(result["url"]):
        raise ValueError("resource.url must be HTTPS")
    if result["attachment_name"] and not result["attachment_name"].endswith(".md"):
        raise ValueError("resource attachment must be a .md file")
    return result


def _verified(contact):
    try:
        verified_at = _time(contact.get("last_verified_at"), "contact.last_verified_at")
        agent_operated_verified_at = _time(
            contact.get("agent_operated_verified_at"),
            "contact.agent_operated_verified_at",
        )
    except ValueError:
        return False
    basic = bool(
        contact.get("channel") in AGENT_CHANNELS
        and contact.get("agent_operated") is True
        and _https(contact.get("agent_operated_evidence_url"))
        and agent_operated_verified_at
        and contact.get("availability") == "available"
        and str(contact.get("address") or "").strip()
        and _https(contact.get("source_url"))
        and verified_at
    )
    if contact.get("channel") == "a2a":
        return bool(
            basic
            and _https(contact.get("agent_card_url"))
            and str(contact.get("authentication_requirements") or "").strip()
        )
    return basic


def _suppression_matches(suppression, contact):
    """Ops exports active entries; absent/unknown scope must never permit contact."""
    if not isinstance(suppression, dict):
        return True
    if "contact_id" not in suppression or "channel" not in suppression:
        return True
    contact_id, channel = suppression["contact_id"], suppression["channel"]
    if any(value is not None and not isinstance(value, str) for value in (contact_id, channel)):
        return True
    if channel and channel != contact.get("channel"):
        return False
    if not contact_id:
        return True
    storage = suppression.get("storage")
    if storage == "suppressions":
        route_id = (
            contact.get("legacy_contact_id")
            if contact.get("profile_id")
            else contact.get("contact_id")
        )
    elif storage == "relationship_contact_suppressions":
        route_id = contact.get("contact_id") if contact.get("profile_id") else None
    else:
        return True  # Unknown contact namespace cannot establish a safe route.
    return contact_id == route_id


def _route_suppressed(candidate, contact):
    return (
        contact.get("suppressed") is not False
        or not isinstance(contact.get("active_suppressions"), list)
        or bool(contact["active_suppressions"])
        or any(_suppression_matches(item, contact) for item in candidate["active_suppressions"])
    )


def _route(candidate, allowed_channels=None, allowed_providers=None):
    contacts = [
        contact
        for contact in candidate.get("contacts", [])
        if _verified(contact)
        and not _route_suppressed(candidate, contact)
        and (allowed_channels is None or contact["channel"] in allowed_channels)
        and (allowed_providers is None or contact.get("provider") in allowed_providers)
    ]
    reaction_channel = (candidate.get("reaction") or {}).get("channel")
    native = [
        contact
        for contact in contacts
        if contact["channel"] == reaction_channel and contact.get("supports_dm") == "yes"
    ]
    if native:
        return sorted(native, key=lambda item: item.get("contact_id") or item["address"])[0]
    for channel in ROUTE_ORDER:
        matches = [
            contact
            for contact in contacts
            if contact["channel"] == channel
            and (channel == "agent_email" or contact.get("supports_dm") != "no")
        ]
        if matches:
            return sorted(matches, key=lambda item: item.get("contact_id") or item["address"])[0]
    return None


def _unanswered(history):
    ordered = []
    for event in history or []:
        if event.get("kind") not in ("outbound", "reply"):
            continue
        value = event.get("occurred_at") or event.get("observed_at")
        if value:
            ordered.append((_time(value, "history timestamp"), event["kind"]))
    ordered.sort()
    if not ordered:
        return False
    last_outbound = max((at for at, kind in ordered if kind == "outbound"), default=None)
    last_reply = max((at for at, kind in ordered if kind == "reply"), default=None)
    return bool(last_outbound and (not last_reply or last_reply <= last_outbound))


def _delivery_form(route, resource, requested):
    if not requested or not resource:
        return "none"
    supported = _tokens(route.get("supported_interactions"))
    if resource.get("url"):
        return "https_link"
    if (
        route["channel"] == "agent_email"
        and resource.get("attachment_name")
        and ("attachment" in supported or "attachments" in supported)
    ):
        return "attachment"
    return "none"


def _base_candidate(candidate):
    kind = candidate.get("kind") or "reaction"
    if kind in ("cold_intro", "follow_up"):
        trigger = candidate.get("trigger") or {}
        agent_id = _required(candidate.get("target_agent_id"), "target_agent_id")
        source_id = _required(trigger.get("source_id"), "trigger.source_id")
        source_url = _required(trigger.get("source_url"), "trigger.source_url")
        if not _https(source_url):
            raise ValueError("trigger.source_url must be HTTPS")
        result = {
            "candidate_id": stable_id("agent_dm_candidate", kind, agent_id, source_id),
            "target_agent_id": agent_id,
            "target_actor_reference": candidate.get("target_actor_reference") or None,
            "kind": kind,
            "trigger": {
                "source_id": source_id,
                "source_url": source_url,
                "sender_account": _required(
                    trigger.get("sender_account"), "trigger.sender_account"
                ),
                "occurred_at": _time(trigger.get("occurred_at"), "trigger.occurred_at"),
                "evidence": _required(trigger.get("evidence"), "trigger.evidence"),
            },
        }
        if kind == "follow_up":
            result["trigger"]["inbound_message_id"] = _required(
                trigger.get("inbound_message_id"), "trigger.inbound_message_id"
            )
            result["trigger"]["inbound_thread_id"] = _required(
                trigger.get("inbound_thread_id"), "trigger.inbound_thread_id"
            )
        return result
    if kind != "reaction":
        raise ValueError("unsupported Agent DM candidate kind")
    reaction = candidate.get("reaction") or {}
    reaction_id = _required(reaction.get("reaction_id"), "reaction.reaction_id")
    channel = _required(reaction.get("channel"), "reaction.channel")
    sender = _required(reaction.get("sender_account"), "reaction.sender_account")
    return {
        "candidate_id": stable_id("agent_dm_candidate", channel, sender, reaction_id),
        "kind": "reaction",
        "target_agent_id": candidate.get("target_agent_id") or None,
        "target_actor_reference": candidate.get("target_actor_reference") or None,
        "reaction": {
            "reaction_id": reaction_id,
            "reaction_type": _required(
                reaction.get("reaction_type"), "reaction.reaction_type"
            ).casefold(),
            "channel": channel,
            "sender_account": sender,
            "target_message_id": _required(
                reaction.get("target_message_id"), "reaction.target_message_id"
            ),
            "target_url": _required(reaction.get("target_url"), "reaction.target_url"),
            "occurred_at": _time(reaction.get("occurred_at"), "reaction.occurred_at"),
            "evidence": _required(reaction.get("evidence"), "reaction.evidence"),
        },
    }


def _draft(candidate):
    draft = candidate.get("draft") or {}
    required = ("query", "body", "critique", "reviewer")
    if any(not str(draft.get(field) or "").strip() for field in required):
        return None
    if draft.get("model_route") != "portkey" or draft.get("revised") is not True:
        return None
    return {
        "subject": str(draft.get("subject") or "").strip() or None,
        "query": draft["query"].strip(),
        "body": draft["body"].strip(),
        "critique": draft["critique"].strip(),
        "reviewer": draft["reviewer"].strip(),
        "model_route": "portkey",
        "revised": True,
    }


def _hold(result, reason):
    return {**result, "outcome": "held", "reason": reason}


def evaluate(candidate, resource=None, as_of=None, allowed_channels=None, allowed_providers=None):
    """Return one deterministic prepared, held or suppressed candidate outcome."""
    result = _base_candidate(candidate)
    kind = result["kind"]
    reaction = result.get("reaction")
    if kind == "reaction":
        if reaction["reaction_type"] not in POSITIVE_REACTIONS:
            return {**result, "outcome": "suppressed", "reason": "non_positive_reaction"}
        if not candidate.get("target_is_ours"):
            return _hold(result, "reaction_target_not_verified_as_ours")
    if not result["target_agent_id"]:
        return _hold(result, "agent_identity_unresolved")
    if (
        candidate.get("history_truncated") is not False
        or not isinstance(candidate.get("history"), list)
        or any(not isinstance(event, dict) for event in candidate["history"])
    ):
        return _hold(result, "agent_history_incomplete")
    if candidate.get("suppression_context_complete") is not True or not isinstance(
        candidate.get("active_suppressions"), list
    ):
        return _hold(result, "suppression_context_incomplete")
    profile = candidate.get("profile") or {}
    owner_policy = profile.get("owner_approval_required", "unknown")
    if owner_policy == "unknown":
        return _hold(result, "owner_approval_status_unknown")
    if owner_policy == "yes" and (
        candidate.get("owner_approval_status") != "approved"
        or not candidate.get("owner_approval_evidence")
    ):
        return _hold(result, "owner_approval_required")
    history = candidate.get("history") or []
    try:
        for event in history:
            if event.get("kind") in ("outbound", "reply"):
                _time(event.get("occurred_at") or event.get("observed_at"), "history timestamp")
    except ValueError:
        return _hold(result, "agent_history_incomplete")
    if _unanswered(history):
        return _hold(result, "prior_outbound_unanswered")
    if kind in ("reaction", "cold_intro") and any(
        event.get("kind") == "outbound" for event in history
    ):
        return _hold(result, "prior_outbound_requires_reply_linked_follow_up")
    if kind == "follow_up":
        replies = [event for event in history if event.get("kind") == "reply"]
        if not replies:
            return _hold(result, "inbound_reply_not_in_agent_history")
        latest_reply = max(
            replies,
            key=lambda event: _time(
                event.get("occurred_at") or event.get("observed_at"),
                "history reply timestamp",
            ),
        )
        if (
            latest_reply.get("provider_id") != result["trigger"]["inbound_message_id"]
            or _time(
                latest_reply.get("occurred_at") or latest_reply.get("observed_at"),
                "history reply timestamp",
            )
            != result["trigger"]["occurred_at"]
        ):
            return _hold(result, "inbound_reply_is_not_latest_agent_reply")
        inbound_text = str(candidate.get("inbound_text") or "")
        if not inbound_text.strip():
            return _hold(result, "inbound_reply_text_missing")
        result["inbound_text_sha256"] = hashlib.sha256(inbound_text.encode()).hexdigest()
    if candidate.get("next_eligible_at"):
        if as_of is None:
            raise ValueError("as_of is required with next_eligible_at")
        if _time(candidate["next_eligible_at"], "next_eligible_at") > as_of:
            return _hold(result, "provider_cooldown_active")
    route = _route(candidate, allowed_channels, allowed_providers)
    if route is None:
        verified = [
            contact
            for contact in candidate.get("contacts", [])
            if _verified(contact)
            and (allowed_channels is None or contact["channel"] in allowed_channels)
            and (allowed_providers is None or contact.get("provider") in allowed_providers)
        ]
        if verified and all(_route_suppressed(candidate, contact) for contact in verified):
            complete = all(
                type(contact.get("suppressed")) is bool
                and isinstance(contact.get("active_suppressions"), list)
                for contact in verified
            )
            if not complete:
                return _hold(result, "suppression_context_incomplete")
            return {**result, "outcome": "suppressed", "reason": "active_suppression"}
        return _hold(result, "verified_agent_route_missing")
    if kind == "follow_up" and (
        route.get("channel") != "agent_email"
        or route.get("address", "").casefold()
        != parseaddr(str(candidate.get("inbound_from") or ""))[1].casefold()
    ):
        return _hold(result, "follow_up_route_does_not_match_inbound_sender")
    draft = _draft(candidate)
    if draft is None:
        return _hold(result, "reviewed_portkey_draft_missing")
    form = _delivery_form(route, resource, candidate.get("expose_resource") is True)
    return {
        **result,
        "outcome": "prepared",
        "reason": f"verified_{kind}_candidate",
        "approval": {
            "owner_approval_required": owner_policy,
            "status": candidate.get("owner_approval_status"),
            "evidence": candidate.get("owner_approval_evidence"),
        },
        "route": {
            **{
                key: route.get(key)
                for key in (
                    "contact_id",
                    "channel",
                    "address",
                    "provider",
                    "source_url",
                    "last_verified_at",
                    "agent_operated",
                    "agent_operated_evidence_url",
                    "agent_operated_verified_at",
                    "agent_card_url",
                    "authentication_requirements",
                )
            },
            "transport_provider": "agentmail" if route["channel"] == "agent_email" else None,
        },
        "draft": draft,
        "resource": (
            {
                "title": resource["title"],
                "version": resource["version"],
                "sha256": resource["sha256"],
                "url": resource.get("url"),
                "attachment_name": resource.get("attachment_name"),
                "delivery_form": form,
            }
            if resource and form != "none"
            else {"delivery_form": "none"}
        ),
        "provider_action": "not_executed",
    }


def plan(snapshot):
    """Prepare a complete reaction-candidate manifest without external writes."""
    generated_at = _time(snapshot.get("generated_at"), "generated_at")
    resource = _resource(snapshot.get("resource"))
    outcomes = []
    seen = set()
    prepared_agents = {}
    for candidate in snapshot.get("candidates") or []:
        outcome = evaluate(
            candidate,
            resource,
            generated_at,
            snapshot.get("approved_channels"),
            snapshot.get("approved_providers"),
        )
        key = outcome["candidate_id"]
        if key in seen:
            continue
        seen.add(key)
        agent_id = outcome.get("target_agent_id")
        if outcome["outcome"] == "prepared" and agent_id in prepared_agents:
            outcome = {
                **outcome,
                "outcome": "held",
                "reason": "another_reaction_candidate_prepared_for_agent",
                "related_candidate_id": prepared_agents[agent_id],
            }
            outcome.pop("route")
            outcome.pop("draft")
            outcome.pop("resource")
            outcome.pop("provider_action")
        elif outcome["outcome"] == "prepared":
            prepared_agents[agent_id] = outcome["candidate_id"]
        outcomes.append(outcome)
    run_identity = json.dumps(
        [generated_at, [item["candidate_id"] for item in outcomes]], separators=(",", ":")
    )
    run_id = stable_id("agent_dm_run", run_identity)
    counts = {
        state: sum(item["outcome"] == state for item in outcomes)
        for state in ("prepared", "held", "suppressed")
    }
    return {
        "schema_version": 1,
        "run_id": run_id,
        "generated_at": generated_at,
        "mode": "prepare_only",
        "provider_writes": False,
        "candidates": outcomes,
        "summary": {"candidate_total": len(outcomes), **counts},
        "crm_request": {
            "request_id": stable_id("crm_request", run_id),
            "source_agent": "agent dm agent",
            "destination_agent": "ops agents",
            "status": "awaiting_confirmed_provider_receipts",
            "operations": [],
        },
        "posthog_request": {
            "request_id": stable_id("posthog_request", run_id),
            "source_agent": "agent dm agent",
            "destination_agent": "ops agents",
            "status": "awaiting_confirmed_provider_receipts",
            "events": [],
        },
    }


def confirmed_handoffs(prepared, receipt):
    """Normalize one exact provider confirmation into separate agent requests."""
    if prepared.get("outcome") != "prepared":
        raise ValueError("only a prepared candidate can accept a receipt")
    route = prepared["route"]
    if receipt.get("status") != "confirmed":
        raise ValueError("confirmed provider receipt required")
    if receipt.get("channel") != route["channel"]:
        raise ValueError("receipt channel does not match prepared route")
    if str(receipt.get("recipient") or "").casefold() != route["address"].casefold():
        raise ValueError("receipt recipient does not match prepared route")
    provider_id = _required(receipt.get("provider_id"), "receipt.provider_id")
    sent_at = _time(receipt.get("sent_at"), "receipt.sent_at")
    observed_at = _time(receipt.get("observed_at"), "receipt.observed_at")
    if sent_at > observed_at:
        raise ValueError("provider send time cannot follow observation time")
    provider = _required(receipt.get("provider"), "receipt.provider")
    if route.get("transport_provider") and provider != route["transport_provider"]:
        raise ValueError("receipt provider does not match prepared route")
    evidence = _required(receipt.get("evidence"), "receipt.evidence")
    if receipt.get("message_url") and not _https(receipt["message_url"]):
        raise ValueError("receipt.message_url must be HTTPS")
    kind = prepared.get("kind") or "reaction"
    reaction = prepared.get("reaction") or {}
    trigger = prepared.get("trigger") or {}
    sender_account = reaction.get("sender_account") or trigger.get("sender_account")
    source_url = reaction.get("target_url") or trigger.get("source_url")
    source_id = reaction.get("reaction_id") or trigger.get("source_id")
    resource = prepared.get("resource") or {"delivery_form": "none"}
    event_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            "|".join(
                (
                    "gtm.agent_dm_sent",
                    provider,
                    route["channel"],
                    sender_account,
                    provider_id,
                )
            ),
        )
    )
    receipt_link = receipt.get("message_url") or provider_id
    crm = {
        "handoff_id": stable_id("agent_dm_crm_handoff", event_uuid),
        "request_id": stable_id("crm_request", event_uuid),
        "idempotency_key": event_uuid,
        "source_agent": "agent dm agent",
        "destination_agent": "ops agents",
        "status": "prepared_not_applied",
        "operations": [
            {
                "action": "event_record",
                "payload": {
                    "target_agent_id": prepared["target_agent_id"],
                    "channel": route["channel"],
                    "message_kind": "outbound",
                    "provider_status": "confirmed",
                    "sender_account": sender_account,
                    "provider_message_id": provider_id,
                    "occurred_at": sent_at,
                    "observed_at": observed_at,
                    "permalink": receipt.get("message_url"),
                    "parent_url": source_url,
                    "conversation_id": receipt.get("thread_id") or receipt_link,
                    "reacted_to_provider_message_id": reaction.get("target_message_id"),
                    "resource_version": resource.get("version"),
                    "evidence": f"{evidence} | {kind}_source_id={source_id}",
                    "reviewer": "agent dm agent",
                },
                "metadata": {
                    "candidate_id": prepared["candidate_id"],
                    "candidate_kind": kind,
                    "source_id": source_id,
                    "route_contact_id": route.get("contact_id"),
                    "sender": receipt.get("sender"),
                    "recipient": receipt.get("recipient"),
                    "text": receipt.get("text"),
                    "thread_id": receipt.get("thread_id"),
                    "agent_operated_evidence_url": route.get("agent_operated_evidence_url"),
                    "owner_approval_required": (prepared.get("approval") or {}).get(
                        "owner_approval_required"
                    ),
                    "owner_approval_evidence": (prepared.get("approval") or {}).get("evidence"),
                    "resource_version": resource.get("version"),
                    "resource_delivery_form": resource["delivery_form"],
                },
            }
        ],
    }
    properties = {
        "distinct_id": "gtm-agent-dm-operator",
        "$process_person_profile": False,
        "$geoip_disable": True,
        "schema_version": 1,
        "source_event_id": event_uuid,
        "candidate_id": prepared["candidate_id"],
        "target_agent_id": prepared["target_agent_id"],
        "platform": route["channel"],
        "provider": provider,
        "sender_account": sender_account,
        "entry_type": "dm",
        "reply_surface": "private_dm",
        "program": "gtm_agent_outreach",
        "candidate_kind": kind,
        "reaction_channel": reaction.get("channel"),
        "reaction_type": reaction.get("reaction_type"),
        "resource_version": resource.get("version"),
        "resource_delivery_form": resource["delivery_form"],
    }
    posthog = {
        "schema_version": 1,
        "run_id": stable_id("agent_dm_posthog_run", event_uuid),
        "request_id": stable_id("posthog_request", event_uuid),
        "idempotency_key": event_uuid,
        "source_agent": "agent dm agent",
        "destination_agent": "ops agents",
        "status": "prepared_not_submitted",
        "events": [
            {
                "event": "gtm.agent_dm_sent",
                "uuid": event_uuid,
                "timestamp": sent_at,
                "properties": properties,
            }
        ],
    }
    return {"crm_request": crm, "posthog_request": posthog}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("plan", help="prepare a no-write Agent DM manifest")
    prepare.add_argument("snapshot", type=Path)
    prepare.add_argument("--draft", type=Path)
    prepare.add_argument("--output", type=Path, required=True)
    receipt = commands.add_parser(
        "receipt", help="normalize one confirmed provider receipt into two requests"
    )
    receipt.add_argument("prepared", type=Path)
    receipt.add_argument("provider_receipt", type=Path)
    receipt.add_argument("--candidate-id", required=True)
    receipt.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        snapshot = json.loads(args.snapshot.read_text())
        if args.draft:
            supplied = json.loads(args.draft.read_text())
            candidate_id = supplied.get("candidate_id")
            snapshot = copy.deepcopy(snapshot)
            matches = [
                item
                for item in snapshot.get("candidates", [])
                if _base_candidate(item)["candidate_id"] == candidate_id
            ]
            if len(matches) != 1:
                raise ValueError("draft candidate ID does not match snapshot")
            matches[0]["draft"] = supplied["draft"]
        result = plan(snapshot)
        summary = result["summary"]
    else:
        manifest = json.loads(args.prepared.read_text())
        matches = [
            item
            for item in manifest.get("candidates", [])
            if item.get("candidate_id") == args.candidate_id
        ]
        if len(matches) != 1:
            raise SystemExit("candidate-id must identify one prepared candidate")
        result = confirmed_handoffs(matches[0], json.loads(args.provider_receipt.read_text()))
        summary = {
            "crm_request": result["crm_request"]["request_id"],
            "posthog_request": result["posthog_request"]["request_id"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
