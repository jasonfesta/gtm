"""Find the company roster with Apify, then emails with Apollo. Never logs tokens."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from crm.local_secrets import apify_token, apollo_token

APIFY_ME = "https://api.apify.com/v2/users/me"
APOLLO_HEALTH = "https://api.apollo.io/api/v1/auth/health"
APOLLO_SEARCH = "https://api.apollo.io/api/v1/mixed_people/search"
APOLLO_ENRICH = "https://api.apollo.io/api/v1/people/bulk_match"

# Cheapest store actors for people, not posts. Do not start as a ping.
ACTORS = {
    "linkedin": "harvestapi/linkedin-company-employees",
    "x": "kaitoeasyapi/twitter-x-data-tweet-scraper-pay-per-result-cheapest",
}


def _get(url, headers, opener=None):
    request = urllib.request.Request(url, headers=headers, method="GET")
    open_url = opener or urllib.request.urlopen
    with open_url(request) as response:
        return json.loads(response.read().decode())


def _post(url, headers, payload, opener=None):
    body = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    open_url = opener or urllib.request.urlopen
    with open_url(request) as response:
        return json.loads(response.read().decode())


def ready(*, opener=None):
    """Token check only. Never start an Actor. Never enrich."""
    apify = apify_token()
    apollo = apollo_token()
    result = {
        "apify": bool(apify),
        "apollo": bool(apollo),
        "ready": bool(apify) and bool(apollo),
        "sends": False,
        "enriches": False,
        "actors": dict(ACTORS),
    }
    if apify:
        try:
            _get(
                APIFY_ME,
                {"Authorization": f"Bearer {apify}"},
                opener=opener,
            )
            result["apify_ok"] = True
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            result["apify_ok"] = False
            result["apify_error"] = type(exc).__name__
    if apollo:
        try:
            _get(
                APOLLO_HEALTH,
                {"x-api-key": apollo, "Content-Type": "application/json"},
                opener=opener,
            )
            result["apollo_ok"] = True
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
            result["apollo_ok"] = False
            result["apollo_error"] = type(exc).__name__
    return result


def host(domain=""):
    raw = (domain or "").strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    return raw.split("/")[0].split("?")[0]


def plan(company, domain="", assistant=""):
    name = (company or assistant or "").strip()
    site = host(domain)
    return {
        "company": name,
        "domain": site,
        "assistant": assistant,
        "apify": [
            {"channel": "linkedin", "actor": ACTORS["linkedin"], "query": name},
            {"channel": "x", "actor": ACTORS["x"], "query": name},
        ],
        "apollo": {
            "search": APOLLO_SEARCH,
            "q_organization_name": name,
            "q_organization_domains": [site] if site else [],
            "enrich": False,
            "reveal_personal_emails": False,
            "reveal_phone_number": False,
        },
        "sends": False,
        "enriches": False,
        "share_order": ["dm", "email"],
        "email_held": True,
        "needs_approval": True,
        "next": (
            "Apify finds the people on LinkedIn and X. "
            "Apollo people search is free. "
            "Agent later picks who, where, and how. Do not send. "
            "Do not start an Actor as a ping. Do not log tokens."
        ),
    }


def search_apollo(company, domain="", *, page=1, per_page=25, opener=None):
    token = apollo_token()
    if not token:
        raise RuntimeError("APOLLO_API_KEY required")
    site = host(domain)
    name = (company or "").strip()
    payload = {"page": page, "per_page": per_page}
    if name:
        payload["q_organization_name"] = name
    if site:
        payload["q_organization_domains"] = [site]
    if not name and not site:
        raise ValueError("company or domain required")
    data = _post(
        APOLLO_SEARCH,
        {"x-api-key": token, "Content-Type": "application/json"},
        payload,
        opener=opener,
    )
    people = data.get("people") or data.get("contacts") or []
    rows = []
    for person in people:
        org = person.get("organization") if isinstance(person.get("organization"), dict) else {}
        rows.append(
            {
                "name": person.get("name")
                or f"{person.get('first_name') or ''} {person.get('last_name') or ''}".strip(),
                "title": person.get("title") or "",
                "linkedin_url": person.get("linkedin_url") or "",
                "email": person.get("email") or "",
                "organization": org.get("name") or company,
                "source": "apollo_search",
                "candidate": True,
            }
        )
    return {
        "company": company,
        "count": len(rows),
        "people": rows,
        "enriches": False,
        "sends": False,
    }


def enrich_apollo(people, *, opener=None):
    """Work emails only. Phone off. Confirmed roster only."""
    token = apollo_token()
    if not token:
        raise RuntimeError("APOLLO_API_KEY required")
    details = []
    for person in people:
        row = {}
        if person.get("linkedin_url"):
            row["linkedin_url"] = person["linkedin_url"]
        if person.get("name"):
            row["name"] = person["name"]
        if person.get("organization") or person.get("domain"):
            row["organization_name"] = person.get("organization") or ""
            if person.get("domain"):
                row["domain"] = person["domain"]
        if row:
            details.append(row)
    data = _post(
        APOLLO_ENRICH,
        {"x-api-key": token, "Content-Type": "application/json"},
        {
            "details": details,
            "reveal_personal_emails": False,
            "reveal_phone_number": False,
        },
        opener=opener,
    )
    matches = data.get("matches") or data.get("people") or []
    rows = []
    for person in matches:
        if not isinstance(person, dict):
            continue
        rows.append(
            {
                "name": person.get("name") or "",
                "title": person.get("title") or "",
                "linkedin_url": person.get("linkedin_url") or "",
                "email": person.get("email") or "",
                "source": "apollo_enrich",
                "candidate": True,
            }
        )
    return {"count": len(rows), "people": rows, "enriches": True, "sends": False}


def person_key(person):
    url = (person.get("linkedin_url") or person.get("x_url") or "").casefold().rstrip("/")
    if url:
        return url
    name = (person.get("name") or "").casefold().strip()
    org = (person.get("organization") or person.get("company") or "").casefold().strip()
    return f"{name}|{org}" if name else ""


def merge_people(*groups):
    seen = {}
    order = []
    for group in groups:
        for person in group or []:
            key = person_key(person)
            if not key:
                continue
            if key not in seen:
                seen[key] = dict(person)
                order.append(key)
                continue
            current = seen[key]
            for field in (
                "title",
                "linkedin_url",
                "x_url",
                "email",
                "organization",
                "company",
                "parent_url",
                "parent_text",
            ):
                if person.get(field) and not current.get(field):
                    current[field] = person[field]
            sources = []
            for row in (current, person):
                src = row.get("source")
                if src and src not in sources:
                    sources.append(src)
            if sources:
                current["sources"] = sources
    return [seen[key] for key in order]


def _linkedin_person(raw, company):
    name = raw.get("name") or raw.get("fullName") or raw.get("full_name") or ""
    first = raw.get("firstName") or raw.get("first_name") or ""
    last = raw.get("lastName") or raw.get("last_name") or ""
    if not name:
        name = f"{first} {last}".strip()
    url = (
        raw.get("linkedinUrl")
        or raw.get("linkedin_url")
        or raw.get("profileUrl")
        or raw.get("url")
        or ""
    )
    current = raw.get("currentPosition") if isinstance(raw.get("currentPosition"), dict) else {}
    title = (
        raw.get("headline")
        or raw.get("title")
        or raw.get("position")
        or current.get("position")
        or ""
    )
    org = raw.get("companyName") or current.get("companyName") or company
    return {
        "name": name,
        "title": title,
        "linkedin_url": url if "linkedin.com" in str(url) else url,
        "x_url": "",
        "email": raw.get("email") or "",
        "organization": org,
        "source": "apify_linkedin",
        "candidate": True,
    }


def _x_person(raw, company):
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    handle = author.get("userName") or raw.get("screen_name") or raw.get("userName") or ""
    name = author.get("name") or raw.get("name") or handle
    url = author.get("url") or (f"https://x.com/{handle}" if handle else "")
    text = raw.get("text") or raw.get("full_text") or ""
    tid = str(raw.get("id") or raw.get("id_str") or "")
    parent = raw.get("url") or (f"https://x.com/{handle}/status/{tid}" if handle and tid else "")
    return {
        "name": name,
        "title": author.get("description") or "",
        "linkedin_url": "",
        "x_url": url,
        "email": "",
        "organization": company,
        "parent_url": parent,
        "parent_text": text,
        "source": "apify_x",
        "candidate": True,
        "mention_only": True,
    }


def search_apify(company, domain="", *, max_linkedin=40, max_x=15, items_fn=None):
    from crm.human_discovery_search import apify_items

    run = items_fn or apify_items
    name = (company or "").strip()
    site = host(domain)
    queries = [part for part in (name, site) if part]
    li_raw, li_run = run(
        ACTORS["linkedin"],
        {
            "profileScraperMode": "Short ($4 per 1k)",
            "maxItems": max_linkedin,
            "companies": [name] if name else queries,
            "companyBatchMode": "all_at_once",
        },
    )
    x_raw, x_run = run(
        ACTORS["x"],
        {
            "searchTerms": queries,
            "maxItems": max_x,
            "queryType": "Latest",
        },
    )
    people = [_linkedin_person(row, name) for row in (li_raw or []) if isinstance(row, dict)]
    people += [_x_person(row, name) for row in (x_raw or []) if isinstance(row, dict)]
    return {
        "linkedin_run": li_run,
        "x_run": x_run,
        "people": [row for row in people if row.get("name")],
        "sends": False,
    }


def looks_at_company(person, company, domain=""):
    org = (person.get("organization") or person.get("company") or "").casefold()
    email = (person.get("email") or "").casefold()
    site = host(domain).casefold()
    if site and email.endswith("@" + site):
        return True
    name = (company or "").casefold()
    return bool(name and name in org)


def gather(
    company,
    domain="",
    assistant="",
    *,
    execute_apify=False,
    enrich=False,
    dest=None,
    pages=3,
    opener=None,
    search_fn=None,
    enrich_fn=None,
    apify_fn=None,
):
    """Pull the roster. Apollo search first. Apify and enrich only when asked. Never sends."""
    outline = plan(company, domain, assistant)
    search = search_fn or search_apollo
    fill = enrich_fn or enrich_apollo
    people = []
    sources = []
    errors = []
    if outline["domain"]:
        try:
            for page in range(1, pages + 1):
                found = search("", outline["domain"], page=page, opener=opener)
                if not found.get("people"):
                    break
                people.extend(found["people"])
                if page == 1:
                    sources.append("apollo_domain")
        except (RuntimeError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            errors.append({"source": "apollo_domain", "error": type(exc).__name__})
    if outline["company"]:
        try:
            found = search(outline["company"], "", opener=opener)
            people.extend(found.get("people") or [])
            sources.append("apollo_name")
        except (RuntimeError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            errors.append({"source": "apollo_name", "error": type(exc).__name__})
    apify_runs = {}
    if execute_apify:
        try:
            found = (apify_fn or search_apify)(outline["company"], outline["domain"])
            people.extend(found.get("people") or [])
            apify_runs = {
                "linkedin": found.get("linkedin_run"),
                "x": found.get("x_run"),
            }
            sources.append("apify")
        except (RuntimeError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            errors.append({"source": "apify", "error": type(exc).__name__})
    merged = merge_people(people)
    if enrich:
        confirmed = [
            row
            for row in merged
            if row.get("linkedin_url")
            and looks_at_company(row, outline["company"], outline["domain"])
        ]
        if confirmed:
            try:
                filled = fill(confirmed, opener=opener)
                merged = merge_people(merged, filled.get("people") or [])
                sources.append("apollo_enrich")
            except (RuntimeError, ValueError, urllib.error.URLError, urllib.error.HTTPError) as exc:
                errors.append({"source": "apollo_enrich", "error": type(exc).__name__})
    payload = {
        **outline,
        "count": len(merged),
        "people": merged,
        "sources": sources,
        "apify_runs": apify_runs,
        "errors": errors,
        "sends": False,
        "enriches": bool(enrich and "apollo_enrich" in sources),
        "share_order": ["dm", "email"],
        "email_held": True,
        "needs_approval": True,
        "next": (
            "Research the people. Main writes findings, why, how to contact, "
            "and four voices. Wait for a human yes. Do not send."
        ),
    }
    if dest:
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n")
        payload["path"] = str(path)
    return payload


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ready", "plan", "gather"))
    parser.add_argument("--company", default="")
    parser.add_argument("--domain", default="")
    parser.add_argument("--assistant", default="")
    parser.add_argument("--dest", default="")
    parser.add_argument("--execute-apify", action="store_true")
    parser.add_argument("--enrich", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "ready":
        print(json.dumps(ready(), indent=2))
        return
    if args.command == "plan":
        print(json.dumps(plan(args.company, args.domain, args.assistant), indent=2))
        return
    print(
        json.dumps(
            gather(
                args.company,
                args.domain,
                args.assistant,
                execute_apify=args.execute_apify,
                enrich=args.enrich,
                dest=args.dest or None,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
