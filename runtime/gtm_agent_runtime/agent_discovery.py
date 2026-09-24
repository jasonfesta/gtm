"""Import one agent-index batch and create its CRM handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import daily_index, directory_crawler, portkey

AUDIENCES = {"research_agents", "assistant_agents", "personal_agents"}
KINDS = {"agent", "infrastructure_provider", "irrelevant"}
CAPABILITY_KEYS = {
    "can_receive_requests",
    "can_call_external_apis",
    "can_connect_mcp",
    "can_install_integrations",
    "owner_approval_required",
}


def capability_value(value):
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return value if value in ("yes", "no", "unknown") else "unknown"


ROUTE_FIELDS = {
    "channel",
    "address",
    "source_url",
    "verification_status",
    "verified_at",
    "availability",
    "last_verified_at",
    "provider",
    "agent_card_url",
    "authentication_requirements",
    "supported_interactions",
    "supports_dm",
    "agent_operated",
    "agent_operated_evidence",
    "agent_operated_source_url",
    "agent_operated_evidence_url",
    "agent_operated_verified_at",
}
ROUTE_CHANNELS = {
    "agent_email",
    "agentdm",
    "masumi",
    "agentlist",
    "a2a",
    "agents_breakroom",
    "moltbook",
    "discord",
    "webmcp",
}


def supported_route(route):
    return bool(
        route.get("channel") in ROUTE_CHANNELS and route.get("address") and route.get("source_url")
    )


def verified_route(route):
    if not supported_route(route):
        return False
    when = route.get("verified_at") or route.get("last_verified_at")
    try:
        parsed = directory_crawler.parse_time(when)
        return bool(
            parsed
            and parsed.utcoffset() is not None
            and parsed <= datetime.now(timezone.utc)
            and route.get("verification_status") in {"published", "confirmed"}
        )
    except (TypeError, ValueError):
        return False


def supported_owner(owner):
    return bool(
        isinstance(owner, dict)
        and owner.get("relationship") in {"owner", "creator", "founder"}
        and (owner.get("person_id") or owner.get("name"))
        and owner.get("source_url")
        and owner.get("evidence")
    )


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_id(prefix, value):
    digest = hashlib.sha256(str(value).encode()).hexdigest()[:20]
    return f"{prefix}_{digest}"


def canonical_url(value):
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = parsed.netloc.casefold().removeprefix("www.")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.casefold()}://{host}{path}"


def load_json(path, default):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def normalize_source_record(record, metadata=None):
    """Map known index exports into the shared discovery fields."""
    metadata = metadata or {}
    normalized = dict(record)
    if record.get("skill_identifier"):
        normalized.setdefault("index_id", record["skill_identifier"])
        normalized.setdefault("source_name", "hermes_skills")
        normalized.setdefault("name", record.get("agent_name"))
        normalized.setdefault("description", record.get("skill_description"))
        normalized.setdefault("repository_url", record.get("repo_url"))
        normalized.setdefault("website_url", record.get("website") or record.get("repo_url"))
    normalized.setdefault("source_url", metadata.get("source"))
    normalized.setdefault(
        "catalog_observed_at",
        metadata.get("frozen_at") or metadata.get("catalog_generated_at"),
    )
    return normalized


def load_input_records(path):
    payload = load_json(path, [])
    if isinstance(payload, list):
        return [normalize_source_record(record) for record in payload]
    records = (
        payload.get("items")
        or payload.get("agents")
        or payload.get("rows")
        or payload.get("records")
        or []
    )
    return [normalize_source_record(record, payload) for record in records]


def verify_export_activity(records, client, *, days=30, as_of=None):
    """Refresh developer activity for exports with GitHub push timestamps."""
    as_of = as_of or datetime.now(timezone.utc)
    cutoff = as_of - timedelta(days=days)
    active = []
    for record in records:
        pushed_at = directory_crawler.parse_time(record.get("updated_at"))
        if not pushed_at or pushed_at < cutoff:
            continue
        slug = directory_crawler.repo_slug(record.get("repository_url"))
        if not slug:
            continue
        commit = client.latest_commit(slug)
        if not commit:
            continue
        commit_at = directory_crawler.parse_time(
            ((commit.get("commit") or {}).get("author") or {}).get("date")
        )
        author = commit.get("author") or {}
        if not commit_at or commit_at < cutoff or not author.get("login"):
            continue
        active.append(
            {
                **record,
                "developer_activity_evidence": {
                    "github_login": author["login"],
                    "github_url": author.get("html_url"),
                    "commit_url": commit.get("html_url"),
                    "commit_at": commit_at.isoformat().replace("+00:00", "Z"),
                },
            }
        )
    return active


def existing_agents(paths):
    if not paths:
        return []
    paths = [paths] if isinstance(paths, str) else paths
    agents = []
    for path in paths:
        if not Path(path).is_file():
            raise ValueError("configured agent snapshot is missing: " + str(path))
        payload = load_json(path, [])
        if isinstance(payload, dict):
            agents.extend(payload.get("records") or payload.get("agents") or [])
        else:
            agents.extend(payload)
    return agents


def match_agent(record, agents):
    index_id = str(record.get("index_id") or record.get("id") or "").strip()
    websites = {
        canonical_url(record.get(key))
        for key in ("website_url", "canonical_url", "repository_url")
        if record.get(key)
    }
    for agent in agents:
        if index_id and str(agent.get("index_id") or "") == index_id:
            return agent
    for agent in agents:
        if websites.intersection(
            canonical_url(agent.get(key))
            for key in ("website_url", "canonical_url", "repository_url")
            if agent.get(key)
        ):
            return agent
    return None


def normalize_decision(decision):
    if not isinstance(decision, dict):
        raise ValueError("classification must be an object")
    kind = decision.get("kind")
    audience = decision.get("audience")
    if kind not in KINDS:
        raise ValueError("classification kind is invalid")
    if kind == "agent" and audience not in AUDIENCES:
        raise ValueError("agent audience is invalid")
    if kind != "agent":
        audience = None
    capabilities = decision.get("capabilities") or {}
    routes = decision.get("routes") or []
    owner_developer = decision.get("owner_developer")
    owner_approval_required = capability_value(decision.get("owner_approval_required"))
    if owner_approval_required != "unknown" and not (
        decision.get("owner_approval_evidence") and decision.get("owner_approval_source_url")
    ):
        owner_approval_required = "unknown"
    if not isinstance(capabilities, dict):
        raise ValueError("capabilities must be an object")
    if not isinstance(routes, list) or any(not isinstance(route, dict) for route in routes):
        raise ValueError("routes must be a list of objects")
    if owner_developer is not None and not isinstance(owner_developer, dict):
        raise ValueError("owner_developer must be an object or null")
    normalized_routes = []
    for route in routes:
        normalized_route = dict(route)
        proof_url = route.get("agent_operated_evidence_url") or route.get(
            "agent_operated_source_url"
        )
        proof_time = route.get("agent_operated_verified_at") or route.get("verified_at")
        try:
            parsed_url = urlsplit(proof_url or "")
            parsed_time = directory_crawler.parse_time(proof_time)
            valid_proof = (
                type(route.get("agent_operated")) is bool
                and parsed_url.scheme == "https"
                and parsed_url.hostname
                and parsed_time
                and parsed_time.utcoffset() is not None
                and parsed_time <= datetime.now(timezone.utc)
            )
        except (ValueError, TypeError):
            valid_proof = False
        if valid_proof:
            normalized_route["agent_operated_evidence_url"] = proof_url
            normalized_route["agent_operated_verified_at"] = proof_time
        else:
            normalized_route["agent_operated"] = None
            for key in (
                "agent_operated_evidence",
                "agent_operated_source_url",
                "agent_operated_evidence_url",
                "agent_operated_verified_at",
            ):
                normalized_route.pop(key, None)
        normalized_routes.append(normalized_route)
    return {
        "kind": kind,
        "audience": audience,
        "darwin_fit": str(decision.get("darwin_fit") or "").strip(),
        "capabilities": {
            **{
                key: capability_value(value)
                for key, value in capabilities.items()
                if key in CAPABILITY_KEYS and key != "owner_approval_required"
            },
            "owner_approval_required": capability_value(owner_approval_required),
        },
        "routes": normalized_routes,
        "owner_approval_required": owner_approval_required,
        "owner_approval_evidence": decision.get("owner_approval_evidence")
        if owner_approval_required != "unknown"
        else None,
        "owner_approval_source_url": decision.get("owner_approval_source_url")
        if owner_approval_required != "unknown"
        else None,
        "owner_developer": owner_developer,
    }


def process_batch(records, agents, classifier, *, agent_limit=None):
    changes = []
    review = []
    processed = []
    qualified_count = 0
    unique = {}
    for record in records:
        index_id = str(record.get("index_id") or record.get("id") or "").strip()
        if not index_id:
            review.append({"record": record, "reason": "missing index ID"})
            continue
        identity = (
            canonical_url(
                record.get("repository_url")
                or record.get("website_url")
                or record.get("canonical_url")
            )
            or index_id
        )
        if identity in unique:
            continue
        unique[identity] = record

    for record in unique.values():
        index_id = str(record.get("index_id") or record.get("id"))
        processed.append(record)
        try:
            decision = normalize_decision(classifier(record))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            review.append({"index_id": index_id, "record": record, "reason": str(exc)})
            continue
        match = match_agent(record, agents)
        agent_id = (
            (match or {}).get("agent_id")
            or (match or {}).get("entity_id")
            or stable_id("agt", index_id)
        )
        change = {
            "change_id": stable_id("agent_change", index_id),
            "action": "update" if match else "create",
            "agent_id": agent_id,
            "index_id": index_id,
            "name": record.get("name"),
            "description": record.get("description"),
            "website_url": record.get("website_url") or record.get("canonical_url"),
            "source_url": record.get("source_url") or record.get("canonical_url"),
            "source_updated_at": record.get("updated_at"),
            "evidence": {key: value for key, value in record.items() if key != "decision"},
            **decision,
        }
        changes.append(change)
        if change["kind"] == "agent":
            qualified_count += 1
        if agent_limit is not None and qualified_count >= agent_limit:
            break
    return changes, review, processed


def process(records, agents, classifier):
    changes, review, _ = process_batch(records, agents, classifier)
    return changes, review


def metrics(records, changes, review):
    agents = [change for change in changes if change["kind"] == "agent"]
    return {
        "records_read": len(records),
        "new_agents": sum(change["action"] == "create" for change in agents),
        "updated_agents": sum(change["action"] == "update" for change in agents),
        "qualified_agents": len(agents),
        "research_agents": sum(change["audience"] == "research_agents" for change in agents),
        "assistant_agents": sum(change["audience"] == "assistant_agents" for change in agents),
        "personal_agents": sum(change["audience"] == "personal_agents" for change in agents),
        "infrastructure": sum(change["kind"] == "infrastructure_provider" for change in changes),
        "reachable_agents": sum(
            any(verified_route(route) for route in change["routes"]) for change in agents
        ),
        "route_holds": sum(
            not supported_route(route) for change in agents for route in change["routes"]
        ),
        "human_links": sum(supported_owner(change["owner_developer"]) for change in agents),
        "contributor_evidence": sum(
            bool(
                change["evidence"].get("owner_developer_evidence")
                or change["evidence"].get("developer_activity_evidence")
            )
            for change in agents
        ),
        "owner_link_holds": sum(
            bool(change["owner_developer"]) and not supported_owner(change["owner_developer"])
            for change in agents
        ),
        "needs_review": len(review),
    }


def crm_operations(changes):
    operations = []
    for change in changes:
        base = {
            key: value
            for key, value in change.items()
            if key not in {"capabilities", "routes", "owner_developer"}
        }
        if change["kind"] != "agent":
            continue
        operations.append({"action": "agent_upsert", "payload": base})
        operations.append(
            {
                "action": "relationship_classify",
                "payload": {
                    "agent_id": change["agent_id"],
                    "audience": change["audience"],
                    # Missing evidence must not erase an existing known capability.
                    "capabilities": {
                        key: value
                        for key, value in change["capabilities"].items()
                        if value != "unknown"
                    },
                    "source_url": change["source_url"],
                },
            }
        )
        for route in change["routes"]:
            if not supported_route(route):
                continue
            route = {key: value for key, value in route.items() if key in ROUTE_FIELDS}
            if not verified_route(route):
                route["availability"] = "unknown"
                route.pop("verified_at", None)
                route.pop("last_verified_at", None)
            operations.append(
                {
                    "action": "contact_upsert",
                    "payload": {
                        "agent_id": change["agent_id"],
                        **route,
                        "source_url": route.get("source_url") or change["source_url"],
                    },
                }
            )
        if supported_owner(change["owner_developer"]):
            operations.append(
                {
                    "action": "agent_owner_link",
                    "payload": {
                        "agent_id": change["agent_id"],
                        **change["owner_developer"],
                    },
                }
            )
    return operations


def run(config, *, records=None, classifier=None, source_results=None, agent_limit=None):
    started_at = now()
    state_dir = Path(config.get("state_dir", "data/agent-discovery"))
    cursor_path = state_dir / "cursor.json"
    prior_cursor = load_json(cursor_path, {}).get("cursor")
    if records is None:
        records, next_cursor = daily_index.read_all(config["index"], prior_cursor)
    else:
        next_cursor = config.get("fixture_next_cursor", prior_cursor)
    snapshot_paths = config.get("existing_agents_files") or config.get("existing_agents_file")
    if config.get("require_existing_snapshot") and not snapshot_paths:
        raise ValueError("Ops agent snapshot is required before CRM matching")
    agents = existing_agents(snapshot_paths)
    if classifier:
        classify = classifier
    elif records is not None and all("decision" in record for record in records):

        def classify(record):
            return record["decision"]
    else:
        if not portkey.credential(config.get("portkey", {})):
            raise ValueError("Portkey credential is missing for unclassified records")

        def classify(record):
            return portkey.classify(record, config["portkey"])

    changes, review, processed = process_batch(records, agents, classify, agent_limit=agent_limit)
    record_ids = "|".join(
        sorted(str(record.get("index_id") or record.get("id") or "") for record in processed)
    )
    run_id = stable_id("agent_discovery", f"{prior_cursor}|{next_cursor}|{record_ids}")
    summary = {
        "run_id": run_id,
        "started_at": started_at,
        "completed_at": now(),
        "source_cursor": next_cursor,
        **metrics(processed, changes, review),
    }
    if source_results is not None:
        summary["sources_crawled"] = len(source_results)
        summary["source_results"] = source_results
    if agent_limit is not None:
        summary["target_agents"] = agent_limit
        summary["target_shortfall"] = max(0, agent_limit - summary["qualified_agents"])
    crm_handoff = {
        "handoff_id": run_id,
        "source_agent": "agent discovery agent",
        "operations": crm_operations(changes),
        "needs_review": review,
        "summary": summary,
    }
    posthog_handoff = {
        "source_agent": "agent discovery agent",
        "run_id": run_id,
        "event": "agent_discovery_completed",
        "occurred_at": summary["completed_at"],
        "properties": {
            key: value
            for key, value in summary.items()
            if key not in {"started_at", "completed_at", "source_cursor"}
        },
    }
    posthog_handoff = {
        "schema_version": 1,
        "source_agent": "agent discovery agent",
        "run_id": run_id,
        "events": [
            {
                "event": posthog_handoff["event"],
                "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "darwin:" + run_id)),
                "timestamp": posthog_handoff["occurred_at"],
                "properties": {
                    **posthog_handoff["properties"],
                    "distinct_id": "agent discovery agent",
                    "source_agent": "agent discovery agent",
                },
            }
        ],
    }
    output_dir = state_dir / "runs" / run_id
    crm_path = output_dir / f"{run_id}.crm.json"
    posthog_path = output_dir / f"{run_id}.posthog.json"
    write_json(output_dir / "classifications.json", {"changes": changes, "needs_review": review})
    write_json(crm_path, crm_handoff)
    write_json(output_dir / "run-summary.json", summary)
    write_json(posthog_path, posthog_handoff)
    write_json(
        cursor_path,
        {
            "cursor": next_cursor,
            "run_id": run_id,
            "updated_at": summary["completed_at"],
        },
    )
    return {
        "crm_handoff": str(crm_path.resolve()),
        "posthog_handoff": str(posthog_path.resolve()),
        "summary": str(output_dir / "run-summary.json"),
        "_processed_ids": [str(record.get("index_id") or record.get("id")) for record in processed],
        "_qualified_ids": [change["index_id"] for change in changes if change["kind"] == "agent"],
        **summary,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--input", help="JSON batch exported from an external index")
    parser.add_argument("--sources", help="Directory source registry to crawl")
    parser.add_argument(
        "--verify-activity",
        action="store_true",
        help="Verify recent GitHub commit activity for exported records",
    )
    parser.add_argument(
        "--repo",
        action="append",
        help="With --input, include only this GitHub owner/repo (repeatable)",
    )
    parser.add_argument("--fixture", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("--once is required")
    config = load_json(args.config, {})
    input_path = args.input or args.fixture
    source_results = None
    checkpoint = None
    checkpoint_state = None
    if args.sources:
        source_config = load_json(args.sources, {})
        if not portkey.credential(config.get("portkey", {})):
            parser.error("Portkey credential is missing for a live directory run")
        checkpoint = (
            Path(config.get("state_dir", "data/agent-discovery")) / "directory-checkpoint.json"
        )
        source_config["github_credentials_file"] = config.get(
            "github_credentials_file", source_config.get("github_credentials_file")
        )
        client = directory_crawler.Client(
            token_env=source_config.get("github_token_env", "GITHUB_TOKEN"),
            credentials_file=source_config.get("github_credentials_file"),
            timeout=source_config.get("timeout_seconds", 30),
            request_limit=int(source_config.get("max_http_requests", 1500)),
        )
        records, source_results, checkpoint_state = directory_crawler.crawl(
            source_config, checkpoint, client=client
        )
        if config.get("enrich_public_readme", True):
            records = directory_crawler.enrich_public_evidence(records, client)
        agent_limit = min(
            int(source_config.get("daily_target", 250)),
            int(source_config.get("total_target", 600))
            - len(checkpoint_state.get("_qualified", [])),
        )
    else:
        records = load_input_records(input_path) if input_path else None
        agent_limit = None
        if args.repo:
            wanted = {slug.casefold() for slug in args.repo}
            records = [
                record
                for record in records or []
                if (directory_crawler.repo_slug(record.get("repository_url")) or "").casefold()
                in wanted
            ]
        if args.verify_activity:
            if records is None:
                parser.error("--verify-activity requires --input")
            before = len(records)
            client = directory_crawler.Client(
                token_env=config.get("github_token_env", "GITHUB_TOKEN"),
                credentials_file=config.get("github_credentials_file"),
            )
            records = verify_export_activity(
                records,
                client,
                days=int(config.get("active_developer_days", 30)),
            )
            source_results = [
                {
                    "source_id": "export_activity",
                    "inspected": before,
                    "selected": len(records),
                }
            ]
    result = run(
        config,
        records=records,
        source_results=source_results,
        agent_limit=agent_limit,
    )
    processed_ids = result.pop("_processed_ids")
    qualified_ids = result.pop("_qualified_ids")
    if checkpoint is not None:
        directory_crawler.commit(
            checkpoint,
            checkpoint_state,
            processed_ids=processed_ids,
            qualified_ids=qualified_ids,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
