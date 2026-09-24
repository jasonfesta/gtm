"""Manual source-backed Apollo contact lookup by name/company or LinkedIn identity."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from crm.local_secrets import apollo_token

MATCH_URL = "https://api.apollo.io/api/v1/people/match"


def _linkedin(value):
    try:
        parsed = urlsplit(str(value or ""))
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or parsed.port
            or not (host == "linkedin.com" or host.endswith(".linkedin.com"))
        ):
            return ""
        path = parsed.path.rstrip("/").lower()
        return "https://www.linkedin.com" + path if re.fullmatch(r"/in/[^/]+", path) else ""
    except ValueError:
        return ""


def _name(value):
    return " ".join(re.findall(r"[^\W_]+", str(value or "").casefold()))


def _company(value):
    name = _name(value)
    return re.sub(r"(?: (?:inc|incorporated|llc|ltd|limited|corp|corporation))+$", "", name)


def _domain(value):
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if (
            parsed.scheme not in {"https", "http"}
            or parsed.username
            or parsed.password
            or parsed.port
        ):
            return ""
        host = (parsed.hostname or "").lower().removeprefix("www.")
        return host if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host) else ""
    except ValueError:
        return ""


def lookup_payload(candidate):
    """Bound the request to one sourced identity, by profile or full name and employer."""
    if not str(candidate.get("identity_key") or "").strip():
        raise ValueError("source identity_key required")
    source = urlsplit(str(candidate.get("source_url") or ""))
    if source.scheme not in {"http", "https"} or not source.hostname:
        raise ValueError("source_url required")
    linkedin = _linkedin(candidate.get("linkedin_url"))
    name = str(candidate.get("name") or "").strip()
    company = str(candidate.get("company") or "").strip()
    domain = _domain(candidate.get("company_domain"))
    if candidate.get("company_domain") and not domain:
        raise ValueError("valid company_domain required")
    if candidate.get("linkedin_url") and not linkedin:
        raise ValueError("valid linkedin_url required")
    if not linkedin and (len(_name(name).split()) < 2 or not _company(company)):
        raise ValueError("full name and company, or source-backed linkedin_url required")
    payload = {
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
        "run_waterfall_email": False,
        "run_waterfall_phone": False,
    }
    if linkedin:
        payload["linkedin_url"] = linkedin
    if name:
        payload["name"] = name
    if company:
        payload["organization_name"] = company
    if domain:
        payload["domain"] = domain
    return payload


def _identity_matches(requested, person, response):
    if response.get("match_confidence", person.get("match_confidence")) in {"low", "none"}:
        return False
    if requested.get("linkedin_url"):
        if _linkedin(person.get("linkedin_url")) != requested["linkedin_url"]:
            return False
    if requested.get("name") and _name(person.get("name")) != _name(requested["name"]):
        return False
    organization = person.get("organization") or {}
    if not isinstance(organization, dict):
        return False
    if requested.get("organization_name") and _company(organization.get("name")) != _company(
        requested["organization_name"]
    ):
        return False
    if (
        requested.get("domain")
        and _domain(organization.get("primary_domain") or organization.get("website_url"))
        != requested["domain"]
    ):
        return False
    return True


def verification_result(candidate, response, *, observed_at=None):
    """An address and verified status must belong to the exact requested profile."""
    requested = lookup_payload(candidate)
    person = response.get("person") or {}
    if not isinstance(person, dict):
        person = {}
    matched = _identity_matches(requested, person, response)
    email = person.get("email")
    valid_email = isinstance(email, str) and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email))
    placeholder = valid_email and ("***" in email or email.lower().endswith("@domain.com"))
    verified = (
        matched and valid_email and not placeholder and person.get("email_status") == "verified"
    )
    result = {
        "identity_key": candidate["identity_key"],
        "source_url": candidate["source_url"],
        "linkedin_url": _linkedin(person.get("linkedin_url")) if matched else None,
        "provider": "apollo",
        "status": "verified" if verified else "unverified",
        "reason": "verified_email"
        if verified
        else ("identity_mismatch" if not matched else "verified_email_not_returned"),
        "sends": False,
        "requires_ops_identity_and_suppression": True,
    }
    result["identity_matched"] = matched
    result["lookup_method"] = "linkedin" if requested.get("linkedin_url") else "name_company"
    result["match_confidence"] = response.get("match_confidence", person.get("match_confidence"))
    if matched:
        result["contact"] = {
            "provider_person_id": person.get("id"),
            "name": person.get("name"),
            "title": person.get("title"),
            "company": (person.get("organization") or {}).get("name"),
            "linkedin_url": _linkedin(person.get("linkedin_url")),
        }
    if verified:
        result["email"] = email
        result["email_verification_evidence"] = {
            "provider": "apollo",
            "endpoint": MATCH_URL,
            "provider_person_id": person.get("id"),
            "lookup_method": result["lookup_method"],
            "name": person.get("name"),
            "company": (person.get("organization") or {}).get("name"),
            "match_confidence": result["match_confidence"],
            "email": email,
            "email_status": "verified",
            "linkedin_url": _linkedin(person.get("linkedin_url")) if matched else None,
            "observed_at": observed_at or datetime.now(timezone.utc).isoformat(),
            "source_url": candidate["source_url"],
        }
    return result


def lookup(candidate, *, auth=None, opener=None):
    """One explicit native lookup; may consume credits. No retries or scheduling."""
    payload = lookup_payload(candidate)
    auth = apollo_token() if auth is None else auth
    if not auth:
        return {"status": "blocked", "reason": "apollo_token_missing", "sends": False}
    request = urllib.request.Request(
        MATCH_URL,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"x-api-key": auth, "Content-Type": "application/json"},
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=30) as response:
            data = json.loads(response.read())
        if not isinstance(data, dict):
            raise ValueError("invalid response")
    except (urllib.error.URLError, TimeoutError, ValueError):
        # Never expose provider response bodies, credentials, or request contents.
        return {"status": "blocked", "reason": "apollo_lookup_failed", "sends": False}
    return verification_result(candidate, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="source-backed candidate JSON")
    parser.add_argument("--name")
    parser.add_argument("--company")
    parser.add_argument("--company-domain")
    parser.add_argument("--source-url")
    parser.add_argument("--credentials-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input:
        if any((args.name, args.company, args.company_domain, args.source_url)):
            parser.error("use --input or name/company arguments, not both")
        candidate = json.loads(args.input.read_text())
    else:
        if not args.name or not args.company or not args.source_url:
            parser.error("--name, --company and --source-url are required")
        identity = (
            "apollo:name-company:"
            + hashlib.sha256(
                (_name(args.name) + "|" + _company(args.company)).encode()
            ).hexdigest()[:24]
        )
        candidate = {
            "identity_key": identity,
            "name": args.name,
            "company": args.company,
            "company_domain": args.company_domain,
            "source_url": args.source_url,
        }
    lookup_payload(candidate)  # Validate before creating an attempt or requesting provider data.
    fingerprint = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()
    if args.output.exists():
        previous = json.loads(args.output.read_text())
        if previous.get("candidate_sha256") != fingerprint:
            parser.error("output belongs to a different candidate; use a new path")
        print(
            json.dumps(
                {
                    "output": str(args.output.resolve()),
                    "status": previous["status"],
                    "replayed": True,
                }
            )
        )
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    pending = {
        "candidate_sha256": fingerprint,
        "status": "uncertain",
        "sends": False,
        "reason": "lookup_started_requires_readback_if_interrupted",
    }
    with os.fdopen(fd, "w") as stream:
        json.dump(pending, stream)
        stream.flush()
        os.fsync(stream.fileno())
    auth = apollo_token(credentials=args.credentials_dir) if args.credentials_dir else None
    result = {**lookup(candidate, auth=auth), "candidate_sha256": fingerprint}
    fd, temporary = tempfile.mkstemp(prefix=".apollo-", dir=args.output.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "status": result["status"],
                "identity_matched": result.get("identity_matched", False),
            }
        )
    )


if __name__ == "__main__":
    main()
