"""Read the registered agent's status or dashboard without exposing its key."""

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "home"))
    args = parser.parse_args()
    credentials = json.loads((Path.home() / ".config/moltbook/credentials.json").read_text())
    endpoint = "agents/status" if args.action == "status" else "home"
    request = urllib.request.Request(
        "https://www.moltbook.com/api/v1/" + endpoint,
        headers={"Authorization": "Bearer " + credentials["api_key"]},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"Moltbook returned HTTP {exc.code}; no redirect followed.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
