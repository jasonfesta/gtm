"""Comparison keys for contact deduplication; preserve original values separately."""

from urllib.parse import urlsplit


def norm(kind, value):
    if kind == "email":
        return value.casefold().strip()
    u = urlsplit(value)
    host = u.hostname or ""
    if host.endswith("linkedin.com"):
        host = "linkedin.com"
    elif host in ("twitter.com", "www.twitter.com", "x.com", "www.x.com"):
        host = "x.com"
    return host + u.path.rstrip("/").casefold()
