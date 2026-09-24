"""Daily read-only campaign detail supplement. No account rollups or campaign writes."""

import uuid
from datetime import datetime, timedelta, timezone

from crm.activity import encode

CAMPAIGN_ID = 3935204


def number(data, key):
    value = data[key]
    if isinstance(value, bool) or str(value) != str(int(value)) or int(value) < 0:
        raise ValueError("invalid provider count")
    return int(value)


def replied_people(client):
    seen = set()
    expected = None
    for offset in range(0, 100000, 100):
        data = client.request(
            "GET",
            f"/campaigns/{CAMPAIGN_ID}/leads",
            params={
                "emailStatus": "is_replied",
                "offset": offset,
                "limit": 100,
            },
        )
        total = number(data, "total_leads" if "total_leads" in data else "total")
        if expected is not None and expected != total:
            raise ValueError("provider changed during pagination; retry")
        expected = total
        rows = data.get("data", data.get("leads"))
        if not isinstance(rows, list):
            raise ValueError("unsupported replied-lead response")
        for row in rows:
            lead = row.get("lead", row)
            reply_evidence = (
                row.get("email_stats", {}).get("is_replied") is True
                or lead.get("email_stats", {}).get("is_replied") is True
                or int(row.get("reply_count", lead.get("reply_count", 0))) > 0
            )
            if not reply_evidence or not lead.get("id"):
                raise ValueError("filtered lead requires reply evidence and provider identity")
            seen.add(str(lead["id"]))
        if len(seen) == total:
            return len(seen)
        if not rows:
            raise ValueError("incomplete replied-lead pagination")
    raise ValueError("replied-lead pagination budget exceeded")


def queue(ledger, client, instant=None):
    when = instant or datetime.now(timezone.utc)
    day = (when - timedelta(days=1)).date().isoformat()
    start, end = day + "T00:00:00Z", day + "T23:59:59Z"
    totals = client.campaign_analytics(CAMPAIGN_ID)
    if str(totals.get("id", totals.get("campaign_id"))) != str(CAMPAIGN_ID):
        raise ValueError("unexpected campaign analytics")
    props = {
        "provider": "smartlead",
        "scope": "campaign",
        "campaign_id": str(CAMPAIGN_ID),
        "distinct_id": "smartlead-campaign-3935204",
        "$process_person_profile": False,
        "$geoip_disable": True,
        "day_utc": when.date().isoformat(),
        "period_day_utc": day,
    }
    for target, source in (
        ("sends", "sent_count"),
        ("replies", "reply_count"),
        ("unique_sent", "unique_sent_count"),
        ("unique_opens", "unique_open_count"),
        ("unique_clicks", "unique_click_count"),
    ):
        props[target] = number(totals, source)
    try:
        period = client.request(
            "GET",
            f"/campaigns/{CAMPAIGN_ID}/analytics-by-date",
            params={"start_date": start, "end_date": end},
        )
        if (
            str(period.get("id", period.get("campaign_id"))) != str(CAMPAIGN_ID)
            or period.get("start_date") != start
            or period.get("end_date") != end
        ):
            raise ValueError("provider period mismatch")
        props.update(
            sends_yesterday=number(period, "sent_count"),
            replies_yesterday=number(period, "reply_count"),
            daily_status="provider date-range report",
        )
    except Exception:
        props.update(sends_yesterday=None, replies_yesterday=None, daily_status="unavailable")
    try:
        props["unique_replied"] = replied_people(client)
        props["unique_reply_status"] = (
            "provider leads with reply evidence; human classification unavailable"
        )
    except Exception:
        props["unique_replied"] = None
        props["unique_reply_status"] = "unavailable; provider person-level reconciliation required"
    event_id = str(uuid.uuid4())
    payload = {
        "uuid": event_id,
        "event": "gtm.smartlead_detail_snapshot",
        "timestamp": when.isoformat(),
        "properties": props,
    }
    with ledger.connect() as conn:
        conn.execute(
            "INSERT INTO snapshots(event_id,payload_json) VALUES(?,?)", (event_id, encode(payload))
        )
    return {
        "event_id": event_id,
        "daily_status": props["daily_status"],
        "unique_reply_status": props["unique_reply_status"],
    }
