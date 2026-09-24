"""Read the Darwin daily agent index."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def read_all(config, cursor=None, *, opener=None):
    """Return every page in the current delta and the final cursor."""
    url = str(config["url"]).strip()
    token = os.environ.get(config.get("token_env", "DARWIN_DAILY_INDEX_TOKEN"), "").strip()
    fetch = opener or urllib.request.urlopen
    items = []
    current = cursor
    seen = set()

    while True:
        query = {} if current in (None, "") else {"cursor": current}
        suffix = ("&" if "?" in url else "?") + urllib.parse.urlencode(query) if query else ""
        page_url = url + suffix
        headers = {
            "Accept": "application/json",
            "User-Agent": "darwin-agent-discovery/1",
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(page_url, headers=headers)
        with fetch(request, timeout=config.get("timeout_seconds", 30)) as response:
            page = json.loads(response.read().decode())

        records = page.get("items", [])
        if not isinstance(records, list):
            raise ValueError("daily index items must be a list")
        items.extend(records)
        next_cursor = page.get("next_cursor")
        if page.get("complete", next_cursor in (None, "")):
            return items, next_cursor if next_cursor is not None else current
        if next_cursor in (None, "") or next_cursor == current or next_cursor in seen:
            raise ValueError("daily index cursor did not advance")
        seen.add(next_cursor)
        current = next_cursor
