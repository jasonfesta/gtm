"""Provider contracts for manually invoked human discovery; no outreach or timers."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from crm.human_discovery_search import fetch_json

REDDIT_ACTOR = "trudax/reddit-scraper-lite"
APOLLO_SEARCH = "https://api.apollo.io/api/v1/mixed_people/api_search"


def reddit_thread_url(value):
    """Accept only real Reddit thread paths, never a post's outbound article URL."""
    value = str(value or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        value = "https://www.reddit.com" + value
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname
            not in {
                "reddit.com",
                "www.reddit.com",
                "old.reddit.com",
                "new.reddit.com",
            }
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            return ""
        if not re.fullmatch(r"/r/[A-Za-z0-9_]+/comments/[a-z0-9]+(?:/[^/?#]*)?/?", parsed.path):
            return ""
    except ValueError:
        return ""
    return "https://www.reddit.com" + parsed.path


def reddit_search_input(terms, max_items):
    return {
        "searches": list(terms),
        "searchPosts": True,
        "searchComments": False,
        "searchUsers": False,
        "searchCommunities": False,
        "skipComments": True,
        "skipCommunity": True,
        "sort": "new",
        "time": "day",
        "maxItems": max_items,
        "maxPostCount": max(1, max_items // len(terms)),
        "proxy": {"useApifyProxy": True},
    }


def completed_apify_items(actor, payload, *, auth, api_call=None):
    """One bounded wait. Unfinished/failed runs are errors, never zero-result success.

    The run ID in the error can be read back manually without re-launching a run.
    This intentionally does not change the hourly or X watcher's provider helper.
    """
    api_call = api_call or fetch_json
    headers = {"Authorization": "Bearer " + auth, "Content-Type": "application/json"}
    result = api_call(
        "https://api.apify.com/v2/acts/"
        + actor.replace("/", "~")
        + "/runs?waitForFinish=55&timeout=300",
        method="POST",
        payload=payload,
        headers=headers,
    )
    run = result.get("data") or result
    return read_completed_run(run, auth=auth, api_call=api_call)


def read_completed_run(run, *, auth, api_call=None):
    api_call = api_call or fetch_json
    rid = run.get("id")
    if run.get("status") != "SUCCEEDED":
        raise RuntimeError(f"apify_run:{rid}:status:{run.get('status', 'UNKNOWN')}")
    dataset = run.get("defaultDatasetId")
    if not dataset:
        raise RuntimeError(f"apify_run:{rid}:missing_dataset")
    items = api_call(
        f"https://api.apify.com/v2/datasets/{dataset}/items?clean=true",
        headers={"Authorization": "Bearer " + auth},
    )
    if not isinstance(items, list):
        raise RuntimeError(f"apify_run:{rid}:invalid_dataset")
    return items, rid
