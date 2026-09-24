"""Reviewed timeline CRM intake, separate from private draft preparation."""

from .contact_normalization import norm
from .human_reply_planning import digest

CONTACT_KIND = {
    "x": "x",
    "linkedin": "linkedin",
    "hacker_news": "other",
    "github": "website",
    "product_hunt": "other",
    "apollo": "other",
}
SOURCE_KIND = {
    "x": "x",
    "linkedin": "linkedin",
    "hacker_news": "news",
    "github": "official_site",
    "product_hunt": "other",
    "apollo": "other",
}


def add_reviewed_person(connection, record):
    """Append supported facts; caller owns journal, transaction and readback."""
    authority = record.get("authority", {})
    if authority.get("kind") not in ("explicit_person_add", "confirmed_public_reply"):
        raise ValueError("explicit person addition or confirmed public reply required")
    if not authority.get("evidence"):
        raise ValueError("authority receipt required")
    if authority["kind"] == "confirmed_public_reply" and (
        authority.get("confirmed") is not True
        or not authority.get("published_url", "").startswith("https://")
        or not authority.get("sender")
        or not authority.get("text")
    ):
        raise ValueError("confirmed actual publication required, not a draft or approval")
    name, channel, profile = record["name"], record["channel"], record["profile_url"]
    if channel not in CONTACT_KIND or not name.strip():
        raise ValueError("named social person required")
    contact_type = CONTACT_KIND[channel]
    normalized = norm(contact_type, profile)
    if not profile.startswith("https://") or not normalized:
        raise ValueError("exact public profile required")
    sources = record["sources"]
    if not sources or any(not s["url"].startswith("https://") for s in sources):
        raise ValueError("public evidence sources required")
    matches = connection.execute(
        "SELECT person_id FROM contact_points WHERE contact_type=? AND normalized_value=?",
        (contact_type, normalized),
    ).fetchall()
    ids = {r["person_id"] for r in matches}
    if len(ids) > 1:
        raise ValueError("conflicting exact contact mappings")
    pid = next(iter(ids), "person_" + digest([contact_type, normalized])[:24])
    namesakes = connection.execute(
        "SELECT person_id FROM people WHERE normalized_name=?", (name.casefold().strip(),)
    ).fetchall()
    if any(row["person_id"] != pid for row in namesakes):
        later_handle = channel in ("hacker_news", "github", "product_hunt", "apollo")
        if later_handle and len(namesakes) == 1 and not ids:
            pid = namesakes[0]["person_id"]
        else:
            raise ValueError("namesake requires reconciliation")
    if ids:
        existing = connection.execute(
            "SELECT full_name FROM people WHERE person_id=?", (pid,)
        ).fetchone()
        if existing["full_name"].casefold().strip() != name.casefold().strip():
            raise ValueError("contact identity conflict")
    # Operator selection is not evidence of verified ownership or stable provider ID.
    connection.execute(
        "INSERT OR IGNORE INTO people (person_id,full_name,normalized_name,current_role,"
        "identity_status,research_status) VALUES (?,?,?,?,'single_source','review_needed')",
        (pid, name, name.casefold().strip(), record.get("role")),
    )
    source_ids = []
    for source in sources:
        sid = "source_" + digest(source["url"])[:24]
        source_type = SOURCE_KIND.get(source.get("type"), source.get("type"))
        if source_type not in (
            "directory",
            "official_site",
            "founder_bio",
            "announcement",
            "corporate_record",
            "linkedin",
            "x",
            "news",
            "email_page",
            "other",
        ):
            source_type = "other"
        connection.execute(
            "INSERT OR IGNORE INTO sources(source_id,url,source_type,quality_tier,accessed_at) "
            "VALUES (?,?,?,?,?)",
            (sid, source["url"], source_type, 1, record["observed_at"]),
        )
        source_ids.append(
            connection.execute(
                "SELECT source_id FROM sources WHERE url=?", (source["url"],)
            ).fetchone()["source_id"]
        )
    connection.execute(
        "INSERT OR IGNORE INTO contact_points(contact_id,person_id,contact_type,value,"
        "normalized_value,verification_status,verification_method,source_id,first_seen_at,notes) "
        "VALUES (?,?,?,?,?,'unverified','reviewed_public_observation',?,?,?)",
        (
            "contact_" + digest([pid, normalized])[:24],
            pid,
            contact_type,
            profile,
            normalized,
            source_ids[0],
            record["observed_at"],
            "Operator-selected; provider identity verification pending.",
        ),
    )
    return {
        "person_id": pid,
        "existing": bool(ids),
        "profile_url": profile,
        "contact_verification": "preserved_existing_or_unverified",
        "send_authorized": False,
    }
