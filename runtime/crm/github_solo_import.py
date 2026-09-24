"""Collect public agent developers from GitHub and import a reviewed CRM cohort.

GitHub is the identity authority. X is accepted only from GitHub's own
``twitterUsername`` profile field. The command stages JSON/CSV first; ``--apply``
adds people, relationship contacts, sources and append-only cohort tags.
It never sends outreach and never guesses social identities.
"""

import argparse
import csv
import hashlib
import json
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from crm.database import CANONICAL, connect

CLUSTERS = {
    "voice_calling": [
        '"voice agent" in:name,description,readme',
        '"phone agent" in:name,description,readme',
        '"AI receptionist" in:name,description,readme',
        "topic:voice-agent",
        "topic:voice-agents",
        "topic:conversational-ai",
    ],
    "booking_scheduling": [
        '"appointment booking" agent in:name,description,readme',
        '"booking agent" AI in:name,description,readme',
        '"appointment setter" AI in:name,description,readme',
        '"calendar agent" in:name,description,readme',
        "topic:appointment-booking",
    ],
    "agent_skills": [
        '"SKILL.md" agent in:readme',
        '"agent skills" in:name,description,readme',
        "topic:agent-skills",
        "topic:claude-skills",
        "topic:openclaw-skills",
    ],
    "mcp_tools": [
        '"MCP server" in:name,description,readme',
        "topic:mcp-server",
        "topic:model-context-protocol",
        "topic:mcp",
    ],
    "agent_frameworks": [
        '"AI agent framework" in:name,description,readme',
        '"multi-agent" in:name,description,readme',
        '"autonomous agent" in:name,description,readme',
        "topic:ai-agents",
        "topic:llm-agent",
        "topic:multi-agent-systems",
    ],
    "sales_gtm_agents": [
        '"sales agent" AI in:name,description,readme',
        '"lead qualification" agent in:name,description,readme',
        '"outbound agent" AI in:name,description,readme',
        '"CRM agent" AI in:name,description,readme',
    ],
}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def stable(prefix, value):
    return prefix + "_" + hashlib.sha256(value.encode()).hexdigest()[:24]


def gh_json(endpoint, fields=None):
    command = ["gh", "api", "-X", "GET", endpoint]
    for key, value in (fields or {}).items():
        command.extend(["-f", f"{key}={value}"])
    for attempt in range(5):
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode == 0:
            return json.loads(result.stdout)
        if "rate limit" not in result.stderr.lower() or attempt == 4:
            raise RuntimeError(result.stderr.strip() or "GitHub API request failed")
        time.sleep(30 * (attempt + 1))


def collect(target):
    developers = {}
    queries_run = []
    repo_count = 0
    per_cluster = max(1, (target + len(CLUSTERS) - 1) // len(CLUSTERS))
    for cluster, queries in CLUSTERS.items():
        cluster_repo_count = 0
        for query in queries:
            queries_run.append({"cluster": cluster, "query": query})
            for page in range(1, 11):
                payload = gh_json(
                    "/search/repositories",
                    {"q": query, "per_page": "100", "page": str(page)},
                )
                items = payload.get("items", [])
                if not items:
                    break
                for repo in items:
                    owner = repo.get("owner") or {}
                    if owner.get("type") != "User":
                        continue
                    login = owner["login"]
                    row = developers.setdefault(
                        login,
                        {
                            "github_login": login,
                            "github_url": owner["html_url"],
                            "clusters": set(),
                            "repositories": [],
                        },
                    )
                    row["clusters"].add(cluster)
                    if not any(r["url"] == repo["html_url"] for r in row["repositories"]):
                        row["repositories"].append(
                            {
                                "name": repo["full_name"],
                                "url": repo["html_url"],
                                "description": repo.get("description"),
                                "stars": repo.get("stargazers_count", 0),
                                "updated_at": repo.get("updated_at"),
                                "cluster": cluster,
                            }
                        )
                        repo_count += 1
                        cluster_repo_count += 1
                        if repo_count >= target or cluster_repo_count >= per_cluster:
                            break
                if len(items) < 100 or repo_count >= target or cluster_repo_count >= per_cluster:
                    break
            if repo_count >= target or cluster_repo_count >= per_cluster:
                break
        if repo_count >= target:
            break
    return developers, queries_run


def enrich(developers):
    logins = list(developers)
    for offset in range(0, len(logins), 40):
        batch = logins[offset : offset + 40]
        aliases = []
        for index, login in enumerate(batch):
            safe = login.replace("\\", "\\\\").replace('"', '\\"')
            aliases.append(
                f'u{index}:user(login:"{safe}")'
                "{databaseId login name url twitterUsername websiteUrl bio company "
                "location isHireable followers{totalCount} repoStats:repositories{totalCount}}"
            )
        result = subprocess.run(
            ["gh", "api", "graphql", "-f", "query=query{" + " ".join(aliases) + "}"],
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "GitHub GraphQL request failed")
        data = json.loads(result.stdout)["data"]
        for index, login in enumerate(batch):
            profile = data.get(f"u{index}")
            if profile:
                developers[login].update(profile)
        if offset and offset % 400 == 0:
            time.sleep(1)
    return developers


def serializable_rows(developers):
    rows = []
    for login, row in developers.items():
        repos = sorted(row["repositories"], key=lambda r: r["stars"], reverse=True)
        rows.append(
            {
                **row,
                "clusters": sorted(row["clusters"]),
                "repositories": repos,
                "name": row.get("name") or login,
                "x_handle": row.get("twitterUsername") or None,
                "x_url": "https://x.com/" + row["twitterUsername"].lstrip("@")
                if row.get("twitterUsername")
                else None,
                "x_confidence": 100 if row.get("twitterUsername") else 0,
                "x_evidence": row.get("url") if row.get("twitterUsername") else None,
            }
        )
    return sorted(rows, key=lambda r: r["github_login"].casefold())


def write_stage(folder, rows, queries):
    folder.mkdir(parents=True, exist_ok=True)
    stamp = now()
    (folder / "developers.json").write_text(
        json.dumps({"collected_at": stamp, "rows": rows}, indent=2, ensure_ascii=False)
    )
    (folder / "queries.json").write_text(
        json.dumps({"collected_at": stamp, "queries": queries}, indent=2)
    )
    columns = (
        "github_login",
        "name",
        "github_url",
        "x_handle",
        "x_url",
        "x_confidence",
        "clusters",
        "company",
        "location",
        "websiteUrl",
        "followers",
        "repository_count",
        "top_repository",
    )
    with (folder / "developers.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "github_login": row["github_login"],
                    "name": row["name"],
                    "github_url": row["github_url"],
                    "x_handle": row["x_handle"],
                    "x_url": row["x_url"],
                    "x_confidence": row["x_confidence"],
                    "clusters": "|".join(row["clusters"]),
                    "company": row.get("company"),
                    "location": row.get("location"),
                    "websiteUrl": row.get("websiteUrl"),
                    "followers": (row.get("followers") or {}).get("totalCount"),
                    "repository_count": (row.get("repoStats") or {}).get("totalCount"),
                    "top_repository": row["repositories"][0]["url"]
                    if isinstance(row.get("repositories"), list) and row["repositories"]
                    else None,
                }
            )
    handoff = []
    for row in rows:
        for repo in row["repositories"]:
            handoff.append(
                {
                    "index_id": stable("github_agent", repo["url"]),
                    "source_name": "github_public_search",
                    "directory": repo["cluster"],
                    "source_url": repo["url"],
                    "name": repo["name"].split("/", 1)[-1],
                    "description": repo.get("description"),
                    "website_url": repo["url"],
                    "repository_url": repo["url"],
                    "updated_at": repo.get("updated_at"),
                    "owner_developer_evidence": {
                        "relationship": "repository_owner",
                        "github_login": row["github_login"],
                        "github_url": row["github_url"],
                        "display_name": row["name"],
                        "x_handle": row.get("x_handle"),
                        "x_url": row.get("x_url"),
                        "x_evidence": row.get("x_evidence"),
                    },
                    "classification": {
                        "agent_audience": "personal_agents",
                        "developer_cohort": "solo_development",
                        "review_status": "review_needed",
                    },
                }
            )
    (folder / "agent-discovery-handoff.json").write_text(
        json.dumps(
            {"exported_at": stamp, "record_count": len(handoff), "records": handoff},
            indent=2,
            ensure_ascii=False,
        )
    )


def find_person(conn, row):
    found = conn.execute(
        "SELECT rp.person_id FROM relationship_contacts rc "
        "JOIN relationship_profiles rp ON rp.profile_id=rc.profile_id "
        "WHERE rc.channel='github' AND lower(rc.address)=lower(?) "
        "AND rp.person_id IS NOT NULL",
        (row["github_url"],),
    ).fetchone()
    if found:
        return found[0]
    if row.get("x_url"):
        found = conn.execute(
            "SELECT person_id FROM contact_points WHERE contact_type='x' "
            "AND lower(normalized_value)=lower(?)",
            ("x.com/" + row["x_handle"].lstrip("@"),),
        ).fetchone()
        if found:
            return found[0]
    return stable("person", "github:" + row["github_login"].casefold())


def import_rows(rows, chunk_size=25):
    observed = now()
    totals = defaultdict(int)
    for start in range(0, len(rows), chunk_size):
        with connect(CANONICAL) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO crm_tags(tag_id,label,kind) VALUES(?,?,?)",
                ("solo_development", "solo development", "role"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO crm_tags(tag_id,label,kind) VALUES(?,?,?)",
                ("open_source_developers", "open source developers", "audience"),
            )
            for row in rows[start : start + chunk_size]:
                pid = find_person(conn, row)
                exists = conn.execute("SELECT 1 FROM people WHERE person_id=?", (pid,)).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO people(person_id,full_name,normalized_name,current_role,"
                        "founder_status,identity_status,research_status,confidence,notes) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            pid,
                            row["name"],
                            row["name"].casefold().strip(),
                            "Solo developer of personal agents",
                            "unknown",
                            "single_source",
                            "review_needed",
                            80,
                            json.dumps(
                                {
                                    "cohort": "solo_development",
                                    "clusters": row["clusters"],
                                    "github_login": row["github_login"],
                                    "top_repositories": row["repositories"][:3],
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                    totals["people_created"] += 1
                else:
                    totals["people_reused"] += 1
                profile = "human:" + pid
                conn.execute(
                    "INSERT OR IGNORE INTO relationship_profiles"
                    "(profile_id,person_id,audience,evidence,reviewer,updated_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        profile,
                        pid,
                        "assistant_developers",
                        row["github_url"],
                        "github_solo_import",
                        observed,
                    ),
                )
                source_id = stable("src", row["github_url"])
                conn.execute(
                    "INSERT OR IGNORE INTO sources(source_id,url,title,publisher,source_type,"
                    "quality_tier,accessed_at,notes) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        source_id,
                        row["github_url"],
                        row["github_login"] + " GitHub profile",
                        "github.com",
                        "official_site",
                        1,
                        observed,
                        "Public developer-authored profile",
                    ),
                )
                github_contact = stable("rel_contact", profile + "|github|" + row["github_url"])
                conn.execute(
                    "INSERT OR IGNORE INTO relationship_contacts"
                    "(contact_id,profile_id,channel,address,availability,provider,source_url,"
                    "last_verified_at,updated_at,supports_dm) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        github_contact,
                        profile,
                        "github",
                        row["github_url"],
                        "available",
                        "GitHub",
                        row["github_url"],
                        observed,
                        observed,
                        "unknown",
                    ),
                )
                totals["github_contacts"] += 1
                for repo in row["repositories"]:
                    existing_agent = conn.execute(
                        "SELECT agent_id FROM agents WHERE lower(website_url)=lower(?)",
                        (repo["url"],),
                    ).fetchone()
                    agent_id = existing_agent[0] if existing_agent else stable("agent", repo["url"])
                    agent_name = repo["name"].split("/", 1)[-1]
                    conn.execute(
                        "INSERT OR IGNORE INTO agents(agent_id,canonical_name,normalized_name,"
                        "agent_kind,description,website_url,status,research_status,confidence,notes) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            agent_id,
                            agent_name,
                            agent_name.casefold(),
                            "agent",
                            repo.get("description"),
                            repo["url"],
                            "active",
                            "review_needed",
                            75,
                            json.dumps(
                                {
                                    "cohort": "personal_agents",
                                    "cluster": repo["cluster"],
                                    "github_owner": row["github_login"],
                                    "stars": repo["stars"],
                                    "updated_at": repo["updated_at"],
                                }
                            ),
                        ),
                    )
                    totals["agents_created_or_reused"] += 1
                    agent_profile = "agent:" + agent_id
                    conn.execute(
                        "INSERT OR IGNORE INTO relationship_profiles"
                        "(profile_id,agent_id,audience,owner_person_id,evidence,reviewer,updated_at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (
                            agent_profile,
                            agent_id,
                            "personal_agents",
                            pid,
                            repo["url"],
                            "github_solo_import",
                            observed,
                        ),
                    )
                    repo_source_id = stable("src", repo["url"])
                    conn.execute(
                        "INSERT OR IGNORE INTO sources(source_id,url,title,publisher,source_type,"
                        "quality_tier,accessed_at,notes) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            repo_source_id,
                            repo["url"],
                            repo["name"] + " GitHub repository",
                            "github.com",
                            "official_site",
                            1,
                            observed,
                            "Public source repository for personal agent cohort",
                        ),
                    )
                if row.get("x_url"):
                    x_source_id = stable("src", row["github_url"] + "#twitterUsername")
                    x_source_url = row["github_url"] + "?tab=overview"
                    conn.execute(
                        "INSERT OR IGNORE INTO sources(source_id,url,title,publisher,source_type,"
                        "quality_tier,accessed_at,notes) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            x_source_id,
                            x_source_url,
                            row["github_login"] + " GitHub-declared X identity",
                            "github.com",
                            "official_site",
                            1,
                            observed,
                            "X handle supplied in GitHub twitterUsername field",
                        ),
                    )
                    x_source_id = conn.execute(
                        "SELECT source_id FROM sources WHERE url=?", (x_source_url,)
                    ).fetchone()[0]
                    normalized_x = "x.com/" + row["x_handle"].lstrip("@").casefold()
                    cp_id = stable("contact", pid + "|x|" + normalized_x)
                    conn.execute(
                        "INSERT OR IGNORE INTO contact_points"
                        "(contact_id,person_id,contact_type,value,normalized_value,verification_status,"
                        "verification_method,confidence,is_primary,is_public,source_id,first_seen_at,"
                        "last_verified_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            cp_id,
                            pid,
                            "x",
                            row["x_url"],
                            normalized_x,
                            "confirmed",
                            "github_profile_twitter_username",
                            100,
                            1,
                            1,
                            x_source_id,
                            observed,
                            observed,
                            "Direct public GitHub profile field; no fuzzy match",
                        ),
                    )
                    cp_id = conn.execute(
                        "SELECT contact_id FROM contact_points WHERE person_id=? "
                        "AND contact_type='x' AND lower(normalized_value)=lower(?)",
                        (pid, normalized_x),
                    ).fetchone()[0]
                    x_contact = stable("rel_contact", profile + "|x|" + row["x_url"])
                    conn.execute(
                        "INSERT OR IGNORE INTO relationship_contacts"
                        "(contact_id,profile_id,channel,address,availability,provider,source_url,"
                        "last_verified_at,legacy_contact_id,updated_at,supports_dm) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            x_contact,
                            profile,
                            "x",
                            row["x_url"],
                            "available",
                            "X",
                            x_source_url,
                            observed,
                            cp_id,
                            observed,
                            "unknown",
                        ),
                    )
                    totals["x_contacts"] += 1
                for tag in ("solo_development", "open_source_developers"):
                    prior = conn.execute(
                        "SELECT action FROM person_tag_events WHERE person_id=? AND tag_id=? "
                        "ORDER BY event_id DESC LIMIT 1",
                        (pid, tag),
                    ).fetchone()
                    if not prior or prior[0] != "add":
                        conn.execute(
                            "INSERT INTO person_tag_events(person_id,tag_id,action,evidence,reviewer) "
                            "VALUES(?,?,?,?,?)",
                            (
                                pid,
                                tag,
                                "add",
                                "Public GitHub agent-project cohort: " + row["github_url"],
                                "github_solo_import",
                            ),
                        )
                        totals["tag_events"] += 1
    return dict(totals)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=int, default=10000)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--from-stage", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.from_stage:
        rows = json.loads((args.run / "developers.json").read_text())["rows"]
        queries = [
            {"cluster": cluster, "query": query}
            for cluster, cluster_queries in CLUSTERS.items()
            for query in cluster_queries
        ]
        write_stage(args.run, rows, queries)
    else:
        developers, queries = collect(args.target)
        rows = serializable_rows(enrich(developers))
        write_stage(args.run, rows, queries)
    result = {
        "run": str(args.run.resolve()),
        "developers": len(rows),
        "agents": sum(len(r["repositories"]) for r in rows),
        "with_confirmed_x": sum(bool(r.get("x_url")) for r in rows),
        "clusters": {cluster: sum(cluster in r["clusters"] for r in rows) for cluster in CLUSTERS},
        "applied": False,
    }
    if args.apply:
        result["import"] = import_rows(rows)
        result["applied"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
