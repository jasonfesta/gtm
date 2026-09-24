"""Which social channels are live. X only for now."""

from __future__ import annotations

LIVE = ("x",)
PAUSED = ("linkedin", "hacker_news", "github", "product_hunt", "apollo")
REASON = "operator: x only for now"


def status():
    return {
        "live": list(LIVE),
        "paused": list(PAUSED),
        "reason": REASON,
    }


def allowed(channel):
    return str(channel or "").strip().casefold() in LIVE


def hold(channel):
    key = str(channel or "").strip().casefold()
    if allowed(key):
        return None
    if key in PAUSED:
        return {
            "send": False,
            "reason": "channel_paused",
            "channel": key,
            "paused": True,
        }
    return {
        "send": False,
        "reason": "channel_not_live",
        "channel": key,
    }
