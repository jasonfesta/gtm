"""Audit every shared CRM person for confirmed public reply locations."""

import json
from collections import Counter
from pathlib import Path

from crm.human_inbox import now


def audit(shared, locations):
    people = {
        r["person_id"]: dict(
            person_id=r["person_id"], name=r["full_name"], profiles=[], locations=[]
        )
        for r in shared.execute("SELECT person_id,full_name FROM people ORDER BY person_id")
    }
    for r in shared.execute(
        "SELECT person_id,contact_type,value FROM contact_points WHERE contact_type IN ('x','linkedin')"
    ):
        if r["person_id"] in people:
            people[r["person_id"]]["profiles"].append(
                dict(channel=r["contact_type"], url=r["value"])
            )
    with locations.watcher.connection() as c:
        local = {
            r["shared_claim_id"]
            for r in c.execute(
                "SELECT shared_claim_id FROM public_reply_locations WHERE shared_state='confirmed'"
            )
        }
    invalid = []
    for r in shared.execute(
        "SELECT claim_id,entity_id,claim_value FROM evidence_claims WHERE entity_type='person' AND field_name='public_reply_location' AND verification_status='accepted'"
    ):
        if r["entity_id"] not in people:
            continue
        try:
            facts = json.loads(r["claim_value"])
            if not facts.get("task_evidence"):
                continue
            if facts["channel"] not in ("x", "linkedin") or not all(
                facts.get(k)
                for k in (
                    "reply_url",
                    "parent_url",
                    "conversation_url",
                    "account_profile_url",
                    "provider_id",
                )
            ):
                raise ValueError("incomplete location")
        except (ValueError, KeyError, TypeError):
            invalid.append(r["claim_id"])
            continue
        people[r["entity_id"]]["locations"].append(
            dict(**facts, claim_id=r["claim_id"], local_revisit_registered=r["claim_id"] in local)
        )
    for p in people.values():
        p["status"] = (
            "confirmed_reply_link"
            if p["locations"]
            else "missing_reply_link"
            if p["profiles"]
            else "missing_social_profile"
        )
    return dict(
        checked_at=now(),
        total_people=len(people),
        counts=dict(Counter(p["status"] for p in people.values())),
        invalid_claims=invalid,
        people=list(people.values()),
    )


def write_report(report, path):
    """Private immutable person-level audit; missing links never imply a sent reply."""
    lines = [
        "# Whole CRM reply coverage",
        "",
        f"Checked: {report['checked_at']}",
        "",
        f"People audited: {report['total_people']}",
        "",
        "```json",
        json.dumps(report["counts"], indent=2),
        "```",
        "",
        "This audits CRM records, not complete live inbox/thread coverage. Missing links require evidence discovery, not invented sent history.",
        "",
    ]
    for p in report["people"]:
        lines.extend([f"## {p['person_id']}", "", f"Status: {p['status']}", ""])
        for location in p["locations"]:
            lines.append(
                f"- Confirmed reply: {location['reply_url']} — local revisit registered: {location['local_revisit_registered']}"
            )
        for profile in p["profiles"]:
            lines.append(f"- Profile to review: {profile['url']}")
        lines.append("")
    if report["invalid_claims"]:
        lines.extend(
            ["Invalid claims held for reconciliation: " + ", ".join(report["invalid_claims"]), ""]
        )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as f:
        f.write("\n".join(lines))
    path.chmod(0o600)


if __name__ == "__main__":
    import argparse

    from crm.database import connect
    from crm.reply_locations import ReplyLocations

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    with connect() as shared:
        report = audit(shared, ReplyLocations())
    write_report(report, args.output)
    print(json.dumps({k: v for k, v in report.items() if k != "people"}))
