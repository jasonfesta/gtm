"""Cold-contact limits from runtime/Rules.md; local evidence only, never send authority."""

import datetime as dt
import json
import re
from pathlib import Path

RULES_PATH = Path(__file__).resolve().parents[1] / "Rules.md"


def load_policy(path=None):
    """Read the Markdown source on every check; fail closed on invalid policy."""
    try:
        blocks = re.findall(
            r"^```crm-contact-policy\n(.*?)^```[ \t]*$",
            Path(path or RULES_PATH).read_text(),
            re.M | re.S,
        )
        if len(blocks) != 1:
            raise ValueError("exactly one policy block required")
        policy = json.loads(blocks[0])
        expected = {
            "version",
            "channels",
            "social_formats",
            "max_unanswered_touches",
            "max_unanswered_per_channel",
            "minimum_gap_hours",
            "stop_drip_on_engagement",
            "allow_conversation_replies",
            "one_response_per_incoming",
            "automatic_restart",
        }
        if set(policy) != expected or policy["version"] != 1:
            raise ValueError("unsupported policy schema")
        if policy["channels"] != ["x", "linkedin", "email"] or policy["social_formats"] != [
            "public_reply",
            "dm",
        ]:
            raise ValueError("unsupported channel/format scope")
        for key in ("max_unanswered_touches", "max_unanswered_per_channel", "minimum_gap_hours"):
            if type(policy[key]) is not int or policy[key] <= 0:
                raise ValueError("positive integer required: " + key)
        if (
            policy["stop_drip_on_engagement"] is not True
            or policy["one_response_per_incoming"] is not True
            or policy["automatic_restart"] is not False
        ):
            raise ValueError(
                "engagement stop, reply deduplication and no automatic restart required"
            )
        if type(policy["allow_conversation_replies"]) is not bool:
            raise ValueError("conversation flag must be boolean")
        return policy
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("runtime/Rules.md policy unavailable or invalid: " + str(exc)) from exc


def assess(channel, events, *, now=None, reply_to=None):
    """Evaluate one person's reconciled history across accounts and campaigns.

    Receipts for one message must share external_reference. Missing references
    are counted separately, conservatively. Passing is only a cadence check.
    """
    policy = load_policy()
    if channel not in policy["channels"]:
        raise ValueError("supported channels are x, linkedin and email (Gmail)")
    now = now or dt.datetime.now(dt.timezone.utc)
    events = list(events)
    reasons, messages, times = [], {}, []
    engaged = False
    if reply_to and not policy["allow_conversation_replies"]:
        reasons.append("conversation_replies_disabled")
    if reply_to:
        incoming = next(
            (
                e
                for e in events
                if e["event_id"] == reply_to
                and e["direction"] == "inbound"
                and e["outcome"] == "replied"
                and e["channel"] == channel
            ),
            None,
        )
        if not incoming:
            reasons.append("actual_incoming_reply_required")
        if any(
            e.get("in_reply_to_message_id") == reply_to
            and e["direction"] == "outbound"
            and e["outcome"] != "drafted"
            for e in events
        ):
            reasons.append("incoming_already_answered")
    for event in events:
        outcome = event["outcome"]
        if outcome == "drafted":
            continue
        if outcome in {"opted_out", "declined"}:
            reasons.append("recipient_stop")
        if outcome == "bounced":
            reasons.append("bounce_needs_review")
        if outcome == "unknown":
            reasons.append("uncertain_submission_reconcile")
        if outcome in {"replied", "engaged"} or event["direction"] == "inbound":
            engaged = True
            if not reply_to:
                reasons.append("conversation_needs_review")
        if event["direction"] != "outbound":
            continue
        try:
            occurred = dt.datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00"))
            if occurred.utcoffset() is None or occurred > now:
                raise ValueError("invalid timestamp")
            times.append(occurred)
        except (ValueError, TypeError, AttributeError):
            reasons.append("history_timestamp_needs_review")
        key = (event["channel"], event.get("external_reference") or event["event_id"])
        messages[key] = event["channel"]
    counts = {ch: sum(value == ch for value in messages.values()) for ch in policy["channels"]}
    if not reply_to and counts[channel] >= policy["max_unanswered_per_channel"]:
        reasons.append("channel_touch_limit")
    if not reply_to and len(messages) >= policy["max_unanswered_touches"]:
        reasons.append("person_touch_limit")
    next_at = max(times) + dt.timedelta(hours=policy["minimum_gap_hours"]) if times else None
    if not reply_to and next_at and now < next_at:
        reasons.append("cross_channel_cooldown")
    return {
        "blockers": sorted(set(reasons)),
        "mode": "conversation" if reply_to else "drip",
        "drip_state": "engaged"
        if engaged
        else "exhausted"
        if len(messages) >= policy["max_unanswered_touches"]
        else "waiting"
        if times
        else "new",
        "channel_counts": counts,
        "total_touches": len(messages),
        "next_cadence_at": next_at.isoformat() if next_at else None,
        "scope": "Local cadence only; history reconciliation, readiness and authority required.",
    }


def person_assessment(connection, person_id, channel):
    # Include aliases sharing an exact normalized contact; never reset via a new campaign/account.
    rows = connection.execute(
        "SELECT oe.* FROM outreach_events oe WHERE oe.person_id=? OR oe.person_id IN ("
        "SELECT other.person_id FROM contact_points mine JOIN contact_points other "
        "ON mine.contact_type=other.contact_type AND "
        "lower(trim(mine.value))=lower(trim(other.value)) WHERE mine.person_id=?)",
        (person_id, person_id),
    )
    result = assess(channel, [dict(row) for row in rows])
    if connection.execute(
        "SELECT 1 FROM suppressions s WHERE s.is_active=1 "
        "AND (s.channel IS NULL OR s.channel=? OR "
        "s.reason IN ('opt_out','do_not_contact','privacy_request')) "
        "AND (s.person_id=? OR s.company_id=(SELECT primary_company_id FROM people WHERE person_id=?) "
        "OR s.contact_id IN (SELECT other.contact_id FROM contact_points mine "
        "JOIN contact_points other ON mine.contact_type=other.contact_type "
        "AND lower(trim(mine.value))=lower(trim(other.value)) WHERE mine.person_id=?))",
        (channel, person_id, person_id, person_id),
    ).fetchone():
        result["blockers"].append("suppressed")
    return result
