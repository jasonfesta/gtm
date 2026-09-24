"""Crawl configured agent directories and select active projects for one block."""

from __future__ import annotations

import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

GITHUB_REPO = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", re.I)
HTML_HREF = re.compile(r"href=[\"']([^\"']+)[\"']", re.I)
SKIP_REPOS = {"topics", "search", "features", "marketplace", "collections"}


def parse_time(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def repo_slug(value):
    value = str(value or "").strip().rstrip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        return value
    match = GITHUB_REPO.search(value)
    if not match or match.group(1).casefold() in SKIP_REPOS:
        return None
    return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"


def unique(values):
    output = []
    seen = set()
    for value in values:
        slug = repo_slug(value)
        key = (slug or "").casefold()
        if slug and key not in seen:
            seen.add(key)
            output.append(slug)
    return output


class Client:
    def __init__(
        self,
        *,
        token_env="GITHUB_TOKEN",
        credentials_file=None,
        opener=None,
        timeout=30,
        request_limit=None,
    ):
        self.token = os.environ.get(token_env, "").strip()
        if not self.token and credentials_file:
            path = Path(credentials_file)
            if path.is_file():
                self.token = str(json.loads(path.read_text()).get(token_env, "")).strip()
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.request_limit = request_limit
        self.requests_made = 0

    def _read(self, url, *, accept="application/json"):
        if self.request_limit is not None and self.requests_made >= self.request_limit:
            raise RuntimeError("manual discovery HTTP request budget exhausted")
        self.requests_made += 1
        headers = {"Accept": accept, "User-Agent": "darwin-agent-discovery/1"}
        request = urllib.request.Request(url, headers=headers)
        target = urllib.parse.urlsplit(url)
        if self.token and target.scheme == "https" and target.hostname == "api.github.com":
            request.add_unredirected_header("Authorization", "Bearer " + self.token)
        with self.opener(request, timeout=self.timeout) as response:
            return response.read()

    def json(self, url):
        return json.loads(self._read(url).decode())

    def text(self, url):
        return self._read(url, accept="text/html,*/*").decode(errors="replace")

    def repo(self, slug):
        quoted = urllib.parse.quote(slug, safe="/")
        return self.json(f"https://api.github.com/repos/{quoted}")

    def latest_commit(self, slug):
        quoted = urllib.parse.quote(slug, safe="/")
        payload = self.json(f"https://api.github.com/repos/{quoted}/commits?per_page=10")
        for commit in payload:
            author = commit.get("author") or {}
            login = str(author.get("login") or "")
            if login and not login.casefold().endswith("[bot]"):
                return commit
        return None

    def readme(self, slug):
        quoted = urllib.parse.quote(slug, safe="/")
        return self._read(
            f"https://api.github.com/repos/{quoted}/readme",
            accept="application/vnd.github.raw+json",
        ).decode(errors="replace")


def source_repositories(source, client):
    kind = source["kind"]
    if kind == "github_repo":
        return unique([source["url"]])
    if kind == "github_readme":
        slug = repo_slug(source["url"])
        body = client.readme(slug)
        return unique(match.group(0) for match in GITHUB_REPO.finditer(body))
    if kind == "json_index":
        payload = client.json(source["url"])
        rows = payload.get(source.get("items_key", "skills"), [])
        field = source.get("repo_field", "repo")
        return unique(row.get(field) for row in rows)
    if kind in {"html", "reddit"}:
        body = client.text(source["url"])
        return unique(match.group(0) for match in GITHUB_REPO.finditer(body))
    if kind == "clawhub":
        body = client.text(source["url"])
        detail_pattern = re.compile(
            source.get(
                "detail_path_pattern",
                r"^/[^/]+/(?:skills|plugins)/[^/?#]+/?$",
            ),
            re.I,
        )
        detail_urls = []
        seen = set()
        for href in HTML_HREF.findall(body):
            parsed = urllib.parse.urlparse(urllib.parse.urljoin(source["url"], href))
            if parsed.netloc.casefold() != urllib.parse.urlparse(source["url"]).netloc.casefold():
                continue
            if not detail_pattern.search(parsed.path):
                continue
            url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
            if url not in seen:
                seen.add(url)
                detail_urls.append(url)
        repositories = []
        for url in detail_urls[: int(source.get("detail_limit", 500))]:
            detail = client.text(url)
            repositories.extend(match.group(0) for match in GITHUB_REPO.finditer(detail))
        return unique(repositories)
    raise ValueError(f"unsupported directory source kind: {kind}")


def active_candidate(source, slug, position, client, *, as_of, project_days):
    repo = client.repo(slug)
    if repo.get("archived") or repo.get("disabled"):
        return None
    pushed_at = parse_time(repo.get("pushed_at"))
    if not pushed_at or pushed_at < as_of - timedelta(days=project_days):
        return None
    commit = client.latest_commit(slug)
    if not commit:
        return None
    commit_date = parse_time(((commit.get("commit") or {}).get("author") or {}).get("date"))
    author = commit.get("author") or {}
    developer_login = author.get("login")
    if not commit_date or not developer_login:
        return None
    return {
        "index_id": "github:" + slug.casefold(),
        "source_name": source["id"],
        "source_url": source["url"],
        "source_position": position,
        "name": repo.get("name") or slug.split("/", 1)[1],
        "description": repo.get("description") or "",
        "website_url": repo.get("homepage") or repo.get("html_url"),
        "repository_url": repo.get("html_url") or "https://github.com/" + slug,
        "updated_at": pushed_at.isoformat().replace("+00:00", "Z"),
        "stars": int(repo.get("stargazers_count") or 0),
        "forks": int(repo.get("forks_count") or 0),
        "latest_commit_at": commit_date.isoformat().replace("+00:00", "Z"),
        "owner_developer_evidence": {
            "relationship": "recent_repository_contributor",
            "github_login": developer_login,
            "github_url": author.get("html_url"),
            "commit_url": commit.get("html_url"),
            "commit_at": commit_date.isoformat().replace("+00:00", "Z"),
        },
    }


def enrich_public_evidence(records, client, *, max_characters=16000):
    """Attach bounded public README evidence, preserving failures for review."""
    output = []
    for record in records:
        item = dict(record)
        slug = repo_slug(item.get("repository_url"))
        if slug:
            try:
                item["readme_excerpt"] = client.readme(slug)[:max_characters]
                item["readme_source_url"] = item["repository_url"].rstrip("/") + "#readme"
                item["observed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            except Exception as exc:
                item["readme_hold"] = type(exc).__name__ + (
                    ": HTTP " + str(exc.code) if hasattr(exc, "code") else ""
                )
        output.append(item)
    return output


def score(record, as_of, developer_days):
    commit_at = parse_time(record["latest_commit_at"])
    age_days = max(0, (as_of - commit_at).total_seconds() / 86400)
    if age_days > developer_days:
        return None
    freshness = max(0, developer_days - age_days) / developer_days * 60
    popularity = min(30, math.log10(record["stars"] + 1) * 10)
    source_rank = max(0, 10 - record["source_position"] / 10)
    return round(freshness + popularity + source_rank, 3)


def rank_candidates(records, *, as_of, developer_days, limit):
    ranked = []
    for record in records:
        value = score(record, as_of, developer_days)
        if value is None:
            continue
        ranked.append(dict(record, discovery_score=value))
    ranked.sort(
        key=lambda row: (
            -row["discovery_score"],
            -row["stars"],
            row["repository_url"].casefold(),
        )
    )
    return ranked[:limit]


def load_checkpoint(path):
    path = Path(path)
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def save_checkpoint(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def crawl(config, checkpoint_path, *, client=None, as_of=None):
    """Checkpoint acquisition incrementally; mark processed only after handoffs."""
    client = client or Client(
        token_env=config.get("github_token_env", "GITHUB_TOKEN"),
        credentials_file=config.get("github_credentials_file"),
        timeout=config.get("timeout_seconds", 30),
        request_limit=int(config.get("max_http_requests", 1500)),
    )
    as_of = as_of or datetime.now(timezone.utc)
    daily_target = int(config.get("daily_target", 250))
    total_target = int(config.get("total_target", 600))
    candidate_limit = int(config.get("max_candidates_per_run", daily_target * 3))
    pool_size = int(config.get("candidate_pool_per_source", daily_target * 2))
    check_budget = int(config.get("max_repository_checks", candidate_limit))
    if min(candidate_limit, pool_size, check_budget) < 1:
        raise ValueError("candidate and repository budgets must be positive")
    checks = 0
    project_days = int(config.get("active_project_days", 30))
    developer_days = int(config.get("active_developer_days", 30))
    state = load_checkpoint(checkpoint_path)
    processed = set(state.get("_processed", []))
    qualified = set(state.get("_qualified", []))
    candidates_by_id = {
        record["index_id"]: record
        for record in state.get("_pending", [])
        if record.get("index_id") not in processed
    }
    source_results = []

    if len(qualified) >= total_target:
        return [], [], state

    sources = config["sources"] if len(candidates_by_id) < candidate_limit else []
    source_start = int(state.get("_next_source", 0)) % max(1, len(sources))
    sources = sources[source_start:] + sources[:source_start]
    for source_number, source in enumerate(sources):
        if checks >= check_budget:
            break
        try:
            slugs = source_repositories(source, client)
        except Exception as exc:
            source_results.append({"source_id": source["id"], "error": str(exc)})
            continue
        start = int((state.get(source["id"]) or {}).get("offset", 0))
        if start >= len(slugs):
            start = 0
        inspected = selected_count = errors = 0
        next_offset = start
        for position, slug in enumerate(slugs[start : start + pool_size], start=start):
            index_id = "github:" + slug.casefold()
            if index_id not in processed and index_id not in candidates_by_id:
                if checks >= check_budget:
                    break
                checks += 1
                inspected += 1
                try:
                    candidate = active_candidate(
                        source,
                        slug,
                        position,
                        client,
                        as_of=as_of,
                        project_days=project_days,
                    )
                except Exception:
                    # Leave this repository at the cursor for a later manual retry.
                    errors += 1
                    break
                ranked = rank_candidates(
                    [candidate] if candidate else [],
                    as_of=as_of,
                    developer_days=developer_days,
                    limit=1,
                )
                for record in ranked:
                    candidates_by_id[record["index_id"]] = record
                    selected_count += 1
            next_offset = (position + 1) % max(1, len(slugs))
            state[source["id"]] = {
                "offset": next_offset,
                "available": len(slugs),
                "last_selected": selected_count,
                "candidate_errors": errors,
                "updated_at": as_of.isoformat().replace("+00:00", "Z"),
            }
            state["_pending"] = list(candidates_by_id.values())
            # Rotate sources between passes so a large directory cannot starve others.
            state["_next_source"] = (source_start + source_number + 1) % len(sources)
            save_checkpoint(checkpoint_path, state)
        source_results.append(
            {
                "source_id": source["id"],
                "available": len(slugs),
                "inspected": inspected,
                "selected": selected_count,
                "candidate_errors": errors,
                "next_offset": next_offset,
            }
        )
    if not candidates_by_id and any(
        "error" in row or row.get("candidate_errors", 0) > 0 for row in source_results
    ):
        raise RuntimeError("directory sources failed; no candidates were available")
    state["_pending"] = sorted(
        candidates_by_id.values(),
        key=lambda row: (-row["discovery_score"], row["index_id"]),
    )
    selected = sorted(
        candidates_by_id.values(),
        key=lambda row: (
            -row["discovery_score"],
            -row["stars"],
            row["repository_url"].casefold(),
        ),
    )[:candidate_limit]
    return selected, source_results, state


def commit(checkpoint_path, state, *, processed_ids, qualified_ids):
    """Record only candidates whose handoffs were written successfully."""
    processed = set(state.get("_processed", [])) | set(processed_ids)
    qualified = set(state.get("_qualified", [])) | set(qualified_ids)
    state["_processed"] = sorted(processed)
    state["_qualified"] = sorted(qualified)
    state["_pending"] = [
        row for row in state.get("_pending", []) if row["index_id"] not in processed
    ]
    state["_total_selected"] = len(qualified)
    save_checkpoint(checkpoint_path, state)
