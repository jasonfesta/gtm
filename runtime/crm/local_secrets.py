"""Load private tokens from env or .local-credentials. Never log values."""

from __future__ import annotations

import json
import os
from pathlib import Path

from crm.database import ROOT

CHECKOUT = ROOT.parent
CREDENTIALS = CHECKOUT / ".local-credentials"


def _read_json(path):
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _first(*values):
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return ""


def apify_token(*, env=None, credentials=CREDENTIALS):
    env = os.environ if env is None else env
    file = _read_json(Path(credentials) / "apify.json")
    return _first(
        env.get("APIFY_API_TOKEN"),
        env.get("APIFY_API_KEY"),
        file.get("APIFY_API_TOKEN"),
        file.get("token"),
        file.get("api_token"),
    )


def apollo_token(*, env=None, credentials=CREDENTIALS):
    env = os.environ if env is None else env
    file = _read_json(Path(credentials) / "apollo.json")
    return _first(
        env.get("APOLLO_API_KEY"),
        env.get("APOLLO_API_TOKEN"),
        file.get("APOLLO_API_KEY"),
        file.get("token"),
        file.get("api_key"),
    )


def smartlead_token(*, env=None, credentials=CREDENTIALS):
    env = os.environ if env is None else env
    file = _read_json(Path(credentials) / "smartlead.json")
    shared_key = ""
    shared_path = env.get("SMARTLEAD_SHARED_ENV_FILE")
    if shared_path:
        for line in Path(shared_path).read_text(encoding="utf-8").splitlines():
            if line.startswith("SMARTLEAD_API_KEY="):
                shared_key = line.partition("=")[2].strip().strip("\"'")
                break
    return _first(
        env.get("SMARTLEAD_API_KEY"),
        file.get("SMARTLEAD_API_KEY"),
        file.get("api_key"),
        file.get("token"),
        shared_key,
    )


def github_token(*, env=None, credentials=CREDENTIALS):
    env = os.environ if env is None else env
    file = _read_json(Path(credentials) / "github.json")
    return _first(env.get("GITHUB_TOKEN"), file.get("GITHUB_TOKEN"), file.get("token"))


def slack_webhook(*, env=None, credentials=CREDENTIALS):
    env = os.environ if env is None else env
    credentials = CREDENTIALS if credentials is None else credentials
    file = _read_json(Path(credentials) / "slack.json")
    return _first(
        env.get("SLACK_REPORT_WEBHOOK"),
        env.get("SLACK_WEBHOOK_URL"),
        file.get("SLACK_REPORT_WEBHOOK"),
        file.get("webhook"),
        file.get("url"),
    )


def persist_apify_token(token, *, credentials=CREDENTIALS):
    token = (token or "").strip()
    if not token:
        raise ValueError("APIFY_API_TOKEN required")
    folder = Path(credentials)
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / "apify.json"
    path.write_text(json.dumps({"APIFY_API_TOKEN": token}) + "\n")
    path.chmod(0o600)
    folder.chmod(0o700)
    return path


def portkey_token(*, env=None, credentials=CREDENTIALS, home=None):
    env = os.environ if env is None else env
    file = _read_json(Path(credentials) / "portkey.json")
    home_path = Path(home or Path.home()) / ".cursor" / "portkey_credentials.json"
    home_file = _read_json(home_path)
    return _first(
        env.get("PORTKEY_API_KEY"),
        env.get("PORTKEY_API_TOKEN"),
        file.get("PORTKEY_API_KEY"),
        file.get("api_key"),
        file.get("token"),
        home_file.get("api_key"),
        home_file.get("PORTKEY_API_KEY"),
    )


def persist_portkey_token(token, *, credentials=CREDENTIALS):
    token = (token or "").strip()
    if not token:
        raise ValueError("PORTKEY_API_KEY required")
    folder = Path(credentials)
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / "portkey.json"
    data = _read_json(path)
    data["PORTKEY_API_KEY"] = token
    path.write_text(json.dumps(data) + "\n")
    path.chmod(0o600)
    folder.chmod(0o700)
    return path


def portkey_model(*, env=None, credentials=CREDENTIALS, home=None):
    env = os.environ if env is None else env
    file = _read_json(Path(credentials) / "portkey.json")
    home_path = Path(home or Path.home()) / ".cursor" / "portkey_credentials.json"
    home_file = _read_json(home_path)
    return (
        _first(
            env.get("PORTKEY_MODEL"),
            file.get("default_model"),
            file.get("model"),
            home_file.get("default_model"),
            home_file.get("model"),
        )
        or "gpt-4o-mini"
    )


def persist_portkey_model(model, *, credentials=CREDENTIALS, home=None):
    model = (model or "").strip()
    if not model:
        raise ValueError("PORTKEY_MODEL required")
    folder = Path(credentials)
    folder.mkdir(mode=0o700, exist_ok=True)
    path = folder / "portkey.json"
    data = _read_json(path)
    data["default_model"] = model
    path.write_text(json.dumps(data) + "\n")
    path.chmod(0o600)
    folder.chmod(0o700)
    home_path = Path(home or Path.home()) / ".cursor" / "portkey_credentials.json"
    if home_path.is_file():
        home_data = _read_json(home_path)
        home_data["default_model"] = model
        home_path.write_text(json.dumps(home_data) + "\n")
        home_path.chmod(0o600)
    return path
