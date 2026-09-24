"""Simple manual Human Discovery handoffs built on search-watcher normalizers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from crm.contact_normalization import norm
from crm.hacker_news_discovery import normalize_hacker_news
from crm.human_discovery_search import (
    ACTORS,
    normalize_apollo,
    normalize_linkedin,
    normalize_ph,
    normalize_x,
)
from crm.human_source_providers import (
    REDDIT_ACTOR,
    completed_apify_items,
    reddit_search_input,
    reddit_thread_url,
)
from crm.local_secrets import apify_token

SOURCES = ("x", "reddit", "linkedin", "hacker_news", "product_hunt", "apollo")
DAILY_TARGET = 200
FOUR_HOUR_BATCHES_PER_DAY = 6
BATCH_TARGET = (DAILY_TARGET + FOUR_HOUR_BATCHES_PER_DAY - 1) // FOUR_HOUR_BATCHES_PER_DAY

# The 23 tools visible in Darwin's sidebar, with slash-paired names searchable
# independently. Keep this separate from the hourly watcher's rotating catalog.
SEARCH_TERMS = (
    "ChatGPT",
    "Claude",
    "Grok",
    "Perplexity",
    "Muse Code",
    "Cursor",
    "GitHub Copilot",
    "VS Code",
    "Gemini CLI",
    "Google Antigravity",
    "Grok Build",
    "Devin",
    "Replit Agent",
    "Windsurf",
    "OpenCode",
    "Hermes Agent",
    "OpenClaw",
    "OpenAI Agents SDK",
    "Anthropic SDK",
    "OpenRouter Agent SDK",
    "Vercel AI SDK",
    "Google ADK",
    "LangChain",
    "LangGraph",
    "Pydantic AI",
)
COLLECT_ITEMS_PER_SOURCE = 100
REPORT_TIMEZONE = ZoneInfo("America/New_York")
WORK_CONTEXT = re.compile(
    r"\b(?:app|apps|builder|building|business|client|code|coding|customer|"
    r"develop(?:er|ing|ment)?|design|engineer|integrat\w*|marketing|"
    r"product|project|research|sales|ship\w*|task|team|tool|plugin|"
    r"workflow|automation|agent|assistant)\b",
    re.IGNORECASE,
)
PERSONAL_ACTION = re.compile(
    r"\b(?:i|my|we|our)\b.*\b(?:use|using|tried|testing|building|built|"
    r"researching|working|wrote|made|created)\b",
    re.IGNORECASE | re.DOTALL,
)
AUTOMATED_NAME = re.compile(r"\b(?:bot|watch|alerts|news|feed|super-intel)\b", re.IGNORECASE)
CURSOR_CONTEXT = re.compile(
    r"\b(?:AI|agent|assistant|app|code|coding|developer|editor|IDE|software)\b",
    re.IGNORECASE,
)


def combined_search_query():
    """One copyable OR query for manual keyword-based discovery."""
    return "(" + " OR ".join(json.dumps(term) for term in SEARCH_TERMS) + ")"


def relevant_live_observation(item):
    """Keep observed tool use or work, not a name hit or a casual bot mention."""
    text = str(item.get("text") or "")
    source = item.get("channel")
    if AUTOMATED_NAME.search(str(item.get("name") or "")):
        return False
    matched_terms = [
        term
        for term in SEARCH_TERMS
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE)
    ]
    if source != "apollo" and not matched_terms:
        return False
    if matched_terms == ["Cursor"] and not CURSOR_CONTEXT.search(text):
        return False
    return bool(WORK_CONTEXT.search(text) or PERSONAL_ACTION.search(text))


def collect_live(
    *,
    now=None,
    max_items=COLLECT_ITEMS_PER_SOURCE,
    apify_call=None,
    apify_auth=None,
    terms=None,
    selected_sources=None,
):
    """Run one explicitly invoked keyword search; never send or schedule."""
    now = now or datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc)
    since = now - timedelta(hours=4)
    query = combined_search_query()
    terms = tuple(terms or SEARCH_TERMS[:5])
    if not terms or any(term not in SEARCH_TERMS for term in terms):
        raise ValueError("terms must come from the canonical search inventory")
    selected_sources = tuple(
        ("x", "reddit", "hacker_news", "product_hunt")
        if selected_sources is None
        else selected_sources
    )
    if any(source not in SOURCES for source in selected_sources):
        raise ValueError("unsupported discovery source")
    if not 1 <= max_items <= 100:
        raise ValueError("max_items must be between 1 and 100")
    terms = terms[:max_items]
    per_query = max(1, max_items // len(terms))
    apify_call = apify_call or completed_apify_items
    apify_auth = apify_auth if apify_auth is not None else apify_token()
    sources = {source: [] for source in SOURCES}
    runs = []
    actor_inputs = {
        "x": (
            ACTORS["x"],
            {
                "searchTerms": [query],
                "maxItems": max_items,
                "queryType": "Latest",
                "since_time": str(int(since.timestamp())),
                "until_time": str(int(now.timestamp())),
            },
        ),
        "reddit": (REDDIT_ACTOR, reddit_search_input(terms, max_items)),
        "product_hunt": (ACTORS["product_hunt"], {"date": now.date().isoformat()}),
    }
    searches = [
        (source, actor, actor_input, query if source == "x" else ", ".join(terms))
        for source, (actor, actor_input) in actor_inputs.items()
    ]
    searches.extend(
        (
            "hacker_news",
            ACTORS["hacker_news"],
            {"query": term, "contentType": "all", "sortBy": "date", "maxItems": per_query},
            term,
        )
        for term in terms
    )
    missing_reported = set()
    for source, actor, actor_input, source_query in searches:
        if source not in selected_sources:
            continue
        if not apify_auth:
            if source not in missing_reported:
                runs.append({"source": source, "error": "apify_token_missing"})
                missing_reported.add(source)
            continue
        try:
            raw_rows, provider_run_id = apify_call(actor, actor_input, auth=apify_auth)
        except (RuntimeError, urllib.error.URLError) as exc:
            runs.append({"source": source, "actor": actor, "input": actor_input, "error": str(exc)})
            continue
        runs.append(
            {
                "source": source,
                "actor": actor,
                "input": actor_input,
                "status": "SUCCEEDED",
                "provider_run_id": provider_run_id,
                "returned": len(raw_rows),
            }
        )
        for raw in raw_rows:
            if not isinstance(raw, dict):
                continue
            item = normalize(source, {**raw, "query": source_query})
            if (
                item["name"]
                and item["profile_url"]
                and item["parent_url"]
                and relevant_live_observation(item)
            ):
                sources[source].append(item)

    if "linkedin" in selected_sources:
        runs.append({"source": "linkedin", "error": "sales_navigator_manual_handoff_required"})
    if "apollo" in selected_sources:
        runs.append({"source": "apollo", "error": "validation_only_not_a_discovery_source"})

    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    return {
        "run_id": f"human-discovery-{stamp}",
        "observed_at": now.isoformat(),
        "query": query,
        "sources": sources,
        "provider_runs": runs,
    }


ASSISTANT_TERMS = (
    "assistant",
    "ai agent",
    "agentic",
    "openclaw",
    "mcp",
    "tool calling",
    "voice agent",
    "coding agent",
)
SOLO_TERMS = (
    "indie",
    "solo",
    "independent",
    "founder",
    "developer",
    "engineer",
    "builder",
    "built",
    "building",
    "shipped",
)
KNOWLEDGE_TERMS = (
    "research",
    "analyst",
    "operator",
    "operations",
    "product",
    "designer",
    "writer",
    "marketing",
    "sales",
    "consultant",
    "workflow",
)


def normalize_reddit(raw, query=""):
    author = (
        raw.get("author")
        or raw.get("author_name")
        or raw.get("authorUsername")
        or raw.get("username")
        or ""
    )
    if isinstance(author, dict):
        author = author.get("username") or author.get("name") or ""
    author = str(author).removeprefix("u/")
    permalink = reddit_thread_url(raw.get("permalink")) or reddit_thread_url(raw.get("url"))
    rid = str(raw.get("id") or raw.get("name") or "")
    title = raw.get("title") or ""
    body = raw.get("selftext") or raw.get("body") or raw.get("text") or raw.get("bodyText") or ""
    return {
        "channel": "reddit",
        "name": author,
        "profile_url": (
            f"https://www.reddit.com/user/{author}/"
            if author and author not in {"[deleted]", "AutoModerator"}
            else ""
        ),
        "parent_url": permalink,
        "text": " ".join(part for part in (title, body) if part).strip(),
        "title": title,
        "provider_id": rid,
        "query": query,
    }


NORMALIZERS = {
    "x": normalize_x,
    "reddit": normalize_reddit,
    "linkedin": normalize_linkedin,
    "hacker_news": normalize_hacker_news,
    "product_hunt": normalize_ph,
    "apollo": normalize_apollo,
}


def normalize(source, raw):
    if source not in NORMALIZERS:
        raise ValueError(f"unsupported discovery source: {source}")
    if raw.get("channel") == source and raw.get("profile_url"):
        item = dict(raw)
    else:
        item = NORMALIZERS[source](raw, str(raw.get("query") or ""))
    if source == "hacker_news":
        item["provider_id"] = str(
            raw.get("provider_id") or raw.get("id") or raw.get("objectID") or ""
        )
    item["channel"] = source
    item["name"] = str(item.get("name") or "").strip()
    item["profile_url"] = str(item.get("profile_url") or "").strip()
    item["parent_url"] = str(item.get("parent_url") or "").strip()
    item["text"] = str(item.get("text") or "").strip()
    item["provider_id"] = str(item.get("provider_id") or "").strip()
    return item


def identity_key(item):
    profile = item.get("profile_url") or ""
    if profile.startswith(("http://", "https://")):
        if item["channel"] == "hacker_news":
            return "profile:" + profile.casefold().rstrip("/")
        return "profile:" + norm(item["channel"], profile)
    provider_id = item.get("provider_id") or ""
    if provider_id:
        return f"{item['channel']}:{provider_id.casefold()}"
    source_url = item.get("parent_url") or ""
    if source_url:
        return "source:" + hashlib.sha256(source_url.encode()).hexdigest()[:24]
    return ""


def classify(item):
    supplied = str(item.get("audience") or "").strip()
    if supplied in {
        "solo developer",
        "assistant developer",
        "technology-interested knowledge worker",
    }:
        audience = supplied
    else:
        blob = " ".join(str(item.get(key) or "") for key in ("name", "title", "text")).casefold()
        if any(term in blob for term in ASSISTANT_TERMS) and any(
            term in blob for term in SOLO_TERMS
        ):
            audience = "assistant developer"
        elif any(term in blob for term in SOLO_TERMS):
            audience = "solo developer"
        elif any(term in blob for term in KNOWLEDGE_TERMS + ASSISTANT_TERMS):
            audience = "technology-interested knowledge worker"
        else:
            audience = "technology-interested knowledge worker"
    relevance = str(item.get("darwin_relevance") or item.get("reason") or "").strip()
    if not relevance:
        text = re.sub(r"\s+", " ", item.get("text") or "").strip()
        relevance = (
            f"Darwin may help with the observed {item['channel']} work: {text[:220]}"
            if text
            else f"Darwin relevance observed through {item['channel']} discovery."
        )
    return audience, relevance


def build_handoffs(payload):
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        encoded = json.dumps(payload.get("sources") or {}, sort_keys=True, separators=(",", ":"))
        run_id = "human-discovery-" + hashlib.sha256(encoded.encode()).hexdigest()[:16]
    observed_at = str(payload.get("observed_at") or datetime.now(timezone.utc).isoformat())
    source_rows = payload.get("sources") or {}
    people = {}
    source_counts = Counter()
    for source in SOURCES:
        for raw in source_rows.get(source) or []:
            item = normalize(source, raw)
            key = identity_key(item)
            if not key or not item["name"] or not item["profile_url"]:
                continue
            audience, relevance = classify(item)
            source_counts[source] += 1
            evidence = {
                "source": source,
                "source_id": item.get("provider_id") or None,
                "profile_url": item.get("profile_url") or None,
                "source_url": item.get("parent_url") or None,
                "text": item.get("text") or None,
                "query": item.get("query") or None,
            }
            if key not in people:
                people[key] = {
                    "identity_key": key,
                    "name": item["name"],
                    "profile_url": item.get("profile_url") or None,
                    "audience": audience,
                    "darwin_relevance": relevance,
                    "sources": [],
                }
            if evidence not in people[key]["sources"]:
                people[key]["sources"].append(evidence)
    records = sorted(people.values(), key=lambda row: row["identity_key"])
    audience_counts = Counter(row["audience"] for row in records)
    cursors = {
        source: (payload.get("cursors") or {}).get(source)
        for source in SOURCES
        if (payload.get("cursors") or {}).get(source) is not None
    }
    handoff_id = "human-discovery:" + run_id
    summary = {
        "observations": sum(source_counts.values()),
        "unique_people": len(records),
        "target_per_day": DAILY_TARGET,
        "target_per_four_hour_batch": BATCH_TARGET,
        "batch_target_met": len(records) >= BATCH_TARGET,
        "by_source": dict(sorted(source_counts.items())),
        "by_audience": dict(sorted(audience_counts.items())),
    }
    crm = {
        "handoff_id": handoff_id,
        "source_agent": "human discovery agent",
        "run_id": run_id,
        "observed_at": observed_at,
        "operations": [{"action": "human_upsert", "payload": row} for row in records],
        "run_summary": summary,
        "cursors": cursors,
    }
    event_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "gtm:human-discovery:" + run_id))
    posthog = {
        "schema_version": 1,
        "source_agent": "human discovery agent",
        "run_id": run_id,
        "events": [
            {
                "event": "gtm.human_discovery_run",
                "uuid": event_uuid,
                "timestamp": observed_at,
                "properties": {
                    "distinct_id": "human-discovery-agent",
                    "$process_person_profile": False,
                    "$geoip_disable": True,
                    "workspace": "gtm",
                    "source_agent": "human discovery agent",
                    "run_id": run_id,
                    **summary,
                },
            }
        ],
    }
    return crm, posthog


def from_search_collections(collections, *, reddit=None, run_id=None):
    """Turn one keyword-search batch into Human Discovery input."""
    sources = {source: [] for source in SOURCES}
    observed = []
    stamps = []
    cursors = {}
    for collection in collections:
        rows = collection.get("items")
        if rows is None:
            rows = list(collection.get("comments") or []) + list(collection.get("later_use") or [])
        for row in rows:
            channel = row.get("channel")
            if channel in sources:
                sources[channel].append(row)
        end = (collection.get("window") or {}).get("end")
        at = end or collection.get("observed_at")
        if at:
            observed.append(at)
        stamp = collection.get("hour")
        if stamp:
            stamps.append(stamp)
        cursor = end or stamp
        if cursor:
            for source in SOURCES:
                if any(row.get("channel") == source for row in rows):
                    cursors[source] = cursor
    reddit = reddit or {}
    reddit_rows = reddit.get("items") if isinstance(reddit, dict) else reddit
    sources["reddit"].extend(reddit_rows or [])
    observed_at = max(observed) if observed else datetime.now(timezone.utc).isoformat()
    stamp = max(stamps) if stamps else observed_at[:13]
    if isinstance(reddit, dict) and reddit.get("cursor") is not None:
        cursors["reddit"] = reddit["cursor"]
    return {
        "run_id": run_id or f"human-discovery-{stamp}",
        "observed_at": observed_at,
        "sources": sources,
        "cursors": cursors,
    }


def from_search_collection(collection, *, reddit=None, run_id=None):
    """Turn one existing search-watcher collection into Human Discovery input."""
    return from_search_collections([collection], reddit=reddit, run_id=run_id)


def requalify_handoff(handoff, collection_receipt):
    """Rebuild a held live batch from its recorded public evidence."""
    sources = {source: [] for source in SOURCES}
    for operation in handoff.get("operations") or []:
        person = operation.get("payload") or {}
        for evidence in person.get("sources") or []:
            source = evidence.get("source")
            if source not in sources:
                continue
            item = {
                "channel": source,
                "name": person.get("name") or "",
                "profile_url": evidence.get("profile_url") or person.get("profile_url") or "",
                "parent_url": evidence.get("source_url") or "",
                "text": evidence.get("text") or "",
                "provider_id": evidence.get("source_id") or "",
                "query": evidence.get("query") or collection_receipt.get("query") or "",
            }
            if item["name"] and item["profile_url"] and relevant_live_observation(item):
                sources[source].append(item)
    return {
        "run_id": handoff["run_id"] + "-qualified",
        "observed_at": handoff["observed_at"],
        "query": collection_receipt["query"],
        "sources": sources,
        "provider_runs": collection_receipt.get("provider_runs") or [],
    }


def write_handoffs(payload, output_dir):
    crm, posthog = build_handoffs(payload)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    crm_path = root / f"{crm['run_id']}.crm.json"
    posthog_path = root / f"{crm['run_id']}.posthog.json"
    crm_path.write_text(json.dumps(crm, indent=2, sort_keys=True) + "\n")
    posthog_path.write_text(json.dumps(posthog, indent=2, sort_keys=True) + "\n")
    paths = {"crm": str(crm_path), "posthog": str(posthog_path)}
    if "provider_runs" in payload:
        report_day = (
            datetime.fromisoformat(payload["observed_at"]).astimezone(REPORT_TIMEZONE).date()
        )
        daily_people = set()
        for prior_path in root.glob("*.crm.json"):
            prior = json.loads(prior_path.read_text())
            prior_day = (
                datetime.fromisoformat(prior["observed_at"]).astimezone(REPORT_TIMEZONE).date()
            )
            if prior_day == report_day:
                daily_people.update(
                    operation["payload"]["identity_key"]
                    for operation in prior.get("operations") or []
                )
        receipt_path = root / f"{crm['run_id']}.collection.json"
        receipt_path.write_text(
            json.dumps(
                {
                    "run_id": crm["run_id"],
                    "observed_at": payload["observed_at"],
                    "report_day": report_day.isoformat(),
                    "daily_unique_people": len(daily_people),
                    "daily_target_met": len(daily_people) >= DAILY_TARGET,
                    "query": payload.get("query") or "",
                    "provider_runs": payload["provider_runs"],
                    "run_summary": crm["run_summary"],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        paths["collection"] = str(receipt_path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--input", type=Path)
    inputs.add_argument("--search-collection", type=Path, action="append")
    inputs.add_argument("--print-query", action="store_true", help="print the combined query only")
    inputs.add_argument("--collect", action="store_true", help="run one live provider search")
    inputs.add_argument("--requalify-handoff", type=Path)
    parser.add_argument("--reddit-input", type=Path)
    parser.add_argument("--collection-receipt", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--max-items-per-source", type=int, default=COLLECT_ITEMS_PER_SOURCE)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--source", action="append", choices=SOURCES)
    parser.add_argument("--term", action="append", choices=SEARCH_TERMS)
    args = parser.parse_args()
    if args.print_query:
        print(combined_search_query())
        return
    if args.output_dir is None:
        parser.error("--output-dir is required for handoff generation")
    if args.collect:
        if args.max_items_per_source < 1 or args.max_items_per_source > 100:
            parser.error("--max-items-per-source must be between 1 and 100")
        payload = collect_live(
            max_items=args.max_items_per_source, terms=args.term, selected_sources=args.source
        )
        if args.run_id:
            payload["run_id"] = args.run_id
    elif args.requalify_handoff:
        if not args.collection_receipt:
            parser.error("--collection-receipt is required to requalify a handoff")
        payload = requalify_handoff(
            json.loads(args.requalify_handoff.read_text()),
            json.loads(args.collection_receipt.read_text()),
        )
    elif args.input:
        payload = json.loads(args.input.read_text())
    else:
        collections = [json.loads(path.read_text()) for path in args.search_collection]
        reddit = json.loads(args.reddit_input.read_text()) if args.reddit_input else None
        payload = from_search_collections(collections, reddit=reddit, run_id=args.run_id)
    print(json.dumps(write_handoffs(payload, args.output_dir), indent=2))


if __name__ == "__main__":
    main()
