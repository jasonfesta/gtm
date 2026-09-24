"""Offline, manual Sales Navigator evidence -> Human Reply outbox. Never sends."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .human_discovery import SEARCH_TERMS


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def linkedin_path(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in {"linkedin.com", "www.linkedin.com"}
        or parts.username
        or parts.password
        or parts.port
    ):
        raise ValueError("expected HTTPS LinkedIn URL")
    return unquote(parts.path).rstrip("/")


def profile(url: str) -> str:
    path = linkedin_path(url)
    if not re.fullmatch(r"/in/[\w%-]+", path):
        raise ValueError("public profile permalink required")
    return "https://www.linkedin.com" + path.lower() + "/"


def validate(row: dict) -> dict:
    for key in ("name", "observed_at", "search_query", "ai_work_evidence", "relevance"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise ValueError(f"missing {key}")
    if datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("observed_at must include timezone")
    if row.get("source") != "sales_navigator_manual":
        raise ValueError("Sales Navigator provenance required; Apify is ancillary only")
    if linkedin_path(row["search_url"]) != "/sales/search/people":
        raise ValueError("Sales Navigator people search required")
    lead = re.fullmatch(
        r"/sales/(?:lead|people)/([^/,]+)(?:,[^/]+)?", linkedin_path(row["sales_profile_url"])
    )
    if not lead:
        raise ValueError("Sales Navigator person identity required")
    identity = profile(row["profile_url"])
    post = row.get("post", {})
    if profile(post.get("author_profile_url", "")) != identity:
        raise ValueError("post author does not match profile identity")
    path = linkedin_path(post.get("url", ""))
    match = re.fullmatch(r"/feed/update/urn:li:activity:(\d+)", path)
    if not match:
        match = re.fullmatch(r"/posts/[^/]+-activity-(\d+)-[^/]+", path)
    if not match:
        raise ValueError("observed public activity permalink required, not profile/share URL")
    if post.get("verified") is not True or post.get("comment_available") is not True:
        raise ValueError("post readback and reply control must be observed")
    body = post.get("text", "")
    terms = [
        term
        for term in SEARCH_TERMS
        if re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", body, re.I)
    ]
    if not terms or row["ai_work_evidence"] not in body:
        raise ValueError("post must contain a canonical tool and exact AI work evidence excerpt")
    evidence = {}
    for stage in ("search", "identity", "post"):
        reference = row.get("evidence", {}).get(stage, {})
        evidence_path = Path(reference.get("path", ""))
        if not evidence_path.is_absolute() or not evidence_path.is_file():
            raise ValueError(f"missing local {stage} evidence file")
        actual = digest(evidence_path.read_bytes())
        if reference.get("sha256") != actual:
            raise ValueError(f"{stage} evidence hash mismatch")
        evidence[stage] = {"path": str(evidence_path), "sha256": actual}
    return {
        **row,
        "profile_url": identity,
        "sales_identity": lead.group(1),
        "post": {
            **post,
            "url": "https://www.linkedin.com" + path + "/",
            "activity_id": match.group(1),
        },
        "matched_tools": terms,
        "evidence": evidence,
        "dedup_key": "linkedin:activity:" + match.group(1),
    }


def write_handoff(input_path: Path, output_dir: Path) -> Path:
    raw = input_path.read_bytes()
    payload = json.loads(raw)
    rows = payload["candidates"]
    if not isinstance(rows, list):
        raise ValueError("candidates must be a list")
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = output_dir / (digest(raw) + ".linkedin-handoff.json")
    with (output_dir / ".linkedin-handoff.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            return target  # Replay returns the same durable outbox, never loses it.
        seen_posts, seen_people = set(), set()
        for previous in output_dir.glob("*.linkedin-handoff.json"):
            for item in json.loads(previous.read_text())["candidates"]:
                seen_posts.add(item["dedup_key"])
        ready, held = [], []
        for index, row in enumerate(rows):
            try:
                item = validate(row)
                if item["dedup_key"] in seen_posts:
                    raise ValueError("duplicate post already in durable outbox")
                if item["profile_url"] in seen_people or item["sales_identity"] in seen_people:
                    raise ValueError("duplicate person within batch")
                seen_posts.add(item["dedup_key"])
                seen_people.update((item["profile_url"], item["sales_identity"]))
                ready.append(item)
            except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
                held.append({"input_index": index, "reason": str(error)})
        result = {
            "schema": "linkedin_sales_handoff_v1",
            "handoff_id": digest(raw),
            "source_agent": "human_discovery",
            "recipient": "human_reply",
            "status": "prepared_not_sent",
            "input_sha256": digest(raw),
            "candidates": ready,
            "held": held,
        }
        fd, temporary = tempfile.mkstemp(prefix=".linkedin-", dir=output_dir)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(write_handoff(args.input, args.output_dir).resolve())


if __name__ == "__main__":
    main()
