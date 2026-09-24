"""Read-only database coverage reporting; never initiates a crawl."""

import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path

from crm.database import private_connection

ROOT = Path(__file__).resolve().parents[1]


def coverage(db=None):
    path = Path(db or ROOT / "data/crm.sqlite3").resolve()
    conn = private_connection(path, readonly=True)
    result = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "database": str(path),
        "sources": [],
        "totals": {},
    }
    for site, domain in [
        ("assistantbenchmark", "assistantbenchmark.com"),
        ("imessage_store", "www.imessage.store"),
    ]:
        fetches = conn.execute(
            "SELECT * FROM fetches WHERE url LIKE ?", ("https://" + domain + "/%",)
        ).fetchall()
        successful = [r for r in fetches if r["http_status"] and 200 <= r["http_status"] < 300]
        details = [r for r in successful if "/agents/" in r["url"] or "/agent/" in r["url"]]
        valid_archives, missing_or_bad = set(), []
        for row in details:
            archive = ROOT / (row["archived_path"] or "__missing__")
            try:
                content = gzip.decompress(archive.read_bytes())
                if hashlib.sha256(content).hexdigest() != row["content_sha256"]:
                    raise ValueError("hash mismatch")
                valid_archives.add(row["url"])
            except (OSError, ValueError, EOFError):
                missing_or_bad.append(row["url"])
        item = {
            "source_site": site,
            "successful_fetch_attempts": len(successful),
            "unique_detail_pages_downloaded": len({r["url"] for r in details}),
            "unique_detail_pages_with_valid_archive": len(valid_archives),
            "missing_or_bad_archives": missing_or_bad,
        }
        for key, query in {
            "canonical_source_listings": "SELECT count(*) FROM listings WHERE source_site=?",
            "staged_candidates": "SELECT count(*) FROM discovery_candidates WHERE source_site=?",
            "unreviewed_candidates": "SELECT count(*) FROM discovery_candidates WHERE source_site=? AND candidate_status IN ('new','needs_review')",
        }.items():
            item[key] = conn.execute(query, (site,)).fetchone()[0]
        result["sources"].append(item)
    for table in ["agents", "companies", "people", "contact_points", "sources", "outreach_events"]:
        result["totals"][table] = conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
    result["contact_breakdown"] = [
        dict(r)
        for r in conn.execute(
            "SELECT contact_type, verification_status, count(*) AS count FROM contact_points GROUP BY 1,2"
        )
    ]
    baseline = ROOT / "reports/source-totals.json"
    result["source_totals_observation"] = (
        json.loads(baseline.read_text()) if baseline.exists() else None
    )
    result["counting_notes"] = [
        "Staged candidates and canonical listings refer to the same pages; never add them.",
        "Downloaded means a successful recorded detail-page fetch, not a homepage, sitemap or source row.",
        "Source totals are observations, not deduplicated company or founder totals.",
        "Legacy manual fetches outside the audit log cannot be counted as fully audited downloads.",
    ]
    conn.close()
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path)
    args = p.parse_args()
    data = coverage()
    encoded = json.dumps(data, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    print(encoded)


if __name__ == "__main__":
    main()
