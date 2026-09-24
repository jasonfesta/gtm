"""Manual Smartlead email run with CRM and PostHog JSON handoffs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path

from crm.local_secrets import smartlead_token
from crm.smartlead import SmartleadClient, SmartleadError

CRM_STATES = {
    "NOT_CONTACTED": "enrolled",
    "STARTED": "scheduled",
    "INPROGRESS": "submitted",
    "SENT": "sent",
    "ACCEPTED": "sent",
    "OPENED": "sent",
    "CLICKED": "sent",
    "NOT REPLIED": "sent",
    "DELIVERED": "delivered",
    "COMPLETED": "sent",
    "INTERESTED": "replied",
    "NOT_INTERESTED": "replied",
    "REPLIED": "replied",
    "BOUNCED": "bounced",
    "UNSUBSCRIBED": "opted_out",
    "DO_NOT_CONTACT": "opted_out",
    "COMPLAINED": "complained",
    "FAILED": "failed",
}
CONFIRMED_CRM_STATES = {"sent", "delivered", "replied", "bounced", "opted_out", "complained"}


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def stable_key(*parts):
    value = "\x1f".join(str(part) for part in parts)
    return "email_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def read_json(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def prepare_candidates(snapshot, campaign_id):
    rows = snapshot.get("human_email", [])
    if not isinstance(rows, list):
        raise ValueError("snapshot human_email must be a list")
    prepared = []
    seen = set()
    required = ("contact_id", "person_id", "email", "email_subject", "email_body", "copy_version")
    for row in rows:
        missing = [
            key for key in required if not isinstance(row.get(key), str) or not row[key].strip()
        ]
        if missing:
            raise ValueError(f"email candidate missing: {', '.join(missing)}")
        email_key = row["email"].strip().casefold()
        if email_key in seen:
            previous = next(item for item in prepared if item["email"] == email_key)
            if any(previous[key] != row[key] for key in required if key != "email"):
                raise ValueError("duplicate email has conflicting canonical identity or copy")
            continue
        seen.add(email_key)
        item = dict(row)
        item["email"] = email_key
        item["idempotency_key"] = stable_key(campaign_id, row["contact_id"], email_key)
        prepared.append(item)
    return prepared


def validate_enrollment(rows, *, max_age_seconds=900):
    if not isinstance(max_age_seconds, (int, float)) or max_age_seconds <= 0:
        raise ValueError("suppression_max_age_seconds must be positive")
    for row in rows:
        if row.get("email_verified") is not True or not row.get("email_verification_evidence"):
            raise ValueError("enrollment requires verified email route evidence")
        if row.get("identity_status") != "verified":
            raise ValueError("enrollment requires verified canonical identity")
        if row.get("suppression_context_complete") is not True or not row.get(
            "suppression_checked_at"
        ):
            raise ValueError("enrollment requires current Ops suppression context")
        if not isinstance(row.get("active_suppressions"), list):
            raise ValueError("enrollment requires explicit suppression list from Ops")
        if row.get("suppressed") or row.get("active_suppressions"):
            raise ValueError("suppressed recipient cannot be enrolled")
        checked = dt.datetime.fromisoformat(row["suppression_checked_at"].replace("Z", "+00:00"))
        if checked.tzinfo is None:
            raise ValueError("Ops suppression check needs a timezone-aware timestamp")
        age = (dt.datetime.now(dt.timezone.utc) - checked).total_seconds()
        if not 0 <= age <= max_age_seconds:
            raise ValueError(
                "Ops suppression context is stale or future-dated; refresh before enrollment"
            )


def index_provider_leads(rows):
    return {
        str(item.get("lead", item).get("email", "")).strip().casefold(): item
        for item in rows
        if item.get("lead", item).get("email")
    }


def matches_enrollment(row, provider):
    lead = provider.get("lead", provider)
    custom = lead.get("custom_fields") or provider.get("custom_fields") or {}
    expected = smartlead_lead(row)["custom_fields"]
    return isinstance(custom, dict) and all(
        str(custom.get(key, "")) == str(expected[key])
        for key in (
            "crm_contact_id",
            "crm_person_id",
            "email_subject",
            "email_body",
            "copy_version",
        )
    )


def smartlead_lead(row):
    custom = dict(row.get("custom_fields", {}))
    custom.update(
        email_subject=row["email_subject"],
        email_body=row["email_body"],
        crm_contact_id=row["contact_id"],
        crm_person_id=row.get("person_id", ""),
        copy_version=row["copy_version"],
        idempotency_key=row["idempotency_key"],
    )
    return {
        "email": row["email"],
        "first_name": row.get("first_name", ""),
        "last_name": row.get("last_name", ""),
        "company_name": row.get("company_name", ""),
        "custom_fields": custom,
    }


def provider_state(row):
    lead = row.get("lead", row)
    raw = str(
        row.get("email_status")
        or row.get("status")
        or lead.get("email_status")
        or lead.get("status")
        or "uncertain"
    ).upper()
    return CRM_STATES.get(raw, raw.lower())


def unsent_provider_state(row):
    state = provider_state(row)
    return "uncertain" if state in {"sent", "delivered", "replied"} else state


def crm_record(row, campaign_id, state, *, provider=None):
    provider = provider or {}
    lead = provider.get("lead", provider)
    email_account = provider.get("email_account") or {}
    last_message = provider.get("last_message") or {}
    if state in {"sent", "delivered", "replied"}:
        outcome_time = (
            last_message.get("replied_at")
            if state == "replied"
            else provider.get("delivered_at")
            if state == "delivered"
            else last_message.get("sent_at")
        )
        if (
            not (provider.get("message_id") or provider.get("id"))
            or not outcome_time
            or not (email_account.get("email") or provider.get("sender_account"))
        ):
            state = "uncertain"
    return {
        "idempotency_key": row["idempotency_key"],
        "channel": "email",
        "provider": "smartlead",
        "message_kind": "outbound_email",
        "direction": "outbound",
        "human_contact_id": row["contact_id"],
        "person_id": row.get("person_id"),
        "email": row["email"],
        "campaign_id": str(campaign_id),
        "sender_account": email_account.get("email")
        or provider.get("sender_account")
        or row.get("sender_account"),
        "template_id": row.get("template_id"),
        "copy_version": row["copy_version"],
        "provider_lead_id": lead.get("id") or provider.get("lead_id"),
        "provider_message_id": provider.get("message_id") or provider.get("id"),
        "conversation_id": provider.get("thread_id") or provider.get("conversation_id"),
        "reply_to_provider_message_id": provider.get("reply_to_message_id"),
        "state": state,
        "crm_category": row.get("crm_category"),
        "occurred_at": (provider.get("delivered_at") if state == "delivered" else None)
        or (last_message.get("replied_at") if state == "replied" else None)
        or last_message.get("sent_at")
        or provider.get("updated_at")
        or utc_now(),
        "provider_reference": provider.get("campaign_lead_map_id"),
        "provider_error": provider.get("error"),
    }


def aggregate_metrics(campaign_id, crm_rows, analytics=None):
    counts = {}
    for row in crm_rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    provider_counts = {
        key: int(analytics[key])
        for key in (
            "total_count",
            "sent_count",
            "unique_sent_count",
            "reply_count",
            "bounce_count",
            "unsubscribed_count",
            "open_count",
            "click_count",
        )
        if isinstance(analytics, dict)
        and not isinstance(analytics.get(key), bool)
        and str(analytics.get(key, "")).isdigit()
    }
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source": "smartlead",
        "campaign_id": str(campaign_id),
        "counts": counts,
        "provider_analytics": provider_counts,
    }


def build_handoffs(campaign_id, crm_rows, analytics=None):
    confirmed_rows = [row for row in crm_rows if row["state"] in CONFIRMED_CRM_STATES]
    missing_senders = [row["email"] for row in confirmed_rows if not row["sender_account"]]
    if missing_senders:
        raise ValueError(
            "confirmed Smartlead outcomes need sender_account: " + ", ".join(missing_senders)
        )
    handoff_id = stable_key(
        "crm-handoff",
        campaign_id,
        *(
            sorted(
                row["idempotency_key"]
                + ":"
                + row["state"]
                + ":"
                + str(row.get("provider_message_id") or "")
                for row in crm_rows
            )
        ),
    )
    analytics_identity = aggregate_metrics(campaign_id, crm_rows, analytics)["provider_analytics"]
    run_id = stable_key("run", handoff_id, json.dumps(analytics_identity, sort_keys=True))
    crm_handoff = {
        "schema_version": 1,
        "handoff_id": handoff_id,
        "generated_at": utc_now(),
        "source_agent": "email agent",
        "operations": [{"action": "event_record", "payload": row} for row in confirmed_rows],
        "summary": aggregate_metrics(campaign_id, crm_rows, analytics),
    }
    posthog_handoff = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source_agent": "email agent",
        "run_id": run_id,
        "events": [
            {
                "event": "gtm.email_agent_run",
                "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, run_id)),
                "timestamp": utc_now(),
                "properties": dict(
                    aggregate_metrics(campaign_id, crm_rows, analytics),
                    distinct_id="email-agent:smartlead:" + str(campaign_id),
                ),
            }
        ],
    }
    return crm_handoff, posthog_handoff


def provision(client, campaign):
    current = client.find_campaign(campaign["name"])
    if current is None:
        current = client.create_campaign(campaign["name"])
    campaign_id = current["id"]
    client.configure_campaign(campaign_id, campaign)
    return campaign_id


def run(
    config,
    snapshot,
    *,
    client=None,
    provision_campaign=False,
    enroll=False,
    activate=False,
    reconcile=False,
):
    campaign = config["campaign"]
    campaign_id = campaign.get("id")
    if client is None and (provision_campaign or enroll or activate or reconcile):
        client = SmartleadClient(smartlead_token())
    if provision_campaign:
        campaign_id = provision(client, campaign)
    if campaign_id is None:
        campaign_id = "unprovisioned"
    if enroll:
        validate_enrollment(
            snapshot.get("human_email", []),
            max_age_seconds=config.get("suppression_max_age_seconds", 900),
        )
    prepared = prepare_candidates(snapshot, campaign_id)
    crm_rows = [crm_record(row, campaign_id, "prepared") for row in prepared]
    provider_results = []
    if enroll:
        if campaign_id == "unprovisioned":
            raise ValueError("campaign.id or --provision required for enrollment")
        before = index_provider_leads(client.campaign_leads(campaign_id))
        conflicts = [
            row
            for row in prepared
            if row["email"] in before and not matches_enrollment(row, before[row["email"]])
        ]
        if conflicts:
            raise ValueError(
                "existing campaign recipient identity/copy differs; reconcile before enrollment"
            )
        missing = [row for row in prepared if row["email"] not in before]
        if missing:
            try:
                provider_results = client.add_leads(
                    campaign_id, [smartlead_lead(row) for row in missing]
                )
            except SmartleadError:
                # A timeout or later-batch failure can occur after prior leads were imported.
                # Provider readback below, never aggregate added_count, determines each state.
                provider_results = [
                    {"status": "uncertain", "reason": "import failed; readback required"}
                ]
        try:
            after = index_provider_leads(client.campaign_leads(campaign_id))
        except SmartleadError:
            after = {}
            provider_results.append(
                {"status": "uncertain", "reason": "post-import readback failed"}
            )
        crm_rows = [
            crm_record(
                row,
                campaign_id,
                "enrolled"
                if row["email"] in after and matches_enrollment(row, after[row["email"]])
                else "uncertain",
            )
            for row in prepared
        ]
    activation_receipt = None
    if activate:
        if campaign_id == "unprovisioned":
            raise ValueError("campaign.id or --provision required for activation")
        if not enroll:
            raise ValueError("activation requires --enroll in the same explicit run")
        if not prepared or any(row["state"] != "enrolled" for row in crm_rows):
            activation_receipt = {
                "status": "blocked",
                "reason": "recipient enrollment not verified",
            }
        else:
            activation_receipt = client.activate_campaign(campaign_id)
    analytics = {}
    if reconcile:
        if campaign_id == "unprovisioned":
            raise ValueError("campaign.id or --provision required for reconciliation")
        provider_leads = client.campaign_leads(campaign_id)
        by_email = {}
        for item in provider_leads:
            lead = item.get("lead", item)
            email = str(lead.get("email", "")).strip().casefold()
            if email:
                by_email[email] = item
        sent_by_email = {}
        for item in client.sent_messages(campaign_id):
            email = str((item.get("lead") or {}).get("email", "")).strip().casefold()
            if email and email not in sent_by_email:
                sent_by_email[email] = item
        crm_rows = [
            crm_record(
                row,
                campaign_id,
                "uncertain"
                if row["email"] not in by_email
                or not matches_enrollment(row, by_email[row["email"]])
                else provider_state(sent_by_email[row["email"]])
                if row["email"] in sent_by_email
                else unsent_provider_state(by_email[row["email"]])
                if row["email"] in by_email
                else "uncertain",
                provider=(
                    dict(
                        sent_by_email[row["email"]],
                        lead=dict(
                            by_email.get(row["email"], {}).get("lead", {}),
                            **(sent_by_email[row["email"]].get("lead") or {}),
                        ),
                    )
                    if row["email"] in sent_by_email
                    else by_email.get(row["email"])
                ),
            )
            for row in prepared
        ]
        analytics = client.campaign_analytics(campaign_id)
    crm_handoff, posthog_handoff = build_handoffs(campaign_id, crm_rows, analytics)
    return {
        "run_id": posthog_handoff["run_id"],
        "campaign_id": str(campaign_id),
        "prepared": len(prepared),
        "provider_results": provider_results,
        "recipient_states": crm_rows,
        "activation_receipt": activation_receipt,
        "crm_handoff": crm_handoff,
        "posthog_handoff": posthog_handoff,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--provision", action="store_true", help="create/configure a draft campaign"
    )
    parser.add_argument("--enroll", action="store_true", help="add snapshot recipients to campaign")
    parser.add_argument(
        "--activate",
        action="store_true",
        help="explicitly activate the campaign; requires --enroll",
    )
    parser.add_argument("--reconcile", action="store_true", help="read current provider outcomes")
    args = parser.parse_args()
    result = run(
        read_json(args.config),
        read_json(args.snapshot),
        provision_campaign=args.provision,
        enroll=args.enroll,
        activate=args.activate,
        reconcile=args.reconcile,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "crm-handoff.json", result["crm_handoff"])
    write_json(args.output_dir / "send-to-posthog-log.json", result["posthog_handoff"])
    write_json(
        args.output_dir / "run-receipt.json",
        {
            key: value
            for key, value in result.items()
            if key not in ("crm_handoff", "posthog_handoff")
        },
    )
    print(json.dumps({"campaign_id": result["campaign_id"], "prepared": result["prepared"]}))


if __name__ == "__main__":
    main()
