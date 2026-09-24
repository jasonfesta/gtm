"""Hacker News source evidence and permalink handoffs for manual Human Reply."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def plain_text(value):
    return html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def normalize_hacker_news(raw, query=""):
    author = str(raw.get("author") or "")
    item_id = str(raw.get("id") or raw.get("objectID") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", author) or not item_id.isdigit():
        return {}
    comment = raw.get("commentText") or raw.get("comment_text") or ""
    is_comment = raw.get("type") == "comment" or bool(comment)
    # Actor titles on comments describe the parent story, not the author's words.
    title = "" if is_comment else plain_text(raw.get("title"))
    body = plain_text(comment if is_comment else raw.get("storyText") or raw.get("story_text"))
    text = " ".join(v for v in (title, body) if v)
    return {
        "channel": "hacker_news",
        "name": author,
        "profile_url": f"https://news.ycombinator.com/user?id={author}",
        "parent_url": f"https://news.ycombinator.com/item?id={item_id}",
        "provider_id": item_id,
        "text": text,
        "title": title,
        "query": query,
        "observed_source_at": raw.get("createdAt") or raw.get("created_at"),
        "story_id": str(raw.get("storyId") or raw.get("story_id") or ""),
        "item_type": "comment" if is_comment else "story",
    }


def reply_handoff(items, *, run_id):
    """Deduplicate exact source items. This is discovery evidence, never a send receipt."""
    candidates = {}
    for item in items:
        url = urlsplit(item.get("parent_url") or "")
        query = parse_qs(url.query)
        item_id = str(item.get("provider_id") or "")
        if (
            url.scheme != "https"
            or url.netloc != "news.ycombinator.com"
            or url.path != "/item"
            or query != {"id": [item_id]}
            or not item_id.isdigit()
        ):
            raise ValueError("exact Hacker News item permalink required")
        if not item.get("text") or not item.get("profile_url"):
            raise ValueError("author identity and source text required")
        candidates[item_id] = {
            "source": "hacker_news",
            "source_id": item_id,
            "author": item["name"],
            "profile_url": item["profile_url"],
            "permalink": item["parent_url"],
            "source_text": item["text"],
            "source_text_sha256": hashlib.sha256(item["text"].encode()).hexdigest(),
            "query": item.get("query"),
            "observed_source_at": item.get("observed_source_at"),
            "dedup_key": f"hacker_news:item:{item_id}",
            "status": "discovered",
        }
    return {
        "schema_version": 1,
        "source_agent": "human discovery agent",
        "destination_agent": "human reply agent",
        "run_id": run_id,
        "channel": "hacker_news",
        "candidates": list(candidates.values()),
        "execution_contract": {
            "open_exact_permalink": True,
            "read_thread_and_community_rules": True,
            "check_existing_reply_by_operator_before_send": True,
            "uncertain_send": "Read back the exact thread and operator reply; never blindly resend.",
            "completion_evidence": ["reply_permalink", "operator_username", "exact_reply_text"],
            "ops_handoffs": "Separate CRM and PostHog files after verified publication.",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="saved qualified discovery input with sources.hacker_news",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text())
    result = reply_handoff(
        payload.get("sources", {}).get("hacker_news", []), run_id=payload["run_id"]
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output.exists():
        if json.loads(args.output.read_text()) != result:
            raise FileExistsError("refusing to overwrite a different source handoff")
    else:
        with args.output.open("x") as stream:
            stream.write(encoded)
    print(args.output.resolve())


if __name__ == "__main__":
    main()
