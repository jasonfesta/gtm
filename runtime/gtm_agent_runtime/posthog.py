"""Submit prepared operation-agent events to PostHog in one manual batch."""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_ID = "121185"


class ProjectTokenPostHog:
    """Capture events with the existing PostHog project token."""

    def __init__(self):
        if os.getenv("POSTHOG_PROJECT_ID") != PROJECT_ID:
            raise ValueError("unexpected PostHog project")
        self.token = os.getenv("POSTHOG_PROJECT_TOKEN")
        if not self.token:
            raise ValueError("PostHog project token unavailable")

    def verify_project(self):
        return None

    def capture_batch(self, events):
        import requests

        response = requests.post(
            "https://us.i.posthog.com/batch/",
            json={"api_key": self.token, "batch": events},
            timeout=30,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"PostHog capture failed: HTTP {response.status_code}")
        return {"http_status": response.status_code}


def read_handoff(path: Path) -> list[dict]:
    handoff = json.loads(path.read_text(encoding="utf-8"))
    events = handoff.get("events") if isinstance(handoff, dict) else None
    if not isinstance(events, list) or not events:
        raise ValueError(f"{path.name}: non-empty events list required")
    for event in events:
        if not isinstance(event, dict) or not all(
            event.get(field) for field in ("event", "uuid", "timestamp", "properties")
        ):
            raise ValueError(f"{path.name}: incomplete PostHog event")
        if not isinstance(event["properties"], dict):
            raise ValueError(f"{path.name}: event properties must be an object")
    return events


def submit_inbox(inbox: Path, *, client=None, dry_run=False) -> dict:
    inbox = Path(inbox)
    files = sorted(path for path in inbox.glob("*.json") if path.is_file())
    if not files:
        return {"files": 0, "events": 0, "status": "empty"}
    events = [event for path in files for event in read_handoff(path)]
    if dry_run:
        return {"files": len(files), "events": len(events), "status": "dry_run"}

    submitted = inbox / "submitted"
    for path in files:
        if (submitted / path.name).exists():
            raise ValueError(f"already submitted: {path.name}")

    if client is None:
        client = ProjectTokenPostHog()
    client.verify_project()
    capture = client.capture_batch(events)

    submitted.mkdir(parents=True, exist_ok=True)
    for path in files:
        path.replace(submitted / path.name)
    return {"files": len(files), "events": len(events), "status": "submitted", **(capture or {})}


def submit_handoff(path: Path, *, client=None, dry_run=False) -> dict:
    """Submit one prepared file without touching other pending handoffs."""
    path = Path(path)
    events = read_handoff(path)
    if dry_run:
        return {"file": str(path), "events": len(events), "status": "dry_run"}
    submitted = path.parent / "submitted"
    if (submitted / path.name).exists():
        raise ValueError(f"already submitted: {path.name}")
    if client is None:
        client = ProjectTokenPostHog()
    client.verify_project()
    capture = client.capture_batch(events)
    submitted.mkdir(parents=True, exist_ok=True)
    path.replace(submitted / path.name)
    return {
        "file": str(submitted / path.name),
        "events": len(events),
        "status": "submitted",
        **(capture or {}),
    }
