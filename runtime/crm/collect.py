from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import random
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.robotparser import RobotFileParser

from .cli import DEFAULT_DB, ROOT, initialize, stable_id, utc_now

USER_AGENT = "LocalFolderruntime/0.1 (public-research; respects-robots-and-rate-limits)"
PILOT_SLUGS = ("orchid", "folk", "asmi", "catch", "lucas")
AB_CATEGORIES = (
    "https://assistantbenchmark.com/",
    "https://assistantbenchmark.com/?kind=travel",
    "https://assistantbenchmark.com/?kind=email",
    "https://assistantbenchmark.com/?kind=shopping",
    "https://assistantbenchmark.com/?kind=games",
    "https://assistantbenchmark.com/?kind=work",
    "https://assistantbenchmark.com/?kind=infra",
)
IM_SITEMAP = "https://www.imessage.store/sitemap.xml"
IM_INFRA_CATEGORIES = (
    "https://www.imessage.store/category/apis-platforms",
    "https://www.imessage.store/category/open-source",
    "https://www.imessage.store/category/apple-official",
)


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.casefold() == "a":
            self._href = dict(attrs).get("href")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._parts).split())))
            self._href = None
            self._parts = []


class JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capture = False
        self._parts: List[str] = []
        self.blocks: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if (
            tag.casefold() == "script"
            and (dict(attrs).get("type") or "").casefold() == "application/ld+json"
        ):
            self._capture = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._capture:
            self.blocks.append("".join(self._parts))
            self._capture = False
            self._parts = []


class CrawlBlocked(RuntimeError):
    pass


class RespectfulClient:
    def __init__(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        delay_seconds: float = 2.0,
        max_attempts: int = 4,
    ) -> None:
        self.conn = conn
        self.run_id = run_id
        self.delay_seconds = max(1.0, delay_seconds)
        self.max_attempts = max_attempts
        self.last_request: Dict[str, float] = {}
        self.robots: Dict[str, Optional[RobotFileParser]] = {}

    def _wait(self, hostname: str) -> None:
        elapsed = time.monotonic() - self.last_request.get(hostname, 0.0)
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)

    def _raw_request(self, url: str) -> Tuple[int, str, bytes, Dict[str, str]]:
        hostname = urllib.parse.urlsplit(url).hostname or ""
        self._wait(hostname)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xml,text/plain;q=0.9,*/*;q=0.5",
            },
        )
        time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                status = int(response.status)
                content_type = response.headers.get_content_type()
                headers = {k.casefold(): v for k, v in response.headers.items()}
                return status, content_type, body, headers
        finally:
            self.last_request[hostname] = time.monotonic()

    def _robots_for(self, url: str) -> Optional[RobotFileParser]:
        parsed = urllib.parse.urlsplit(url)
        origin = parsed.scheme + "://" + parsed.netloc
        if origin in self.robots:
            return self.robots[origin]
        robots_url = origin + "/robots.txt"
        try:
            status, _, body, _ = self._raw_request(robots_url)
            if status == 200:
                parser = RobotFileParser()
                parser.set_url(robots_url)
                parser.parse(body.decode("utf-8", errors="replace").splitlines())
                self.robots[origin] = parser
            else:
                self.robots[origin] = None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                self.robots[origin] = None
            else:
                raise CrawlBlocked(
                    "Cannot verify robots policy for " + origin + ": HTTP " + str(exc.code)
                )
        except (urllib.error.URLError, TimeoutError) as exc:
            raise CrawlBlocked("Cannot verify robots policy for " + origin + ": " + str(exc))
        return self.robots[origin]

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    @staticmethod
    def _retry_after(headers: Dict[str, str], attempt: int) -> float:
        value = headers.get("retry-after")
        if value:
            try:
                return min(120.0, max(1.0, float(value)))
            except ValueError:
                try:
                    when = parsedate_to_datetime(value)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=dt.timezone.utc)
                    return min(
                        120.0, max(1.0, (when - dt.datetime.now(dt.timezone.utc)).total_seconds())
                    )
                except (TypeError, ValueError):
                    pass
        return min(60.0, (2 ** (attempt - 1)) + random.random())

    def fetch(self, url: str, source_site: str) -> Tuple[str, str]:
        if not self.allowed(url):
            raise CrawlBlocked("robots.txt disallows " + url)
        headers: Dict[str, str] = {}
        for attempt in range(1, self.max_attempts + 1):
            fetch_id = stable_id("fet", self.run_id, url, str(attempt))
            requested_at = utc_now()
            started = time.monotonic()
            try:
                status, content_type, body, headers = self._raw_request(url)
                digest = hashlib.sha256(body).hexdigest()
                archive_dir = ROOT / "evidence" / "raw" / source_site / self.run_id
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = archive_dir / (digest + ".gz")
                if not archive_path.exists():
                    with gzip.open(str(archive_path), "wb") as handle:
                        handle.write(body)
                self.conn.execute(
                    """
                    INSERT INTO fetches(
                        fetch_id, run_id, url, requested_at, http_status, content_type,
                        content_sha256, archived_path, robots_allowed, attempt, duration_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        fetch_id,
                        self.run_id,
                        url,
                        requested_at,
                        status,
                        content_type,
                        digest,
                        str(archive_path.relative_to(ROOT)),
                        attempt,
                        int((time.monotonic() - started) * 1000),
                    ),
                )
                self.conn.commit()
                return body.decode("utf-8", errors="replace"), digest
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                headers = {k.casefold(): v for k, v in exc.headers.items()} if exc.headers else {}
                retry = status == 429 or 500 <= status < 600
                wait_for = self._retry_after(headers, attempt) if retry else None
                self.conn.execute(
                    """
                    INSERT INTO fetches(fetch_id, run_id, url, requested_at, http_status, robots_allowed, attempt, retry_after_seconds, error_class, error_message, duration_ms)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'HTTPError', ?, ?)
                    """,
                    (
                        fetch_id,
                        self.run_id,
                        url,
                        requested_at,
                        status,
                        attempt,
                        int(wait_for or 0),
                        str(exc),
                        int((time.monotonic() - started) * 1000),
                    ),
                )
                self.conn.commit()
                if not retry or attempt == self.max_attempts:
                    raise
                time.sleep(wait_for or 1.0)
            except (urllib.error.URLError, TimeoutError) as exc:
                wait_for = self._retry_after(headers, attempt)
                self.conn.execute(
                    """
                    INSERT INTO fetches(fetch_id, run_id, url, requested_at, robots_allowed, attempt, retry_after_seconds, error_class, error_message, duration_ms)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        fetch_id,
                        self.run_id,
                        url,
                        requested_at,
                        attempt,
                        int(wait_for),
                        type(exc).__name__,
                        str(exc),
                        int((time.monotonic() - started) * 1000),
                    ),
                )
                self.conn.commit()
                if attempt == self.max_attempts:
                    raise
                time.sleep(wait_for)
        raise RuntimeError("unreachable")


def links_matching(body: str, base_url: str, path_prefix: str) -> Dict[str, str]:
    parser = AnchorParser()
    parser.feed(body)
    result: Dict[str, str] = {}
    for href, name in parser.links:
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlsplit(absolute)
        if parsed.path.startswith(path_prefix):
            result[absolute] = name
    return result


def parse_imessage_detail(body: str, url: str) -> Dict[str, object]:
    parser = JsonLdParser()
    parser.feed(body)
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", []) if isinstance(data, dict) else []
        if isinstance(data, dict) and data.get("@type") == "SoftwareApplication":
            nodes = [data]
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "SoftwareApplication":
                same_as = node.get("sameAs") or []
                return {
                    "name": node.get("name"),
                    "description": node.get("description"),
                    "category": node.get("applicationCategory"),
                    "official_url": same_as[0] if same_as else None,
                    "source_url": url,
                }
    raise ValueError("No SoftwareApplication JSON-LD found at " + url)


def parse_ab_detail(body: str, url: str) -> Dict[str, object]:
    def capture(pattern: str) -> Optional[str]:
        match = re.search(pattern, body, flags=re.DOTALL | re.IGNORECASE)
        return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip() if match else None

    name = capture(r'<h1[^>]*class="ag-name"[^>]*>(.*?)</h1>')
    description = capture(r'<p[^>]*class="ag-tag"[^>]*>(.*?)</p>')
    website_match = re.search(
        r'<a[^>]*class="ag-dev"[^>]*href="([^"]+)"', body, flags=re.IGNORECASE
    )
    if not name:
        raise ValueError("No agent name found at " + url)
    return {
        "name": name,
        "description": description,
        "category": None,
        "official_url": html.unescape(website_match.group(1)) if website_match else None,
        "source_url": url,
    }


def discover_urls(client: RespectfulClient, source: str, mode: str) -> Tuple[List[str], Set[str]]:
    if source == "assistantbenchmark":
        if mode == "pilot":
            return (
                ["https://assistantbenchmark.com/agents/" + slug for slug in PILOT_SLUGS],
                set(),
            )
        found: Set[str] = set()
        for category_url in AB_CATEGORIES:
            body, _ = client.fetch(category_url, source)
            found.update(links_matching(body, category_url, "/agents/").keys())
        return (sorted(found), set())
    if source == "imessage_store":
        if mode == "pilot":
            return (["https://www.imessage.store/agent/" + slug for slug in PILOT_SLUGS], set())
        sitemap_body, _ = client.fetch(IM_SITEMAP, source)
        root = ET.fromstring(sitemap_body)
        found = {
            (node.text or "").strip()
            for node in root.findall(
                "{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
            )
            if "/agent/" in (node.text or "")
        }
        infra: Set[str] = set()
        for category_url in IM_INFRA_CATEGORIES:
            body, _ = client.fetch(category_url, source)
            infra.update(links_matching(body, category_url, "/agent/").keys())
        return (sorted(found), infra)
    raise ValueError(source)


def upsert_candidate(
    conn: sqlite3.Connection, source: str, run_id: str, record: Dict[str, object], listing_kind: str
) -> None:
    source_url = str(record["source_url"])
    slug = urllib.parse.urlsplit(source_url).path.rstrip("/").split("/")[-1]
    candidate_id = stable_id("can", source, slug)
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO discovery_candidates(
            candidate_id, source_site, source_key, source_url, source_name,
            source_description, source_category, official_url, listing_kind,
            raw_record_json, candidate_status, run_id, discovered_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
        ON CONFLICT(source_site, source_key) DO UPDATE SET
            source_url=excluded.source_url,
            source_name=excluded.source_name,
            source_description=excluded.source_description,
            source_category=excluded.source_category,
            official_url=excluded.official_url,
            listing_kind=excluded.listing_kind,
            raw_record_json=excluded.raw_record_json,
            run_id=excluded.run_id,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
        """,
        (
            candidate_id,
            source,
            slug,
            source_url,
            record.get("name"),
            record.get("description"),
            record.get("category"),
            record.get("official_url"),
            listing_kind,
            payload,
            run_id,
            utc_now(),
        ),
    )


def run_collection(
    db_path: Path, sources: Iterable[str], mode: str, delay: float, approved_full: bool
) -> int:
    if mode == "full" and not approved_full:
        raise SystemExit(
            "Full crawl is gated. Re-run only after review with --approved-full-crawl."
        )
    conn = initialize(db_path)
    total_errors = 0
    total_records = 0
    for source in sources:
        run_id = "run_" + source + "_" + uuid.uuid4().hex[:12]
        started = utc_now()
        conn.execute(
            "INSERT INTO crawl_runs(run_id, source_site, mode, status, started_at) VALUES (?, ?, ?, 'started', ?)",
            (run_id, source, mode, started),
        )
        conn.commit()
        client = RespectfulClient(conn, run_id, delay_seconds=delay)
        errors = 0
        discovered = 0
        try:
            urls, infra_urls = discover_urls(client, source, mode)
            for url in urls:
                try:
                    body, _ = client.fetch(url, source)
                    record = (
                        parse_ab_detail(body, url)
                        if source == "assistantbenchmark"
                        else parse_imessage_detail(body, url)
                    )
                    upsert_candidate(
                        conn,
                        source,
                        run_id,
                        record,
                        "infrastructure_provider" if url in infra_urls else "agent",
                    )
                    discovered += 1
                    conn.commit()
                except (
                    CrawlBlocked,
                    urllib.error.URLError,
                    urllib.error.HTTPError,
                    TimeoutError,
                    ValueError,
                ) as exc:
                    errors += 1
                    note_path = ROOT / "logs" / (run_id + ".jsonl")
                    with note_path.open("a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(
                                {
                                    "url": url,
                                    "error": type(exc).__name__,
                                    "message": str(exc),
                                    "at": utc_now(),
                                }
                            )
                            + "\n"
                        )
            status = "complete" if errors == 0 else "partial"
        except Exception:
            conn.execute(
                "UPDATE crawl_runs SET status='failed', finished_at=?, error_count=? WHERE run_id=?",
                (utc_now(), errors + 1, run_id),
            )
            conn.commit()
            conn.close()
            raise
        conn.execute(
            """
            UPDATE crawl_runs SET status=?, finished_at=?, request_count=(SELECT count(*) FROM fetches WHERE run_id=?),
                records_discovered=?, records_changed=?, error_count=?, checkpoint_json=?
            WHERE run_id=?
            """,
            (
                status,
                utc_now(),
                run_id,
                discovered,
                discovered,
                errors,
                json.dumps({"completed_urls": discovered, "mode": mode}),
                run_id,
            ),
        )
        conn.commit()
        total_errors += errors
        total_records += discovered
        print(source + ": " + str(discovered) + " candidates, " + str(errors) + " errors")
    conn.close()
    print("total: " + str(total_records) + " candidates")
    return 1 if total_errors else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Respectful public-page collector; stages candidates for review"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--source", choices=["assistantbenchmark", "imessage_store", "both"], default="both"
    )
    parser.add_argument("--mode", choices=["pilot", "full"], default="pilot")
    parser.add_argument(
        "--delay", type=float, default=2.0, help="Minimum seconds between requests to a host"
    )
    parser.add_argument(
        "--approved-full-crawl",
        action="store_true",
        help="Explicit post-review gate for a full crawl",
    )
    args = parser.parse_args(argv)
    sources = ["assistantbenchmark", "imessage_store"] if args.source == "both" else [args.source]
    return run_collection(args.db, sources, args.mode, args.delay, args.approved_full_crawl)


if __name__ == "__main__":
    raise SystemExit(main())
