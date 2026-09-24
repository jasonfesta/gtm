"""One manual scan, compose, reply, and handoff cycle."""

from pathlib import Path
from typing import Any, Dict, Optional

from .agent_reply import JsonHttp, run_once
from .agent_reply_compose import PortkeyComposer
from .agent_reply_scan import Reader, scan_once


def engage_once(
    snapshot: Any,
    config: Dict[str, Any],
    assets: Dict[str, Any],
    output_dir: Path,
    *,
    dry_run: bool = False,
    reader: Optional[Reader] = None,
    composer: Optional[PortkeyComposer] = None,
    sender_http: Optional[JsonHttp] = None,
):
    candidates = scan_once(snapshot, config, reader)
    prepared = (composer or PortkeyComposer(config.get("portkey") or {})).compose(
        candidates, assets
    )
    if prepared is None:
        return {"status": "no_relevant_conversation", "candidates_scanned": len(candidates)}
    return run_once(prepared, output_dir, dry_run=dry_run, http=sender_http)


def prepare_once(
    snapshot: Any,
    config: Dict[str, Any],
    assets: Dict[str, Any],
    *,
    reader: Optional[Reader] = None,
    composer: Optional[PortkeyComposer] = None,
) -> Dict[str, Any]:
    """Select and draft one public reply for a connected provider to send."""
    candidates = scan_once(snapshot, config, reader)
    return (composer or PortkeyComposer(config.get("portkey") or {})).compose(candidates, assets)
