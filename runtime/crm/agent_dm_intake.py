"""Join a frozen reaction export to a bounded Ops Agents CRM snapshot."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from crm.agent_dm import _https, _required, _time


def _contacts(record, attestations):
    contacts = [
        dict(item) for item in (record.get("contact_points") or record.get("contacts") or [])
    ]
    by_contact = {item.get("contact_id"): item for item in contacts if item.get("contact_id")}
    for proof in attestations or []:
        contact_id = _required(proof.get("contact_id"), "attestation.contact_id")
        address = _required(proof.get("address"), "attestation.address")
        evidence_url = _required(proof.get("evidence_url"), "attestation.evidence_url")
        if not _https(evidence_url):
            raise ValueError("agent-operated evidence URL must be HTTPS")
        verified_at = _time(proof.get("verified_at"), "attestation.verified_at")
        contact = by_contact.get(contact_id)
        if not contact or str(contact.get("address") or "").casefold() != address.casefold():
            raise ValueError("agent-operated attestation does not match CRM contact")
        if proof.get("agent_operated") is not True:
            raise ValueError("agent-operated attestation must explicitly be true")
        contact.update(
            agent_operated=True,
            agent_operated_evidence_url=evidence_url,
            agent_operated_verified_at=verified_at,
        )
    return contacts


def _safety_context(record):
    # Absence is unknown, never an empty/complete history or suppression list.
    return {
        key: record.get(key)
        for key in ("history_truncated", "suppression_context_complete", "active_suppressions")
    }


def assemble(reactions, agents, resource, *, outreach=None, generated_at=None):
    """Keep unresolved actors as candidates; never infer an owner as the agent."""
    if agents.get("entity_type") not in (None, "agent"):
        raise ValueError("agent CRM snapshot required")
    records = agents.get("records") or []
    by_id = {
        item.get("agent_id") or item.get("entity_id"): item
        for item in records
        if item.get("agent_id") or item.get("entity_id")
    }
    if len(by_id) != len(records):
        raise ValueError("CRM snapshot contains duplicate or missing agent IDs")
    candidates = []
    seen = set()
    for entry in reactions.get("reactions") or []:
        reaction = entry.get("reaction") or entry
        channel = _required(reaction.get("channel"), "reaction.channel")
        sender = _required(reaction.get("sender_account"), "reaction.sender_account")
        reaction_id = _required(reaction.get("reaction_id"), "reaction.reaction_id")
        identity = (channel, sender, reaction_id)
        if identity in seen:
            continue
        seen.add(identity)
        if entry.get("target_audience") != "agent":
            raise ValueError("reaction must link to an agent-facing reply")
        agent_id = entry.get("target_agent_id")
        record = by_id.get(agent_id) if agent_id else None
        if agent_id and record is None:
            raise ValueError("resolved target agent missing from bounded CRM snapshot")
        contacts = _contacts(record, entry.get("agent_route_attestations")) if record else []
        history = record.get("contact_history", record.get("history")) if record else None
        candidates.append(
            {
                "target_agent_id": agent_id,
                "target_actor_reference": entry.get("target_actor_reference"),
                "target_is_ours": entry.get("target_is_ours") is True,
                "reaction": {
                    key: reaction.get(key)
                    for key in (
                        "reaction_id",
                        "reaction_type",
                        "channel",
                        "sender_account",
                        "target_message_id",
                        "target_url",
                        "occurred_at",
                        "evidence",
                    )
                },
                "agent_name": (record or {}).get("name") or (record or {}).get("canonical_name"),
                "agent_description": (record or {}).get("description"),
                "agent_capabilities": {
                    key: (record or {}).get(key)
                    for key in (
                        "can_receive_requests",
                        "can_call_external_apis",
                        "can_connect_mcp",
                        "can_install_integrations",
                    )
                },
                "our_reply_text": entry.get("our_reply_text"),
                "profile": {
                    "owner_approval_required": (record or {}).get(
                        "owner_approval_required", "unknown"
                    )
                },
                "owner_approval_status": entry.get("owner_approval_status"),
                "owner_approval_evidence": entry.get("owner_approval_evidence"),
                "contacts": contacts,
                "history": history,
                **_safety_context(record or {}),
                "next_eligible_at": entry.get("next_eligible_at"),
                "expose_resource": entry.get("expose_resource") is True,
            }
        )
    for entry in (outreach or {}).get("candidates") or []:
        kind = entry.get("kind")
        if kind not in ("cold_intro", "follow_up"):
            raise ValueError("outreach candidate kind must be cold_intro or follow_up")
        if entry.get("target_audience") != "agent":
            raise ValueError("outreach candidate must target an agent")
        agent_id = _required(entry.get("target_agent_id"), "target_agent_id")
        record = by_id.get(agent_id)
        if record is None:
            raise ValueError("outreach agent missing from bounded CRM snapshot")
        source = entry.get("source") or {}
        trigger = {
            "source_id": source.get("source_id"),
            "source_url": source.get("source_url"),
            "sender_account": entry.get("sender_account"),
            "occurred_at": source.get("occurred_at"),
            "evidence": source.get("evidence"),
        }
        inbound = entry.get("inbound") or {}
        if kind == "follow_up":
            trigger["inbound_message_id"] = inbound.get("message_id")
            trigger["inbound_thread_id"] = inbound.get("thread_id")
        candidates.append(
            {
                "kind": kind,
                "target_agent_id": agent_id,
                "target_actor_reference": entry.get("target_actor_reference"),
                "trigger": trigger,
                "agent_name": record.get("name") or record.get("canonical_name"),
                "agent_description": record.get("description"),
                "agent_capabilities": {
                    key: record.get(key)
                    for key in (
                        "can_receive_requests",
                        "can_call_external_apis",
                        "can_connect_mcp",
                        "can_install_integrations",
                    )
                },
                "profile": {
                    "owner_approval_required": record.get("owner_approval_required", "unknown")
                },
                "owner_approval_status": entry.get("owner_approval_status"),
                "owner_approval_evidence": entry.get("owner_approval_evidence"),
                "contacts": _contacts(record, entry.get("agent_route_attestations")),
                "history": record.get("contact_history", record.get("history")),
                **_safety_context(record),
                "next_eligible_at": entry.get("next_eligible_at"),
                "expose_resource": entry.get("expose_resource") is True,
                "inbound_from": inbound.get("from"),
                "inbound_text": inbound.get("text"),
            }
        )
    captured = generated_at or datetime.now(timezone.utc).isoformat()
    return {
        "generated_at": _time(captured, "generated_at"),
        "resource": resource,
        "approved_channels": ["agent_email"],
        "candidates": candidates,
        "source": {
            "reaction_export_id": reactions.get("export_id"),
            "outreach_export_id": (outreach or {}).get("export_id"),
            "crm_snapshot_request_id": agents.get("request_id"),
        },
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reactions", type=Path)
    parser.add_argument("--agents", type=Path, required=True)
    parser.add_argument("--outreach", type=Path)
    parser.add_argument("--resource", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = assemble(
        json.loads(args.reactions.read_text()) if args.reactions else {"reactions": []},
        json.loads(args.agents.read_text()),
        json.loads(args.resource.read_text()),
        outreach=json.loads(args.outreach.read_text()) if args.outreach else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"candidate_total": len(result["candidates"])}))


if __name__ == "__main__":
    main()
