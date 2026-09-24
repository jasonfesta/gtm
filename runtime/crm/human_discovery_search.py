"""Bounded search collection and reconciliation helpers for Human Discovery."""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from crm.assistant_fit import POLICY, score_post_via_portkey
from crm.database import ROOT, configured, connect
from crm.live_channels import allowed as channel_live
from crm.local_secrets import apify_token, apollo_token, persist_apify_token
from crm.publication import HELD as PUBLICATION_HELD
from crm.publication import PROCESSED as PUBLICATION_PROCESSED
from crm.publication import ingest
from crm.team_contacts import APOLLO_HEALTH, APOLLO_SEARCH

NY = ZoneInfo("America/New_York")
REPLY_AGENTS = {"x": "Human Reply Agent X", "linkedin": "Human Reply Agent LinkedIn"}

CATALOG = ROOT / "config/timeline-search-keywords.json"
DROPS = ROOT / "logs/hourly-search"
HANDOFF = DROPS / "handoff"
SEARCH_PENDING = DROPS / "pending"
PENDING = ROOT / "logs/publication-receipts/pending"
ACTORS = {
    "x": "kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest",
    "linkedin": "harvestapi/linkedin-post-search",
    "hacker_news": "mangudai/hacker-news-scraper",
    "github": "rupom888/github-repository-scraper",
    "product_hunt": "data_alchemist/producthunt-daily-scraper",
}
KEYWORD_QUERIES = 5
X_QUERIES = KEYWORD_QUERIES
LINKEDIN_QUERIES = KEYWORD_QUERIES
OTHER_QUERIES = KEYWORD_QUERIES
KEEP = 5
MAX_HITS = 50
X_ACTOR_ITEMS = 50
LOOKBACK_HOURS = 1
MAX_CHARGE_USD = 0.25
HOURLY_FAMILIES = {"A", "B"}
BUILDER_WORDS = {"developer", "dev", "engineer", "builder", "building"}
COSTS = {
    "x": 0.20 / 1000,
    "linkedin": 1.75 / 1000,
    "hacker_news": 0.09 / 1000,
    "github": 0.50 / 1000,
    "product_hunt": 0.10 / 1000,
}


def hour_stamp(now=None):
    now = now or datetime.now(timezone.utc)
    completed = now.astimezone(timezone.utc) - timedelta(hours=1)
    return completed.strftime("%Y-%m-%dT%H")


def hour_window(stamp):
    """The UTC hour on the drop. Search does not go back further."""
    end = datetime.strptime(stamp, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) + timedelta(hours=1)
    return end - timedelta(hours=LOOKBACK_HOURS), end


def parse_when(raw):
    for key in ("createdAt", "created_at", "updated_at", "postedAt", "publishedAt"):
        value = raw.get(key) if isinstance(raw, dict) else None
        if not value:
            continue
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        text = str(value).replace("Z", "+00:00")
        try:
            when = datetime.fromisoformat(text)
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when
    return None


def recent_enough(raw, start):
    when = parse_when(raw)
    return True if when is None else when >= start


def drop_path(stamp, drops=DROPS):
    return Path(drops) / (stamp + ".json")


def hour_slots(day):
    """24 UTC hours that fall on the America/New_York calendar day."""
    start = datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=NY)
    return [
        (start + timedelta(hours=i)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
        for i in range(24)
    ]


def token():
    return apify_token()


def load_catalog(path=CATALOG, local_path=None):
    """Load only explicit agent/assistant builder phrases for hourly acquisition."""
    from crm.keyword_docs import load_search_catalog

    kwargs = {"catalog_path": path}
    if local_path is not None:
        kwargs["local_path"] = local_path
    catalog = load_search_catalog(**kwargs)
    catalog["entries"] = [
        entry for entry in catalog.get("entries", []) if hourly_builder_entry(entry)
    ]
    catalog["version"] = f"{catalog.get('version', 'unknown')}-hourly-builders"
    return catalog


def hourly_builder_entry(entry):
    if entry.get("family") not in HOURLY_FAMILIES:
        return False
    words = set(re.findall(r"[a-z]+", str(entry.get("x") or "").casefold()))
    return bool(words & BUILDER_WORDS)


def phrase(entry, channel):
    if entry.get("local"):
        return (entry.get(channel) or "").strip()
    if channel in entry:
        return entry[channel]
    if channel in ("hacker_news", "github", "product_hunt", "apollo"):
        return entry.get("x") or entry["linkedin"]
    return entry["x"] if channel == "x" else entry["linkedin"]


def rotate(catalog, stamp, channel, limit):
    entries = [
        entry
        for entry in sorted(catalog["entries"], key=lambda e: (e.get("priority", 999), e["id"]))
        if phrase(entry, channel)
    ]
    if not entries:
        return []
    start = int(re.sub(r"\D", "", stamp) or "0") % len(entries)
    chosen = [entries[(start + i) % len(entries)] for i in range(limit)]
    return [{"id": e["id"], "family": e["family"], "query": phrase(e, channel)} for e in chosen]


def qualify(item, catalog=None, *, scorer=None):
    """Score live work through the configured model; injected scorers support finalization."""
    del catalog
    return (scorer or score_post_via_portkey)(item)


def normalize_x(raw, query):
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    handle = author.get("userName") or raw.get("screen_name") or ""
    tid = str(raw.get("id") or raw.get("id_str") or "")
    url = raw.get("url") or (f"https://x.com/{handle}/status/{tid}" if handle and tid else "")
    return {
        "channel": "x",
        "name": author.get("name") or handle,
        "profile_url": f"https://x.com/{handle}" if handle else "",
        "parent_url": url,
        "text": raw.get("text") or raw.get("full_text") or "",
        "provider_id": tid,
        "query": query,
    }


def normalize_linkedin(raw, query):
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    handle = author.get("publicIdentifier") or ""
    profile = author.get("linkedinUrl") or (
        f"https://www.linkedin.com/in/{handle}/" if handle else ""
    )
    return {
        "channel": "linkedin",
        "name": author.get("name") or handle,
        "profile_url": profile,
        "parent_url": raw.get("linkedinUrl") or "",
        "text": raw.get("content") or raw.get("text") or "",
        "provider_id": str(raw.get("id") or ""),
        "query": query,
    }


def normalize_hn(raw, query):
    author = raw.get("author") or ""
    oid = raw.get("id") or raw.get("objectID")
    title = raw.get("title") or ""
    body = (
        raw.get("storyText")
        or raw.get("story_text")
        or raw.get("comment_text")
        or raw.get("text")
        or ""
    )
    return {
        "channel": "hacker_news",
        "name": author,
        "profile_url": f"https://news.ycombinator.com/user?id={author}" if author else "",
        "parent_url": raw.get("hnUrl")
        or (f"https://news.ycombinator.com/item?id={oid}" if oid else raw.get("url") or ""),
        "text": (title + " " + body).strip(),
        "title": title,
        "query": query,
    }


def normalize_github(raw, query):
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    owner = raw.get("owner") if isinstance(raw.get("owner"), dict) else {}
    full = raw.get("full_name") or raw.get("fullName") or ""
    login = (
        user.get("login")
        or owner.get("login")
        or raw.get("login")
        or (full.split("/")[0] if "/" in full else "")
    )
    title = raw.get("title") or full or raw.get("name") or ""
    body = raw.get("body") or raw.get("description") or ""
    url = raw.get("html_url") or raw.get("url") or (f"https://github.com/{full}" if full else "")
    return {
        "channel": "github",
        "name": login,
        "profile_url": f"https://github.com/{login}" if login else "",
        "parent_url": url,
        "text": (title + " " + body).strip(),
        "title": title,
        "query": query,
    }


def normalize_apollo(raw, query):
    org = raw.get("organization") if isinstance(raw.get("organization"), dict) else {}
    name = raw.get("name") or f"{raw.get('first_name') or ''} {raw.get('last_name') or ''}".strip()
    linkedin = raw.get("linkedin_url") or ""
    title = raw.get("title") or raw.get("headline") or ""
    org_name = org.get("name") or ""
    apollo_id = str(raw.get("id") or "")
    identity = linkedin or (f"https://app.apollo.io/#/people/{apollo_id}" if apollo_id else "")
    return {
        "channel": "apollo",
        "name": name,
        "profile_url": identity,
        "parent_url": identity,
        "text": " ".join(part for part in (title, org_name) if part),
        "title": title,
        "query": query,
        "provider_id": apollo_id,
    }


def normalize_ph(raw, query):
    makers = raw.get("makers") or raw.get("hunters") or []
    maker = makers[0] if makers and isinstance(makers[0], dict) else {}
    username = maker.get("username") or ""
    profile = maker.get("url") or (f"https://www.producthunt.com/@{username}" if username else "")
    title = raw.get("name") or raw.get("tagline") or ""
    return {
        "channel": "product_hunt",
        "name": maker.get("name") or username,
        "profile_url": profile,
        "parent_url": raw.get("url") or raw.get("productUrl") or "",
        "text": (title + " " + (raw.get("tagline") or raw.get("description") or "")).strip(),
        "title": title,
        "query": query,
    }


def fetch_json(url, *, method="GET", payload=None, headers=None, opener=None):
    opener = opener or urllib.request.urlopen
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with opener(request) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"search request failed ({exc.code})") from None


def apify_items(actor, payload, *, opener=None, auth=None):
    auth = auth if auth is not None else token()
    if not auth:
        raise RuntimeError("APIFY_API_TOKEN required")
    actor_id = actor.replace("/", "~")
    started = fetch_json(
        f"https://api.apify.com/v2/acts/{actor_id}/runs?waitForFinish=120",
        method="POST",
        payload=payload,
        headers={"Authorization": "Bearer " + auth, "Content-Type": "application/json"},
        opener=opener,
    )
    data = started.get("data") or started
    dataset = data.get("defaultDatasetId")
    if not dataset:
        return [], data.get("id")
    items = fetch_json(
        f"https://api.apify.com/v2/datasets/{dataset}/items",
        headers={"Authorization": "Bearer " + auth},
        opener=opener,
    )
    return items if isinstance(items, list) else [], data.get("id")


def apollo_people(query, *, opener=None, auth=None):
    """Free people search. Never enrich. Never send."""
    auth = auth if auth is not None else apollo_token()
    if not auth:
        return [], "apollo_token_missing"
    try:
        data = fetch_json(
            APOLLO_SEARCH,
            method="POST",
            payload={"person_titles": [query], "page": 1, "per_page": KEEP},
            headers={"x-api-key": auth, "Content-Type": "application/json"},
            opener=opener,
        )
    except RuntimeError:
        return [], "apollo_search_failed"
    people = data.get("people") or data.get("contacts") or []
    return (people if isinstance(people, list) else []), None


def estimate(channel, count):
    return COSTS.get(channel, 0.0) * count


def write_handoff(item, judgment, stamp, folder=HANDOFF):
    channel = item["channel"]
    dest = Path(folder) / channel
    dest.mkdir(parents=True, exist_ok=True)
    key = re.sub(r"[^a-z0-9]+", "-", (item.get("provider_id") or item["parent_url"]).casefold())
    path = dest / f"{stamp}-{key[:48]}.json"
    payload = {
        "handoff_id": path.stem,
        "action": "comment",
        "hour": stamp,
        "reason": judgment["reason"],
        "do": (
            "This person is already in CRM. Reply to this post. "
            "If it is gone, open their profile and reply to one of their most recent authored posts."
        ),
        **item,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def write_outreach(item, judgment, stamp, folder=HANDOFF):
    """CRM is not enough. Watchers must find them on X or LinkedIn and comment."""
    dest = Path(folder) / "outreach"
    dest.mkdir(parents=True, exist_ok=True)
    key = re.sub(
        r"[^a-z0-9]+", "-", (item.get("parent_url") or item.get("name") or "item").casefold()
    )
    path = dest / f"{stamp}-{key[:48]}.json"
    payload = {
        "handoff_id": path.stem,
        "action": "find_and_comment",
        "hour": stamp,
        "reason": judgment.get("reason") or "assistant_agent_source",
        "do": (
            "This person is already in CRM. Prove exact X or LinkedIn identity "
            "from this source. Open their profile. Reply to one of their most "
            "recent authored posts. Do not comment on HN, GitHub, Product Hunt, or Apollo. "
            "Do not guess a handle. One network only."
        ),
        **item,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def _named(item, do):
    return {
        "name": item.get("name") or "",
        "channel": item.get("channel") or "",
        "parent_url": item.get("parent_url") or "",
        "profile_url": item.get("profile_url") or "",
        "reason": item.get("reason") or "",
        "do": do,
    }


def brief_from_payload(payload, *, path=None):
    stamp = payload.get("hour") or ""
    comments = []
    for row in payload.get("comments") or []:
        reply_agent = REPLY_AGENTS.get(row.get("channel"), "Human Reply Agent")
        item = _named(
            row,
            f"in CRM. {reply_agent} replies to this post, or one more recent. Search does not send.",
        )
        item["handoff"] = row.get("handoff")
        comments.append(item)
    later = []
    for row in payload.get("later_use") or []:
        item = _named(
            row,
            "in CRM. Watcher finds them on X or LinkedIn and replies to one of "
            "their most recent posts. Do not comment on HN, GitHub, Product Hunt, or Apollo.",
        )
        item["outreach"] = row.get("outreach")
        later.append(item)
    skipped_count = len(payload.get("skipped") or [])
    comment_names = ", ".join(f"{r['name']} ({r['channel']})" for r in comments) or "none"
    later_names = ", ".join(f"{r['name']} ({r['channel']})" for r in later) or "none"
    return {
        "hour": stamp,
        "path": str(path) if path else None,
        "missing": False,
        "queries": payload.get("queries") or {},
        "comments": comments,
        "later_use": later,
        "skipped_count": skipped_count,
        "tell_watchers": [f"{row['channel']} {row['name']} {row['parent_url']}" for row in comments]
        + [f"outreach {row['name']} {row['parent_url']}" for row in later],
        "tell_main": (
            f"Hour {stamp}: {len(comments) + len(later)} hits in CRM. "
            f"{len(comments)} X/LinkedIn post"
            f"{'' if len(comments) == 1 else 's'} to comment ({comment_names}). "
            f"{len(later)} HN/GitHub/Product Hunt/Apollo hit"
            f"{'' if len(later) == 1 else 's'} still need a recent X or LinkedIn "
            f"comment ({later_names}). {skipped_count} skipped. Search did not send."
        ),
    }


def brief(stamp=None, *, drops=DROPS, data=None):
    stamp = stamp or hour_stamp()
    dest = drop_path(stamp, drops)
    if data is not None:
        return brief_from_payload({**data, "hour": data.get("hour") or stamp}, path=dest)
    if not dest.is_file():
        return {
            "hour": stamp,
            "path": str(dest),
            "missing": True,
            "comments": [],
            "later_use": [],
            "skipped_count": 0,
            "tell_watchers": [],
            "tell_main": f"Hour {stamp}: no drop. Run search if this is the current UTC hour.",
        }
    try:
        payload = json.loads(dest.read_text())
    except json.JSONDecodeError:
        payload = {"hour": stamp}
    return brief_from_payload({**payload, "hour": payload.get("hour") or stamp}, path=dest)


def day_brief(day=None, *, now=None, drops=DROPS):
    now = now or datetime.now(timezone.utc)
    day = day or now.astimezone(NY).strftime("%Y-%m-%d")
    current = hour_stamp(now)
    comments = []
    later = []
    seen_comments = set()
    seen_later = set()
    skipped_count = 0
    hours = []
    missed = []
    remaining = []
    for stamp in hour_slots(day):
        dest = drop_path(stamp, drops)
        if dest.is_file():
            row = brief(stamp, drops=drops)
            hours.append(row)
            skipped_count += row.get("skipped_count") or 0
            for item in row.get("comments") or []:
                key = item.get("parent_url") or item.get("name")
                if key in seen_comments:
                    continue
                seen_comments.add(key)
                comments.append(item)
            for item in row.get("later_use") or []:
                key = item.get("parent_url") or item.get("name")
                if key in seen_later:
                    continue
                seen_later.add(key)
                later.append(item)
        elif stamp <= current:
            missed.append(stamp)
        else:
            remaining.append(stamp)
    return {
        "day": day,
        "hours_present": len(hours),
        "hours_expected": 24,
        "missed": missed,
        "remaining": remaining,
        "comments": comments,
        "later_use": later,
        "skipped_count": skipped_count,
        "hours": hours,
    }


def search_receipt(item, judgment, stamp, evidence, hold="search_hit"):
    link = (item.get("parent_url") or "").strip()
    profile = (item.get("profile_url") or "").strip()
    name = (item.get("name") or "").strip()
    if not name or not link.startswith("https://") or not profile.startswith("https://"):
        raise ValueError("search-hit receipt needs a name, profile, and item link")
    prefix = "later-" if hold == "later_use" else "hit-"
    return {
        "receipt_id": prefix + re.sub(r"[^a-z0-9]+", "-", link)[:48],
        "name": name,
        "channel": item["channel"],
        "profile_url": profile,
        "parent_url": link,
        "hold": hold,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence": evidence,
        "task_evidence": judgment["reason"],
        "sources": [{"url": link, "type": item["channel"]}],
        "hour": stamp,
    }


def later_receipt(item, judgment, stamp, evidence):
    return search_receipt(item, judgment, stamp, evidence, hold="later_use")


def collect(
    stamp, *, catalog=None, opener=None, auth=None, apollo_auth=None, max_charge=MAX_CHARGE_USD
):
    catalog = catalog or load_catalog()
    auth = auth if auth is not None else token()
    apollo_auth = apollo_auth if apollo_auth is not None else apollo_token()
    start, end = hour_window(stamp)
    charged = 0.0
    runs = []
    items = []

    def paid(channel, count):
        nonlocal charged
        cost = estimate(channel, count)
        if charged + cost > max_charge:
            return False
        charged += cost
        return True

    def start_actor(channel, payload, count, lookback="1h"):
        if not auth:
            runs.append({"channel": channel, "hold": "apify_token_missing"})
            return []
        if not paid(channel, count):
            runs.append({"channel": channel, "hold": "over_budget"})
            return []
        raw, run_id = apify_items(ACTORS[channel], payload, opener=opener, auth=auth)
        runs.append(
            {
                "channel": channel,
                "actor": ACTORS[channel],
                "run_id": run_id,
                "lookback": lookback,
            }
        )
        return raw if isinstance(raw, list) else []

    x_queries = rotate(catalog, stamp, "x", X_QUERIES)
    li_queries = rotate(catalog, stamp, "linkedin", LINKEDIN_QUERIES)
    hn_queries = rotate(catalog, stamp, "hacker_news", OTHER_QUERIES)
    gh_queries = rotate(catalog, stamp, "github", OTHER_QUERIES)

    x_raw = start_actor(
        "x",
        {
            "searchTerms": [row["query"] for row in x_queries],
            "maxItems": X_ACTOR_ITEMS,
            "queryType": "Latest",
            "since_time": str(int(start.timestamp())),
            "until_time": str(int(end.timestamp())),
        },
        X_ACTOR_ITEMS * max(len(x_queries), 1),
    )
    x_items = []
    for row in x_raw:
        if recent_enough(row, start):
            x_items.append(normalize_x(row, x_queries[0]["query"] if x_queries else ""))
    items.extend(x_items[:X_ACTOR_ITEMS])
    if runs and runs[-1].get("channel") == "x":
        runs[-1]["provider_item_count"] = len(x_raw)
        runs[-1]["bounded_item_count"] = min(len(x_items), X_ACTOR_ITEMS)

    li_raw = []
    if channel_live("linkedin"):
        li_raw = start_actor(
            "linkedin",
            {
                "searchQueries": [row["query"] for row in li_queries],
                "postedLimit": "1h",
                "sortBy": "date",
                "maxPosts": KEEP,
            },
            KEEP * max(len(li_queries), 1),
        )
        for row in li_raw[:KEEP]:
            if recent_enough(row, start):
                items.append(normalize_linkedin(row, li_queries[0]["query"] if li_queries else ""))
    else:
        runs.append({"channel": "linkedin", "hold": "channel_paused"})

    hn_raw = []
    if channel_live("hacker_news"):
        hn_raw = start_actor(
            "hacker_news",
            {
                "query": " OR ".join(f'"{row["query"]}"' for row in hn_queries),
                "contentType": "story",
                "sortBy": "date",
                "maxItems": KEEP,
            },
            KEEP,
        )
        for row in hn_raw[:KEEP]:
            if recent_enough(row, start):
                items.append(normalize_hn(row, hn_queries[0]["query"] if hn_queries else ""))
    else:
        runs.append({"channel": "hacker_news", "hold": "channel_paused"})

    gh_raw = []
    if channel_live("github"):
        gh_raw = start_actor(
            "github",
            {
                "scrapeType": "search",
                "queries": [row["query"] for row in gh_queries],
                "resultsPerQuery": KEEP,
                "sort": "updated",
            },
            KEEP * max(len(gh_queries), 1),
        )
        for row in gh_raw[: KEEP * max(len(gh_queries), 1)]:
            if recent_enough(row, start):
                items.append(normalize_github(row, gh_queries[0]["query"] if gh_queries else ""))
    else:
        runs.append({"channel": "github", "hold": "channel_paused"})

    ph_raw = []
    if channel_live("product_hunt"):
        ph_raw = start_actor(
            "product_hunt",
            {"date": stamp[:10]},
            KEEP,
            lookback="24h",
        )
        for row in ph_raw[:KEEP]:
            items.append(normalize_ph(row, "product hunt daily"))
    else:
        runs.append({"channel": "product_hunt", "hold": "channel_paused"})

    apollo_queries = rotate(catalog, stamp, "apollo", OTHER_QUERIES)
    if not channel_live("apollo"):
        runs.append({"channel": "apollo", "hold": "channel_paused", "enriches": False})
    elif not apollo_auth:
        runs.append({"channel": "apollo", "hold": "apollo_token_missing", "enriches": False})
    else:
        seen_apollo = set()
        for row in apollo_queries:
            raw, hold = apollo_people(row["query"], opener=opener, auth=apollo_auth)
            runs.append(
                {
                    "channel": "apollo",
                    "source": "mixed_people/search",
                    "query": row["query"],
                    "lookback": "current",
                    "enriches": False,
                    **({"hold": hold} if hold else {}),
                }
            )
            if hold:
                continue
            for person in raw:
                item = normalize_apollo(person, row["query"])
                key = (item.get("profile_url") or item.get("name") or "").casefold()
                if not item["name"] or not item["profile_url"].startswith("https://"):
                    continue
                if key in seen_apollo:
                    continue
                seen_apollo.add(key)
                items.append(item)
                if len(seen_apollo) >= KEEP:
                    break
            if len(seen_apollo) >= KEEP:
                break

    return {
        "queries": {
            "x": x_queries,
            "linkedin": li_queries,
            "hacker_news": hn_queries,
            "github": gh_queries,
            "apollo": apollo_queries,
        },
        "lookback": "1h",
        "source": "apify+apollo" if apollo_auth and channel_live("apollo") else "apify",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "runs": runs,
        "items": items,
        "estimated_charge_usd": charged,
    }


def recover_x_dataset(stamp, dataset_id, *, run_id=None, catalog=None, opener=None, auth=None):
    """Resume an interrupted hour from one existing Apify dataset; never starts an Actor."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", str(dataset_id or "")):
        raise ValueError("valid Apify dataset ID required")
    auth = auth if auth is not None else token()
    if not auth:
        raise RuntimeError("APIFY_API_TOKEN required")
    catalog = catalog or load_catalog()
    queries = rotate(catalog, stamp, "x", X_QUERIES)
    start, end = hour_window(stamp)
    raw = fetch_json(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        headers={"Authorization": "Bearer " + auth},
        opener=opener,
    )
    raw = raw if isinstance(raw, list) else []
    query = queries[0]["query"] if queries else ""
    items = [normalize_x(row, query) for row in raw if recent_enough(row, start)]
    return {
        "queries": {"x": queries},
        "lookback": "1h",
        "source": "apify",
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "runs": [
            {
                "channel": "x",
                "actor": ACTORS["x"],
                "run_id": run_id,
                "dataset_id": dataset_id,
                "lookback": "1h",
                "recovered": True,
            },
            *[
                {"channel": channel, "hold": "channel_paused"}
                for channel in ("linkedin", "hacker_news", "github", "product_hunt")
            ],
            {"channel": "apollo", "hold": "channel_paused", "enriches": False},
        ],
        "items": items,
        "estimated_charge_usd": 0.0,
    }


def pending_path(stamp, folder=SEARCH_PENDING):
    return Path(folder) / f"{stamp}.json"


def bounded_collection(collected, limit=MAX_HITS):
    """Return a review-sized copy even when a provider ignores its item limit."""
    payload = dict(collected)
    items = list(collected.get("items") or [])
    payload["items"] = items[:limit]
    payload["provider_item_count"] = collected.get("provider_item_count", len(items))
    payload["bounded_item_count"] = len(payload["items"])
    payload["overflow_item_count"] = max(0, len(items) - len(payload["items"]))
    return payload


def collect_pending(stamp=None, *, catalog=None, folder=SEARCH_PENDING, opener=None, auth=None):
    """Collect one bounded hour for model review. Replays never start another Actor."""
    stamp = stamp or hour_stamp()
    final = drop_path(stamp)
    if final.exists():
        return {"skipped": True, "reason": "hour_exists", "path": str(final)}
    path = pending_path(stamp, folder)
    if path.exists():
        return {
            "skipped": True,
            "reason": "pending_exists",
            "path": str(path),
            "data": json.loads(path.read_text()),
        }
    catalog = catalog or load_catalog()
    data = collect(stamp, catalog=catalog, opener=opener, auth=auth, apollo_auth="")
    payload = {
        "hour": stamp,
        "catalog_version": catalog.get("version"),
        **data,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return {"skipped": False, "path": str(path), "data": payload}


def _review_scorer(review):
    rows = review.get("judgments")
    if not isinstance(rows, list):
        raise ValueError("review judgments must be a list")
    by_url = {}
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("parent_url") or "").startswith("https://"):
            raise ValueError("every judgment needs the exact parent URL")
        if row["parent_url"] in by_url:
            raise ValueError("duplicate reviewed parent URL")
        required = {
            "builder",
            "specificity",
            "substance",
            "total",
            "action",
            "reason",
            "tool_discovery",
            "topic",
        }
        if not required.issubset(row):
            raise ValueError("incomplete model judgment")
        scores = (row["builder"], row["specificity"], row["substance"])
        if any(type(value) is not int for value in scores):
            raise ValueError("model scores must be integers")
        if not (0 <= scores[0] <= 50 and 0 <= scores[1] <= 30 and 0 <= scores[2] <= 20):
            raise ValueError("model score outside policy bounds")
        if type(row["total"]) is not int or row["total"] != sum(scores):
            raise ValueError("model score total mismatch")
        if row["action"] not in {"comment", "skip"}:
            raise ValueError("X model action must be comment or skip")
        if type(row["tool_discovery"]) is not bool or row["topic"] not in {
            "",
            "agent",
            "assistant",
        }:
            raise ValueError("invalid model classification fields")
        by_url[row["parent_url"]] = row

    def score(item):
        url = item.get("parent_url")
        if url not in by_url:
            raise ValueError("model review does not cover every collected item")
        return {
            **{
                key: by_url[url][key]
                for key in (
                    "builder",
                    "specificity",
                    "substance",
                    "total",
                    "action",
                    "reason",
                    "tool_discovery",
                    "topic",
                )
            },
            "policy": POLICY,
            "model": review["model"],
            "model_route": review["route"],
        }

    return score


def finalize_pending(
    review_path,
    *,
    drops=DROPS,
    handoff=HANDOFF,
    pending=PENDING,
    search_pending=SEARCH_PENDING,
):
    """Finalize an exact model review without starting a provider search."""
    review = json.loads(Path(review_path).read_text())
    stamp = str(review.get("hour") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", stamp):
        raise ValueError("review hour required")
    if not str(review.get("model") or "").strip() or not str(review.get("route") or "").strip():
        raise ValueError("actual model and route required")
    source = pending_path(stamp, search_pending)
    if not source.is_file():
        raise ValueError("matching pending collection missing")
    collected = json.loads(source.read_text())
    if collected.get("hour") != stamp:
        raise ValueError("pending collection hour mismatch")
    result = run(
        stamp,
        catalog=load_catalog(),
        drops=drops,
        handoff=handoff,
        pending=pending,
        collected=collected,
        scorer=_review_scorer(review),
    )
    result["model"] = review["model"]
    result["model_route"] = review["route"]
    return result


def run(
    stamp=None,
    *,
    catalog=None,
    drops=DROPS,
    handoff=HANDOFF,
    pending=PENDING,
    opener=None,
    auth=None,
    apollo_auth=None,
    ingest_later=True,
    record_kwargs=None,
    scorer=None,
    collected=None,
):
    stamp = stamp or hour_stamp()
    dest = drop_path(stamp, drops)
    if dest.exists():
        try:
            existing = json.loads(dest.read_text())
        except json.JSONDecodeError:
            existing = {"hour": stamp}
        return {
            "skipped": True,
            "hour_exists": True,
            "path": str(dest),
            "reason": "hour_exists",
            "brief": brief_from_payload(
                {**existing, "hour": existing.get("hour") or stamp}, path=dest
            ),
        }
    dest.parent.mkdir(parents=True, exist_ok=True)
    catalog = catalog or load_catalog()
    collected = collected or collect(
        stamp, catalog=catalog, opener=opener, auth=auth, apollo_auth=apollo_auth
    )
    collected = bounded_collection(collected)
    comments = []
    later = []
    skipped = []
    for item in collected["items"]:
        judgment = qualify(item, catalog, scorer=scorer)
        if item.get("channel") == "apollo" and judgment["action"] == "comment":
            judgment = {**judgment, "action": "later_use"}
        if item.get("channel") == "linkedin" and not channel_live("linkedin"):
            if judgment["action"] == "comment":
                judgment = {**judgment, "action": "later_use", "reason": "channel_paused"}
        row = {**item, **judgment}
        if judgment["action"] == "comment":
            comments.append(row)
        elif judgment["action"] == "later_use":
            later.append(row)
        else:
            skipped.append(row)
    unique_later = []
    seen_links = set()
    for item in later:
        link = item.get("parent_url")
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique_later.append(item)
    kept = comments + unique_later
    kept.sort(
        key=lambda row: (-(row.get("total") or 0), row.get("channel") or "", row.get("name") or "")
    )
    overflow = kept[MAX_HITS:]
    kept = kept[:MAX_HITS]
    for row in overflow:
        skipped.append({**row, "action": "skip", "reason": "over_hour_handful"})
    comments = []
    later = []
    for row in kept:
        if row.get("action") == "comment":
            comments.append({**row, "handoff": str(write_handoff(row, row, stamp, handoff))})
        else:
            later.append({**row, "outreach": str(write_outreach(row, row, stamp, handoff))})
    evidence = str(dest.relative_to(ROOT)) if dest.is_relative_to(ROOT) else str(dest)
    receipts = [search_receipt(item, item, stamp, evidence) for item in comments] + [
        later_receipt(item, item, stamp, evidence) for item in later
    ]
    crm = []
    if ingest_later and receipts:
        pending = Path(pending)
        pending.mkdir(parents=True, exist_ok=True)
        paths = []
        seen_paths = set()
        for receipt in receipts:
            path = pending / (receipt["receipt_id"] + ".json")
            path.write_text(json.dumps(receipt, indent=2) + "\n")
            if path.resolve() in seen_paths:
                continue
            seen_paths.add(path.resolve())
            paths.append(path)
        crm = ingest(paths, **(record_kwargs or {}))
    payload = {
        "hour": stamp,
        "catalog_version": collected.get("catalog_version") or catalog.get("version"),
        "source": collected.get("source") or "apify",
        "lookback": collected.get("lookback") or "1h",
        "window": collected.get("window"),
        "queries": collected["queries"],
        "runs": collected["runs"],
        "estimated_charge_usd": collected["estimated_charge_usd"],
        "provider_item_count": collected.get("provider_item_count", len(collected["items"])),
        "bounded_item_count": collected.get("bounded_item_count", len(collected["items"])),
        "overflow_item_count": collected.get("overflow_item_count", 0),
        "comments": comments,
        "later_use": later,
        "skipped": [{"channel": r.get("channel"), "reason": r.get("reason")} for r in skipped],
        "crm": [
            {
                "path": row.get("path"),
                "status": row.get("status"),
                "person_id": row.get("person_id"),
                "existing": row.get("existing"),
                "task_id": row.get("task_id"),
                "queued": row.get("queued", False),
                "error": row.get("error"),
            }
            for row in crm
        ],
    }
    dest.write_text(json.dumps(payload, indent=2) + "\n")
    text = dest.read_text()
    leaked = [secret for secret in (token(), apollo_token()) if secret and secret in text]
    if leaked:
        dest.unlink()
        raise RuntimeError("search drop leaked a credential")
    result = {"hour_exists": False, "path": str(dest), **payload}
    result["brief"] = brief_from_payload(payload, path=dest)
    return result


def ready(*, opener=None, persist=True, shared_factory=None):
    """Check Apify token and CRM write path. Never start an Actor. Never enrich."""
    auth = token()
    if persist and auth:
        persist_apify_token(auth)
        auth = token()
    apify = {"present": bool(auth), "verified": False, "hold": None}
    if auth:
        try:
            user = fetch_json(
                "https://api.apify.com/v2/users/me",
                headers={"Authorization": "Bearer " + auth},
                opener=opener,
            )
            data = user.get("data") or user
            apify["verified"] = True
            apify["username"] = data.get("username")
            apify["plan"] = (data.get("plan") or {}).get("id") or data.get("plan")
        except Exception as exc:
            apify["hold"] = type(exc).__name__
    apollo_auth = apollo_token()
    apollo = {"present": bool(apollo_auth), "verified": False, "hold": None, "enriches": False}
    if not channel_live("apollo"):
        apollo["hold"] = "channel_paused"
    elif apollo_auth:
        try:
            fetch_json(
                APOLLO_HEALTH,
                headers={"x-api-key": apollo_auth, "Content-Type": "application/json"},
                opener=opener,
            )
            apollo["verified"] = True
        except Exception as exc:
            apollo["hold"] = type(exc).__name__
    crm = {"configured": bool(configured()), "connected": False, "hold": None}
    if crm["configured"]:
        try:
            shared = (shared_factory or connect)()
            try:
                row = shared.execute("SELECT count(*) FROM people").fetchone()
                crm["connected"] = True
                crm["people"] = row[0]
            finally:
                shared.close()
        except Exception as exc:
            crm["hold"] = type(exc).__name__
    return {
        "apify": apify,
        "apollo": apollo,
        "crm": crm,
        "actors": list(ACTORS.values()),
        "lookback": "1h",
        "ready": bool(apify.get("verified") and crm.get("connected")),
    }


def pending_handoffs(channel, folder=HANDOFF):
    dest = Path(folder) / channel
    if not dest.is_dir():
        return []
    return sorted(p for p in dest.glob("*.json") if not p.name.endswith(".done.json"))


def reconcile_leads(
    *,
    held=PUBLICATION_HELD,
    pending=PENDING,
    processed=PUBLICATION_PROCESSED,
    record_kwargs=None,
):
    """Retry only hourly lead receipts whose prior shared-CRM write failed."""
    failed = []
    held = Path(held)
    if held.is_dir():
        for path in sorted(held.glob("*.json")):
            if path.name.endswith(".result.json"):
                continue
            result_path = path.with_suffix(path.suffix + ".result.json")
            if not result_path.is_file():
                continue
            try:
                receipt = json.loads(path.read_text())
                result = json.loads(result_path.read_text())
            except json.JSONDecodeError:
                continue
            if receipt.get("hold") == "search_hit" and result.get("error") in {
                "OperationalError",
                "ConnectionError",
                "TimeoutError",
            }:
                failed.append(path)
    results = (
        ingest(
            failed,
            held=held,
            pending=Path(pending),
            processed=Path(processed),
            **(record_kwargs or {}),
        )
        if failed
        else []
    )
    return {"retried": len(failed), "results": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=("run", "collect", "finalize", "handoffs", "ready", "brief", "reconcile"),
    )
    parser.add_argument("--hour")
    parser.add_argument("--day")
    parser.add_argument("--channel")
    parser.add_argument("--dataset-id")
    parser.add_argument("--run-id")
    parser.add_argument("--review")
    args = parser.parse_args()
    if args.command == "handoffs":
        print(json.dumps([str(p) for p in pending_handoffs(args.channel or "")], indent=2))
        return
    if args.command == "ready":
        print(json.dumps(ready(), indent=2))
        return
    if args.command == "reconcile":
        print(json.dumps(reconcile_leads(), indent=2))
        return
    if args.command == "collect":
        print(json.dumps(collect_pending(args.hour), indent=2))
        return
    if args.command == "finalize":
        if not args.review:
            parser.error("finalize requires --review")
        print(json.dumps(finalize_pending(args.review), indent=2))
        return
    if args.command == "brief":
        if args.day:
            print(json.dumps(day_brief(args.day), indent=2))
        else:
            print(json.dumps(brief(args.hour), indent=2))
        return
    stamp = args.hour or hour_stamp()
    recovered = (
        recover_x_dataset(stamp, args.dataset_id, run_id=args.run_id) if args.dataset_id else None
    )
    print(json.dumps(run(stamp, collected=recovered), indent=2))


if __name__ == "__main__":
    main()
