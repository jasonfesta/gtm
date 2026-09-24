"""Publish reviewed, body-free X DM reconciliation facts to the shared CRM."""

import argparse
import hashlib
import json
from pathlib import Path

from crm.database import CANONICAL, ROOT, connect


def stable(prefix, *values):
    raw = "|".join(str(value) for value in values)
    return prefix + hashlib.sha256(raw.encode()).hexdigest()[:24]


def reconcile(path, *, connection_factory=None):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
        raise ValueError("private X reconciliation inside CRM required")
    data = json.loads(path.read_text())
    if (
        data.get("schema_version") != 1
        or data.get("account_profile_url") != "https://x.com/jasonfesta"
    ):
        raise ValueError("reviewed @jasonfesta reconciliation required")
    conversations = data.get("conversations")
    if not isinstance(conversations, list) or not conversations:
        raise ValueError("nonempty conversation set required")
    factory = connection_factory or (lambda: connect(CANONICAL))
    connection = factory()
    created_people = created_events = 0
    try:
        with connection:
            for item in conversations:
                handle = item["handle"]
                profile = item["profile_url"]
                conversation = item["conversation_url"]
                if profile.casefold() != f"https://x.com/{handle}".casefold():
                    raise ValueError("profile and handle mismatch")
                matches = connection.execute(
                    """SELECT DISTINCT person_id FROM contact_points
                       WHERE contact_type='x' AND
                       (lower(normalized_value)=? OR lower(value)=?)""",
                    (handle.casefold(), profile.casefold()),
                ).fetchall()
                people = {row[0] for row in matches}
                if len(people) > 1:
                    raise ValueError("conflicting X contact ownership")
                person_id = next(iter(people), stable("dm_x_", handle.casefold()))
                source = connection.execute(
                    "SELECT source_id FROM sources WHERE url=?", (profile,)
                ).fetchone()
                source_id = source[0] if source else stable("dm_src_", profile.casefold())
                if not source:
                    connection.execute(
                        """INSERT INTO sources
                           (source_id,url,title,publisher,source_type,quality_tier,accessed_at)
                           VALUES (?,?,?,'X','x',2,?)""",
                        (source_id, profile, f"@{handle} on X", data["checked_at"]),
                    )
                person = connection.execute(
                    "SELECT person_id FROM people WHERE person_id=?", (person_id,)
                ).fetchone()
                if not person:
                    connection.execute(
                        """INSERT INTO people
                           (person_id,full_name,normalized_name,identity_status,research_status,confidence)
                           VALUES (?,?,?,'single_source','review_needed',90)""",
                        (person_id, item["name"], item["name"].casefold().strip()),
                    )
                    created_people += 1
                connection.execute(
                    """INSERT OR IGNORE INTO contact_points
                       (contact_id,person_id,contact_type,value,normalized_value,
                        verification_status,verification_method,confidence,is_primary,is_public,
                        source_id,first_seen_at,last_verified_at)
                       VALUES (?,?,'x',?,?,'confirmed','observed_x_dm',100,1,1,?,?,?)""",
                    (
                        stable("dm_cp_", handle.casefold()),
                        person_id,
                        profile,
                        handle.casefold(),
                        source_id,
                        data["checked_at"],
                        data["checked_at"],
                    ),
                )
                outbound = item["outbound"]
                event_id = "dm_x_" + outbound["message_id"]
                prior = connection.execute(
                    "SELECT event_id FROM outreach_events WHERE event_id=?", (event_id,)
                ).fetchone()
                if not prior:
                    connection.execute(
                        """INSERT INTO outreach_events
                           (event_id,person_id,channel,direction,occurred_at,outcome,
                            template_id,external_reference)
                           VALUES (?,?,'x','outbound',?,'sent','x_dm_conversation_v1',?)""",
                        (event_id, person_id, outbound["occurred_at"], conversation),
                    )
                    created_events += 1
                reply = item.get("reply")
                if reply:
                    reply_id = "dm_x_" + reply["message_id"]
                    prior = connection.execute(
                        "SELECT event_id FROM outreach_events WHERE event_id=?", (reply_id,)
                    ).fetchone()
                    if not prior:
                        connection.execute(
                            """INSERT INTO outreach_events
                               (event_id,person_id,channel,direction,occurred_at,outcome,
                                template_id,external_reference)
                               VALUES (?,?,'x','inbound',?,'replied','x_dm_reply_v1',?)""",
                            (reply_id, person_id, reply["occurred_at"], conversation),
                        )
                        created_events += 1
        with connection:
            outbound = connection.execute(
                """SELECT count(*) FROM outreach_events
                   WHERE channel='x' AND direction='outbound'
                   AND template_id='x_dm_conversation_v1'"""
            ).fetchone()[0]
            replies = connection.execute(
                """SELECT count(*) FROM outreach_events
                   WHERE channel='x' AND direction='inbound'
                   AND template_id='x_dm_reply_v1'"""
            ).fetchone()[0]
    finally:
        connection.close()
    return {
        "conversations": outbound,
        "replies": replies,
        "created_people": created_people,
        "created_events": created_events,
        "private_bodies_exported": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(reconcile(args.path), indent=2))


if __name__ == "__main__":
    main()
