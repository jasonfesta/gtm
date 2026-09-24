"""Read public reply opportunities from Agent routes in a CRM snapshot."""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional

from .agent_reply import ProviderFailure


class Reader:
    def __init__(self, opener: Optional[Callable[..., Any]] = None):
        self.opener = opener or urllib.request.urlopen

    def get(self, url: str, headers=None) -> Any:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", **(headers or {})},
            method="GET",
        )
        try:
            with self.opener(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            exc.read()
            raise ProviderFailure(f"http_{exc.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise ProviderFailure("transport_error", uncertain=True) from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderFailure("invalid_provider_response", uncertain=True) from None


def _bearer(env_name: str, prefix: str = "Bearer") -> str:
    token = (os.environ.get(env_name) or "").strip()
    return f"{prefix} {token}" if token else ""


def _github_repo(value: Any):
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = urllib.parse.urlsplit(value.strip())
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if parsed.hostname not in {"github.com", "www.github.com"} or len(parts) < 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


def _contact_urls(agent: Dict[str, Any], channel: str) -> Iterable[str]:
    direct = agent.get(channel + "_url")
    if direct:
        yield direct
    for contact in agent.get("contact_points") or []:
        if contact.get("channel") == channel or contact.get("contact_type") == channel:
            if contact.get("address"):
                yield contact["address"]
            if contact.get("source_url"):
                yield contact["source_url"]


def github_routes(snapshot: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    routes = []
    seen = set()
    for agent in snapshot:
        values = [
            agent.get("website_url"),
            agent.get("github_url"),
            *_contact_urls(agent, "github"),
        ]
        for value in values:
            repo = _github_repo(value)
            if not repo or repo in seen:
                continue
            seen.add(repo)
            routes.append(
                {
                    "target_agent_id": agent.get("agent_id") or agent.get("entity_id"),
                    "target_reference": agent.get("name") or agent.get("canonical_name"),
                    "crm_category": agent.get("audience"),
                    "description": agent.get("description"),
                    "owner": repo[0],
                    "repo": repo[1],
                }
            )
    return routes


def scan_github(
    snapshot: List[Dict[str, Any]], config: Dict[str, Any], reader: Reader
) -> List[Dict[str, Any]]:
    settings = config.get("github") or {}
    if settings.get("enabled") is not True:
        return []
    token = _bearer(settings.get("token_env", "GITHUB_TOKEN"))
    headers = {"Authorization": token} if token else {}
    headers["X-GitHub-Api-Version"] = settings.get("api_version", "2022-11-28")
    base = settings.get("api_base", "https://api.github.com").rstrip("/")
    per_repo = int(settings.get("conversations_per_repo", 2))
    max_repos = int(settings.get("max_repos", 25))
    sender = settings.get("sender_account", "")
    candidates = []
    for route in github_routes(snapshot)[:max_repos]:
        query = urllib.parse.urlencode(
            {
                "state": "open",
                "sort": "updated",
                "direction": "desc",
                "per_page": per_repo,
            }
        )
        url = f"{base}/repos/{route['owner']}/{route['repo']}/issues?{query}"
        try:
            issues = reader.get(url, headers)
        except ProviderFailure:
            continue
        if not isinstance(issues, list):
            continue
        for issue in issues[:per_repo]:
            number = issue.get("number")
            parent_url = issue.get("html_url")
            if number is None or not parent_url:
                continue
            candidates.append(
                {
                    "channel": "github",
                    "sender_account": sender,
                    "target_agent_id": route["target_agent_id"],
                    "target_reference": route["target_reference"],
                    "crm_category": route["crm_category"],
                    "conversation_id": f"{route['owner']}/{route['repo']}#{number}",
                    "parent_url": parent_url,
                    "conversation_text": "\n\n".join(
                        part for part in (issue.get("title"), issue.get("body")) if part
                    ),
                    "agent_description": route["description"],
                    "provider": {
                        "owner": route["owner"],
                        "repo": route["repo"],
                        "issue_number": str(number),
                        "token_env": settings.get("token_env", "GITHUB_TOKEN"),
                        "api_base": base,
                        "api_version": settings.get("api_version", "2022-11-28"),
                    },
                }
            )
    return candidates


def scan_moltbook(config: Dict[str, Any], reader: Reader) -> List[Dict[str, Any]]:
    settings = config.get("moltbook") or {}
    if settings.get("enabled") is not True:
        return []
    limit = int(settings.get("limit", 25))
    base = settings.get("api_base", "https://www.moltbook.com/api/v1").rstrip("/")
    token = _bearer(settings.get("token_env", "MOLTBOOK_API_KEY"))
    headers = {"Authorization": token} if token else {}
    payload = reader.get(f"{base}/posts?sort=new&limit={limit}", headers)
    posts = payload.get("posts") or payload.get("data") or payload
    if not isinstance(posts, list):
        return []
    candidates = []
    for post in posts[:limit]:
        post_id = post.get("id") or post.get("post_id")
        if not post_id:
            continue
        author = post.get("author") or post.get("agent") or {}
        author_name = author.get("name") if isinstance(author, dict) else str(author)
        parent_url = post.get("url") or f"https://www.moltbook.com/post/{post_id}"
        candidates.append(
            {
                "channel": "moltbook",
                "sender_account": settings.get("sender_account", ""),
                "target_reference": author_name or f"moltbook:{post_id}",
                "conversation_id": str(post_id),
                "parent_url": parent_url,
                "conversation_text": "\n\n".join(
                    part for part in (post.get("title"), post.get("content")) if part
                ),
                "provider": {
                    "post_id": str(post_id),
                    "token_env": settings.get("token_env", "MOLTBOOK_API_KEY"),
                    "credentials_file": settings.get("credentials_file"),
                    "api_base": base,
                },
            }
        )
    return candidates


def scan_discord(config: Dict[str, Any], reader: Reader) -> List[Dict[str, Any]]:
    settings = config.get("discord") or {}
    if settings.get("enabled") is not True:
        return []
    base = settings.get("api_base", "https://discord.com/api/v10").rstrip("/")
    token = _bearer(settings.get("token_env", "DISCORD_BOT_TOKEN"), "Bot")
    headers = {"Authorization": token} if token else {}
    candidates = []
    for route in settings.get("channels") or []:
        channel_id = str(route.get("channel_id") or "")
        guild_id = str(route.get("guild_id") or "")
        limit = int(route.get("limit", 25))
        if not channel_id or not guild_id:
            continue
        try:
            messages = reader.get(f"{base}/channels/{channel_id}/messages?limit={limit}", headers)
        except ProviderFailure:
            continue
        if not isinstance(messages, list):
            continue
        for message in messages[:limit]:
            message_id = str(message.get("id") or "")
            if not message_id or not message.get("content"):
                continue
            author = message.get("author") or {}
            candidates.append(
                {
                    "channel": "discord",
                    "sender_account": settings.get("sender_account", ""),
                    "target_agent_id": route.get("target_agent_id"),
                    "target_reference": route.get("target_reference")
                    or author.get("username")
                    or author.get("id"),
                    "crm_category": route.get("crm_category"),
                    "conversation_id": f"{guild_id}/{channel_id}/{message_id}",
                    "parent_url": f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}",
                    "conversation_text": message["content"],
                    "provider": {
                        "guild_id": guild_id,
                        "channel_id": channel_id,
                        "message_id": message_id,
                        "token_env": settings.get("token_env", "DISCORD_BOT_TOKEN"),
                        "api_base": base,
                    },
                }
            )
    return candidates


def snapshot_records(snapshot: Any) -> List[Dict[str, Any]]:
    """Accept the Ops Agents CRM envelope and the legacy bare-record fixture shape."""
    records = snapshot.get("records") if isinstance(snapshot, dict) else snapshot
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        raise ValueError("Ops Agents CRM snapshot must contain a records array")
    return records


def scan_once(
    snapshot: Any, config: Dict[str, Any], reader: Optional[Reader] = None
) -> List[Dict[str, Any]]:
    records = snapshot_records(snapshot)
    reader = reader or Reader()
    return [
        *scan_moltbook(config, reader),
        *scan_github(records, config, reader),
        *scan_discord(config, reader),
    ]
