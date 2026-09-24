"""Build one private X evidence blob and publish only its aggregate snapshot.

The blob intentionally contains private message bodies and stays under ``runtime/data``.
The PostHog event produced from it contains counts and coverage metadata only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from crm.activity import DEFAULT_DB, Ledger, PostHog, encode
from crm.cli import ROOT
from crm.posthog_export import timestamp

DEFAULT_REPLY_DB = ROOT / "data/reply-watcher.sqlite3"
DEFAULT_OVERLAYS = ROOT / "data/x-dm-live-observations.json"
DEFAULT_OUTPUT = ROOT / "data/x-gtm-latest.json"


def _read_private(path: Path, root: Path = ROOT):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("existing private evidence file inside CRM required")
    return json.loads(path.read_text()), path


def _iso(value: str) -> str:
    return timestamp(value).isoformat().replace("+00:00", "Z")


def _status_id(url: str) -> str:
    marker = "/status/"
    if marker not in url:
        raise ValueError("X status URL required")
    return url.split(marker, 1)[1].split("/", 1)[0].split("?", 1)[0]


def _engagements(path: Path, account: str, root: Path = ROOT):
    path = Path(path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("existing private reply ledger inside CRM required")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT event_id,account,provider_id,interaction,actor_handle,target_url,
                      interaction_url,occurred_at,facts,person_id,identity_status,
                      target_status,shared_state
               FROM x_notification_events WHERE account=?
               ORDER BY occurred_at DESC,provider_id DESC""",
            (account,),
        ).fetchall()
    return [dict(row) | {"facts": json.loads(row["facts"])} for row in rows], path


def _record(record_id: str, area: str, direction: str, occurred_at: str, **values):
    return {
        "record_id": record_id,
        "area": area,
        "channel": "x",
        "direction": direction,
        "occurred_at": _iso(occurred_at),
        **values,
    }


def build_blob(
    reply_pairs_path: Path,
    reply_db_path: Path,
    dm_path: Path,
    overlay_path: Path | None = DEFAULT_OVERLAYS,
    *,
    root: Path = ROOT,
):
    pairs, pairs_path = _read_private(reply_pairs_path, root)
    dms, dms_path = _read_private(dm_path, root)
    account = pairs.get("account")
    if not account or dms.get("account") != account:
        raise ValueError("X account mismatch across evidence sources")
    engagements, reply_db_path = _engagements(reply_db_path, account, root)

    overlays = {"schema_version": 1, "account": account, "observations": []}
    resolved_overlay_path = None
    if overlay_path and Path(overlay_path).exists():
        overlays, resolved_overlay_path = _read_private(overlay_path, root)
        if overlays.get("schema_version") != 1 or overlays.get("account") != account:
            raise ValueError("unsupported or mismatched live observation overlay")

    conversations = dms.get("conversations")
    authored = pairs.get("pairs")
    if not isinstance(conversations, list) or not isinstance(authored, list):
        raise ValueError("conversation and authored-reply arrays required")
    conversation_ids = {item["conversation_id"] for item in conversations}
    observations = overlays.get("observations", [])
    if not isinstance(observations, list):
        raise ValueError("live observations array required")

    records = []
    for item in authored:
        reply_url = item["reply_url"]
        records.append(
            _record(
                "reply:outbound:" + _status_id(reply_url),
                "replies",
                "outbound",
                item["posted_at"],
                kind="public_reply",
                account=account,
                provider_id=_status_id(reply_url),
                body=item["reply_text"],
                url=reply_url,
                parent_author=item["parent_author"],
                parent_url=item["parent_url"],
                provider_id_status="confirmed",
            )
        )

    for item in engagements:
        if item["interaction"] != "reply":
            continue
        facts = item["facts"]
        records.append(
            _record(
                "reply:inbound:" + item["provider_id"],
                "replies",
                "inbound",
                item["occurred_at"],
                kind="public_reply",
                account=account,
                provider_id=item["provider_id"],
                body=facts.get("body"),
                actor_handle=item["actor_handle"],
                url=item["interaction_url"],
                in_reply_to_url=item["target_url"],
                identity_status=item["identity_status"],
                provider_id_status="confirmed",
            )
        )

    replied_conversations = set()
    for conversation in conversations:
        outbound = conversation["outbound"]
        conversation_id = conversation["conversation_id"]
        reply = conversation.get("reply")
        common = {
            "account": account,
            "conversation_id": conversation_id,
            "conversation_url": conversation["conversation_url"],
            "contact_name": conversation["name"],
            "contact_handle": conversation["handle"],
        }
        records.append(
            _record(
                "dm:outbound:" + outbound["message_id"],
                "dms",
                "outbound",
                outbound["occurred_at"],
                kind="dm",
                provider_id=outbound["message_id"],
                provider_id_status="confirmed",
                body=outbound["body"],
                body_status="captured",
                **common,
            )
        )
        non_outbound_ids = {outbound["message_id"]}
        if reply:
            non_outbound_ids.add(reply["message_id"])
        for message_id in conversation["all_message_ids"]:
            if message_id in non_outbound_ids:
                continue
            records.append(
                _record(
                    "dm:outbound:" + message_id,
                    "dms",
                    "outbound",
                    outbound["occurred_at"],
                    kind="dm",
                    provider_id=message_id,
                    provider_id_status="confirmed",
                    body=None,
                    body_status="not_present_in_reconciliation_source",
                    timestamp_source="conversation-level minute",
                    **common,
                )
            )
        if reply:
            replied_conversations.add(conversation_id)
            records.append(
                _record(
                    "dm:inbound:" + reply["message_id"],
                    "dms",
                    "inbound",
                    reply["occurred_at"],
                    kind="dm",
                    provider_id=reply["message_id"],
                    provider_id_status="confirmed",
                    classification=reply["classification"],
                    body=reply["body"],
                    body_status="captured",
                    **common,
                )
            )

    for item in observations:
        conversation_id = item.get("conversation_id")
        if conversation_id not in conversation_ids:
            raise ValueError("live observation has no audited DM conversation")
        conversation = next(
            value for value in conversations if value["conversation_id"] == conversation_id
        )
        native_id = item.get("native_message_id")
        fingerprint = hashlib.sha256(
            encode([conversation_id, item.get("occurred_at"), item.get("body"), native_id]).encode()
        ).hexdigest()[:24]
        replied_conversations.add(conversation_id)
        records.append(
            _record(
                "dm:inbound:" + (native_id or "pending:" + fingerprint),
                "dms",
                "inbound",
                item["occurred_at"],
                kind="dm",
                account=account,
                conversation_id=conversation_id,
                conversation_url=conversation["conversation_url"],
                contact_name=conversation["name"],
                contact_handle=conversation["handle"],
                provider_id=native_id,
                provider_id_status="confirmed" if native_id else "pending_readback",
                classification=item.get("classification", "unknown"),
                body=item["body"],
                body_status="captured",
                evidence=item.get("evidence"),
                timestamp_source=item.get("timestamp_source"),
            )
        )

    records.sort(key=lambda item: (item["occurred_at"], item["record_id"]), reverse=True)
    authored_urls = {item["reply_url"] for item in authored}
    successful_reply_urls = {
        item["target_url"]
        for item in engagements
        if item["interaction"] == "reply" and item["target_url"] in authored_urls
    }
    interaction_counts = {
        interaction: sum(item["interaction"] == interaction for item in engagements)
        for interaction in ("reply", "like")
    }
    pending_provider_ids = sum(
        item.get("provider_id_status") == "pending_readback" for item in records
    )
    reply_total = len(authored)
    dm_total = len(conversations)
    observed_times = [pairs["observed_at"], dms["checked_at"]]
    if overlays.get("observed_at"):
        observed_times.append(overlays["observed_at"])
    captured_at = max(_iso(value) for value in observed_times)
    return {
        "schema_version": 1,
        "account": account,
        "platform": "x",
        "captured_at": captured_at,
        "coverage": {
            "claim": "bounded provider evidence; not full X account history",
            "authored_replies": pairs.get("coverage_note"),
            "dms": dms.get("coverage"),
        },
        "summary": {
            "replies": {
                "authored_total": reply_total,
                "successful_authored_total": len(successful_reply_urls),
                "success_rate_percent": round(100 * len(successful_reply_urls) / reply_total, 2)
                if reply_total
                else 0,
                "canonical_inbound_reply_total": interaction_counts["reply"],
                "canonical_like_total": interaction_counts["like"],
                "canonical_engagement_total": len(engagements),
            },
            "dms": {
                "conversation_total": dm_total,
                "observed_replied_conversation_total": len(replied_conversations),
                "success_rate_percent": round(100 * len(replied_conversations) / dm_total, 2)
                if dm_total
                else 0,
                "receipt_confirmed_replied_conversation_total": sum(
                    bool(item.get("reply")) for item in conversations
                ),
                "observed_outbound_message_total": dms.get("observed_outbound_message_count"),
                "normalized_outbound_message_total": sum(
                    item["area"] == "dms" and item["direction"] == "outbound" for item in records
                ),
                "message_body_available_total": sum(
                    item["area"] == "dms" and item.get("body_status") == "captured"
                    for item in records
                ),
            },
            "pending_provider_id_total": pending_provider_ids,
            "normalized_record_total": len(records),
        },
        "records": records,
        "raw_sources": {
            "authored_reply_pairs": pairs,
            "canonical_engagements": engagements,
            "dm_reconciliation": dms,
            "live_dm_observations": overlays,
        },
        "source_paths": {
            "authored_reply_pairs": str(pairs_path),
            "reply_ledger": str(reply_db_path),
            "dm_reconciliation": str(dms_path),
            "live_dm_observations": str(resolved_overlay_path) if resolved_overlay_path else None,
        },
    }


def posthog_event(blob, ledger: Ledger):
    """Return an aggregate-only PostHog event for the private blob."""
    reply = blob["summary"]["replies"]
    dms = blob["summary"]["dms"]
    with ledger.connect() as connection:
        account_key = ledger.identity(connection, "x|" + blob["account"])
    properties = {
        "distinct_id": "gtm-operator-" + account_key,
        "$process_person_profile": False,
        "$geoip_disable": True,
        "workspace": "gtm-dev",
        "dashboard_key": "gtm-dev",
        "platform": "x",
        "account_key": account_key,
        "coverage": blob["coverage"]["claim"],
        "reply_outbound_total": reply["authored_total"],
        "reply_success_total": reply["successful_authored_total"],
        "reply_success_rate_percent": reply["success_rate_percent"],
        "reply_inbound_total": reply["canonical_inbound_reply_total"],
        "reply_engagement_total": reply["canonical_engagement_total"],
        "dm_conversation_total": dms["conversation_total"],
        "dm_outbound_message_total": dms["normalized_outbound_message_total"],
        "dm_message_body_available_total": dms["message_body_available_total"],
        "dm_success_total": dms["observed_replied_conversation_total"],
        "dm_success_rate_percent": dms["success_rate_percent"],
        "dm_receipt_confirmed_success_total": dms["receipt_confirmed_replied_conversation_total"],
        "pending_provider_id_total": blob["summary"]["pending_provider_id_total"],
        "audited_at": blob["captured_at"],
    }
    identity = encode([blob["captured_at"], properties])
    event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "gtm.x_audit_snapshot|" + identity))
    return {
        "event": "gtm.x_audit_snapshot",
        "uuid": event_id,
        "timestamp": blob["captured_at"],
        "properties": properties,
    }


def queue_posthog_event(blob, ledger: Ledger):
    event = posthog_event(blob, ledger)
    payload = encode(event)
    with ledger.connect() as connection:
        prior = connection.execute(
            "SELECT payload_json FROM snapshots WHERE event_id=?", (event["uuid"],)
        ).fetchone()
        if prior and prior["payload_json"] != payload:
            raise ValueError("conflicting X audit snapshot")
        connection.execute(
            "INSERT OR IGNORE INTO snapshots(event_id,payload_json) VALUES(?,?)",
            (event["uuid"], payload),
        )
    return event["uuid"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reply-pairs", type=Path, required=True)
    parser.add_argument("--reply-db", type=Path, default=DEFAULT_REPLY_DB)
    parser.add_argument("--dms", type=Path, required=True)
    parser.add_argument("--dm-overlays", type=Path, default=DEFAULT_OVERLAYS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--activity-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--queue-posthog", action="store_true")
    parser.add_argument("--sync", action="store_true")
    args = parser.parse_args()

    blob = build_blob(args.reply_pairs, args.reply_db, args.dms, args.dm_overlays)
    output = args.output.resolve()
    if not output.is_relative_to(ROOT.resolve()):
        raise ValueError("private blob output must stay inside CRM")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(blob, indent=2) + "\n")
    output.chmod(0o600)
    result = {"output": str(output), "summary": blob["summary"]}
    if args.queue_posthog or args.sync:
        ledger = Ledger(args.activity_db)
        result["posthog_event_id"] = queue_posthog_event(blob, ledger)
        if args.sync:
            result["delivery"] = ledger.deliver_snapshots(PostHog())
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
