"""Manual agent-reply runner and provider adapters.

This module intentionally has no loop, scheduler, timer, CRM client, or PostHog client.
It sends one prepared reply and writes separate CRM and PostHog handoffs for Ops Agents.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

FORMS = {"query", "markdown_resource", "usage_instruction"}
CHANNELS = {"moltbook", "discord", "github"}


def _required(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def idempotency_key(item: Dict[str, Any]) -> str:
    identity = {
        "channel": item.get("channel"),
        "sender_account": item.get("sender_account"),
        "conversation_id": item.get("conversation_id"),
        "parent_url": item.get("parent_url"),
    }
    return "agent_reply_" + hashlib.sha256(_encode(identity).encode()).hexdigest()[:24]


def validate(item: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("input must be a JSON object")
    channel = _required(item.get("channel"), "channel").lower()
    if channel not in CHANNELS:
        raise ValueError("channel must be moltbook, discord, or github")
    form = _required(item.get("response_form"), "response_form")
    if form not in FORMS:
        raise ValueError("unsupported response_form")
    item = dict(item)
    item["channel"] = channel
    item["sender_account"] = _required(item.get("sender_account"), "sender_account")
    item["conversation_id"] = _required(item.get("conversation_id"), "conversation_id")
    item["parent_url"] = _required(item.get("parent_url"), "parent_url")
    item["outbound_text"] = _required(item.get("outbound_text"), "outbound_text")
    if not item.get("target_agent_id") and not item.get("target_reference"):
        raise ValueError("target_agent_id or target_reference is required")
    if form == "markdown_resource":
        item["resource_version"] = _required(item.get("resource_version"), "resource_version")
    item["provider"] = item.get("provider") or {}
    if not isinstance(item["provider"], dict):
        raise ValueError("provider must be an object")
    return item


class ProviderFailure(RuntimeError):
    def __init__(self, code: str, *, uncertain: bool = False, result=None):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain
        self.result = result


@dataclass(frozen=True)
class ProviderResult:
    message_id: str
    permalink: str


class JsonHttp:
    def __init__(self, opener: Optional[Callable[..., Any]] = None):
        self.opener = opener or urllib.request.urlopen

    def post(self, url: str, token: str, body: Dict[str, Any], headers=None) -> Dict[str, Any]:
        return self._request(url, token, body, headers)

    def get(self, url: str, token: str) -> Dict[str, Any]:
        return self._request(url, token, None)

    def _request(self, url, token, body, headers=None):
        request_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **(headers or {}),
        }
        if token:
            request_headers.setdefault("Authorization", token)
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            headers=request_headers,
            method="POST" if body is not None else "GET",
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


def _secret(env_name: str, credentials_file: Optional[str] = None) -> str:
    value = (os.environ.get(env_name) or "").strip()
    if not value and env_name == "MOLTBOOK_API_KEY":
        paths = (
            [Path(credentials_file)]
            if credentials_file
            else [
                Path(__file__).resolve().parents[2] / ".local-credentials" / "moltbook.json",
                Path.home() / ".config" / "moltbook" / "credentials.json",
            ]
        )
        for path in paths:
            try:
                saved = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(saved, dict):
                agent = saved.get("agent") or {}
                value = str(
                    (agent.get("api_key") if isinstance(agent, dict) else None)
                    or saved.get("api_key")
                    or ""
                ).strip()
            if value:
                break
    if not value:
        raise ProviderFailure(f"missing_{env_name.lower()}")
    return value


def send_moltbook(item: Dict[str, Any], http: JsonHttp) -> ProviderResult:
    provider = item["provider"]
    post_id = _required(provider.get("post_id"), "provider.post_id")
    base = provider.get("api_base", "https://www.moltbook.com/api/v1").rstrip("/")
    token = _secret(provider.get("token_env", "MOLTBOOK_API_KEY"), provider.get("credentials_file"))
    status = http.get(f"{base}/agents/status", f"Bearer {token}")
    if status.get("status") != "claimed":
        raise ProviderFailure("moltbook_not_claimed")
    body = {"content": item["outbound_text"]}
    if provider.get("parent_comment_id"):
        body["parent_id"] = str(provider["parent_comment_id"])
    payload = http.post(
        f"{base}/posts/{post_id}/comments",
        token,
        body,
        {"Authorization": f"Bearer {token}"},
    )
    data = payload.get("data") or payload.get("comment") or payload
    message_id = str(data.get("id") or data.get("comment_id") or "").strip()
    if not message_id:
        raise ProviderFailure("missing_provider_message_id", uncertain=True)
    permalink = str(data.get("url") or data.get("permalink") or "").strip()
    if not permalink:
        permalink = item["parent_url"] + f"#comment-{message_id}"
    result = ProviderResult(message_id, permalink)
    if (
        payload.get("verification")
        or data.get("verification")
        or data.get("verification_status") in {"pending", "failed"}
    ):
        raise ProviderFailure("moltbook_verification_required", uncertain=True, result=result)
    readback = _readback(
        http, f"{base}/posts/{post_id}/comments?sort=new&limit=100", f"Bearer {token}", result
    )
    # Replies are nested under their root comment in the provider response.
    pending = list(readback.get("comments") or [])
    while pending:
        comment = pending.pop()
        if not isinstance(comment, dict):
            continue
        if str(comment.get("id")) == message_id:
            if comment.get("content") == item["outbound_text"] and comment.get(
                "verification_status"
            ) not in {"pending", "failed"}:
                return result
            break
        pending.extend(comment.get("replies") or [])
    raise ProviderFailure("provider_readback_mismatch", uncertain=True, result=result)


def _readback(http, url, token, result):
    try:
        payload = http.get(url, token)
        if not isinstance(payload, dict):
            raise ProviderFailure("invalid_provider_response")
        return payload
    except ProviderFailure as exc:
        # Creation already returned an ID. Even a failed GET must not permit a resend.
        raise ProviderFailure(
            "provider_readback_" + exc.code, uncertain=True, result=result
        ) from None


def send_discord(item: Dict[str, Any], http: JsonHttp) -> ProviderResult:
    provider = item["provider"]
    guild_id = _required(provider.get("guild_id"), "provider.guild_id")
    channel_id = _required(provider.get("channel_id"), "provider.channel_id")
    message_id = _required(provider.get("message_id"), "provider.message_id")
    base = provider.get("api_base", "https://discord.com/api/v10").rstrip("/")
    payload = http.post(
        f"{base}/channels/{channel_id}/messages",
        _secret(provider.get("token_env", "DISCORD_BOT_TOKEN")),
        {
            "content": item["outbound_text"],
            "message_reference": {"message_id": message_id, "fail_if_not_exists": True},
            "allowed_mentions": {"parse": []},
        },
        {"Authorization": "Bot " + _secret(provider.get("token_env", "DISCORD_BOT_TOKEN"))},
    )
    sent_id = str(payload.get("id") or "").strip()
    if not sent_id:
        raise ProviderFailure("missing_provider_message_id", uncertain=True)
    result = ProviderResult(
        sent_id, f"https://discord.com/channels/{guild_id}/{channel_id}/{sent_id}"
    )
    readback = _readback(
        http,
        f"{base}/channels/{channel_id}/messages/{sent_id}",
        "Bot " + _secret(provider.get("token_env", "DISCORD_BOT_TOKEN")),
        result,
    )
    if (
        str(readback.get("id")) != sent_id
        or str(readback.get("channel_id")) != channel_id
        or readback.get("content") != item["outbound_text"]
        or str((readback.get("message_reference") or {}).get("message_id")) != message_id
    ):
        raise ProviderFailure("provider_readback_mismatch", uncertain=True, result=result)
    return result


def send_github(item: Dict[str, Any], http: JsonHttp) -> ProviderResult:
    provider = item["provider"]
    owner = _required(provider.get("owner"), "provider.owner")
    repo = _required(provider.get("repo"), "provider.repo")
    issue_number = _required(provider.get("issue_number"), "provider.issue_number")
    base = provider.get("api_base", "https://api.github.com").rstrip("/")
    payload = http.post(
        f"{base}/repos/{owner}/{repo}/issues/{issue_number}/comments",
        _secret(provider.get("token_env", "GITHUB_TOKEN")),
        {"body": item["outbound_text"]},
        {
            "Authorization": "Bearer " + _secret(provider.get("token_env", "GITHUB_TOKEN")),
            "X-GitHub-Api-Version": provider.get("api_version", "2022-11-28"),
        },
    )
    message_id = str(payload.get("id") or "").strip()
    permalink = str(payload.get("html_url") or "").strip()
    if not message_id or not permalink:
        raise ProviderFailure("missing_provider_receipt", uncertain=True)
    return ProviderResult(message_id, permalink)


SENDERS = {
    "moltbook": send_moltbook,
    "discord": send_discord,
    "github": send_github,
}


def _write_crm_handoff(output_dir: Path, outcome: Dict[str, Any]) -> Path:
    key = outcome["idempotency_key"]
    ops_dir = output_dir / "ops-agents"
    ops_dir.mkdir(parents=True, exist_ok=True)
    crm_path = ops_dir / f"{key}.crm.json"
    crm_record = {
        "handoff_id": key,
        "source_agent": "agent reply agent",
        "operations": [{"action": "event_record", "payload": outcome}],
    }
    crm_path.write_text(json.dumps(crm_record, indent=2, sort_keys=True) + "\n")
    return crm_path


def _write_posthog_handoff(output_dir: Path, outcome: Dict[str, Any]) -> Path:
    ops_dir = output_dir / "ops-agents"
    ops_dir.mkdir(parents=True, exist_ok=True)
    handoff_id = outcome["idempotency_key"]
    handoff_path = ops_dir / f"{handoff_id}.posthog.json"
    distinct_source = "|".join(
        str(outcome.get(key) or "")
        for key in ("sender_account", "target_agent_id", "target_reference")
    )
    distinct_id = "agent_reply_" + hashlib.sha256(distinct_source.encode()).hexdigest()[:24]
    event_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, "gtm.agent_public_reply:" + handoff_id))
    handoff = {
        "schema_version": 1,
        "source_agent": "agent reply agent",
        "run_id": handoff_id,
        "events": [
            {
                "event": "gtm.agent_public_reply",
                "uuid": event_uuid,
                "timestamp": outcome["attempted_at"],
                "properties": {
                    "distinct_id": distinct_id,
                    "$process_person_profile": False,
                    "$geoip_disable": True,
                    "workspace": "gtm",
                    "source_agent": "agent reply agent",
                    "run_id": handoff_id,
                    **{
                        key: outcome.get(key)
                        for key in (
                            "channel",
                            "sender_account",
                            "target_agent_id",
                            "conversation_id",
                            "action_type",
                            "response_form",
                            "provider_status",
                            "recorded",
                            "provider_message_id",
                            "permalink",
                            "crm_category",
                        )
                    },
                },
            }
        ],
    }
    handoff_path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n")
    return handoff_path


def _existing_result(
    item: Dict[str, Any], output_dir: Path, *, allow_uncertain: bool = True
) -> Optional[Dict[str, Any]]:
    key = idempotency_key(item)
    existing = output_dir / "ops-agents" / f"{key}.crm.json"
    if existing.is_file():
        crm = json.loads(existing.read_text())
        payload = crm["operations"][0]["payload"]
        replayable = {"confirmed", "uncertain"} if allow_uncertain else {"confirmed"}
        if payload.get("provider_status") in replayable:
            posthog_handoff = _write_posthog_handoff(output_dir, payload)
            return {
                **payload,
                "crm_handoff": str(existing),
                "posthog_handoff": str(posthog_handoff),
                "replayed": True,
            }
    return None


def _record_outcome(
    item: Dict[str, Any],
    output_dir: Path,
    *,
    result: Optional[ProviderResult],
    status: str,
    error_code: Optional[str] = None,
    attempted_at: Optional[str] = None,
) -> Dict[str, Any]:
    key = idempotency_key(item)
    outcome = {
        "idempotency_key": key,
        "channel": item["channel"],
        "sender_account": item["sender_account"],
        "target_agent_id": item.get("target_agent_id"),
        "target_reference": item.get("target_reference"),
        "conversation_id": item["conversation_id"],
        "parent_url": item["parent_url"],
        "action_type": "public_reply",
        "response_form": item["response_form"],
        "outbound_text": item["outbound_text"],
        "resource_version": item.get("resource_version"),
        "attempted_at": attempted_at or _stamp(),
        "provider_status": status,
        "recorded": status == "confirmed",
        "provider_message_id": result.message_id if result else None,
        "permalink": result.permalink if result else None,
        "error_code": error_code,
        "crm_category": item.get("crm_category"),
    }
    crm_path = _write_crm_handoff(output_dir, outcome)
    posthog_path = _write_posthog_handoff(output_dir, outcome)
    return {
        **outcome,
        "crm_handoff": str(crm_path),
        "posthog_handoff": str(posthog_path),
    }


def record_external(
    raw_item: Dict[str, Any], output_dir: Path, receipt: Dict[str, Any]
) -> Dict[str, Any]:
    """Record a reply sent through a connected provider rather than an API token."""
    item = validate(raw_item)
    prior = _existing_result(item, output_dir, allow_uncertain=False)
    if prior:
        return prior
    if not isinstance(receipt, dict):
        raise ValueError("provider receipt must be an object")
    result = ProviderResult(
        _required(receipt.get("message_id"), "receipt.message_id"),
        _required(receipt.get("permalink"), "receipt.permalink"),
    )
    return _record_outcome(
        item,
        output_dir,
        result=result,
        status="confirmed",
        attempted_at=receipt.get("sent_at"),
    )


def run_once(
    raw_item: Dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool = False,
    http: Optional[JsonHttp] = None,
) -> Dict[str, Any]:
    item = validate(raw_item)
    prior = _existing_result(item, output_dir)
    if prior:
        return prior
    if dry_run:
        return _record_outcome(
            item, output_dir, result=None, status="failed", error_code="dry_run_no_send"
        )
    try:
        result = SENDERS[item["channel"]](item, http or JsonHttp())
    except ProviderFailure as exc:
        return _record_outcome(
            item,
            output_dir,
            result=exc.result,
            status="uncertain" if exc.uncertain else "failed",
            error_code=exc.code,
        )
    return _record_outcome(item, output_dir, result=result, status="confirmed")
