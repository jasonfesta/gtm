"""Live send mode: automatic or approve every message.

Setup asks once. Unset fails closed: do not send.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from crm.database import ROOT

STATE = ROOT / "accounts/send-mode.json"
MODES = ("automatic", "approve")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _state(state=STATE):
    path = Path(state)
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write(state, payload):
    path = Path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def status(*, state=STATE):
    data = _state(state)
    mode = data.get("mode")
    if mode not in MODES:
        mode = None
    return {
        "mode": mode,
        "ready": mode in MODES,
        "send_live": mode == "automatic",
        "needs_approval": mode != "automatic",
        "updated_at": data.get("updated_at"),
        "reason": data.get("reason") or "",
        "path": "runtime/accounts/send-mode.json",
    }


def set_mode(mode, *, state=STATE, reason=""):
    if mode not in MODES:
        raise ValueError("mode must be automatic or approve")
    payload = {
        "mode": mode,
        "updated_at": _now(),
        "reason": reason,
    }
    _write(state, payload)
    return {**status(state=state), **payload, "path": str(Path(state))}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "set"))
    parser.add_argument("--automatic", action="store_true")
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    if args.command == "status":
        print(json.dumps(status(), indent=2))
        return
    if args.automatic == args.approve:
        raise SystemExit("pass --automatic or --approve")
    mode = "automatic" if args.automatic else "approve"
    print(json.dumps(set_mode(mode, reason=args.reason), indent=2))


if __name__ == "__main__":
    main()
