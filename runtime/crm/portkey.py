"""Small verified Portkey lane for GTM watcher drafting. Never logs credentials."""

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = "https://api.portkey.ai/v1"
MODEL = "gpt-4o-mini"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CREDENTIALS = ROOT / ".local-credentials/portkey.json"


def _token(env=None, credentials=DEFAULT_CREDENTIALS):
    env = os.environ if env is None else env
    token = (env.get("PORTKEY_API_KEY") or "").strip()
    if token:
        return token
    path = Path(env.get("PORTKEY_CREDENTIALS_PATH") or credentials)
    if path.is_file():
        value = json.loads(path.read_text()).get("PORTKEY_API_KEY", "")
        if isinstance(value, str):
            return value.strip()
    return ""


def ready(*, env=None, credentials=DEFAULT_CREDENTIALS):
    return {
        "ready": bool(_token(env, credentials)),
        "gateway": GATEWAY,
        "model": MODEL,
        "key_present": bool(_token(env, credentials)),
    }


def complete(
    prompt,
    *,
    max_completion_tokens=100,
    env=None,
    credentials=DEFAULT_CREDENTIALS,
    opener=None,
):
    token = _token(env, credentials)
    if not token:
        raise ValueError("PORTKEY_API_KEY required")
    prompt = (prompt or "").strip()
    if not prompt:
        raise ValueError("prompt required")
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": max_completion_tokens,
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        GATEWAY + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "x-portkey-api-key": token,
            "User-Agent": "gtm-x-reply-watcher/1",
        },
        method="POST",
    )
    fetch = opener or urllib.request.urlopen
    try:
        with fetch(request, timeout=30) as response:
            raw = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        exc.read()
        raise RuntimeError(f"portkey HTTP {exc.code}") from None
    choices = raw.get("choices") or []
    message = (choices[0] or {}).get("message") if choices else {}
    text = (message or {}).get("content") or ""
    actual = (raw.get("model") or "").rsplit("/", 1)[-1]
    if actual != MODEL and not actual.startswith(MODEL + "-"):
        raise RuntimeError(
            f"portkey model mismatch: requested {MODEL}, received {actual or 'unknown'}"
        )
    if not text.strip():
        raise RuntimeError("portkey returned empty content")
    return {"text": text.strip(), "model": raw.get("model"), "usage": raw.get("usage") or {}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("ready")
    complete_parser = sub.add_parser("complete")
    complete_parser.add_argument("--prompt", required=True)
    complete_parser.add_argument("--max-completion-tokens", type=int, default=100)
    args = parser.parse_args()
    if args.command == "ready":
        print(json.dumps(ready(), sort_keys=True))
        return
    print(
        json.dumps(
            complete(args.prompt, max_completion_tokens=args.max_completion_tokens),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
