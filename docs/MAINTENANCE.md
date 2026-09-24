# Maintenance, lint and private transfer

## Runtime and setup

The shared CRM uses Python 3.12+, psycopg and SQLGlot from root pyproject.toml; the private LinkedIn ledger uses SQLite. Workspace-wide lint uses **Python 3.12+**, Ruff, PyYAML and Node.js for JavaScript syntax and WebMCP contract tests.

On a receiving machine, create a local developer environment from the workspace root:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
make source-check
```

The default Makefile uses the root .venv when available, otherwise python3. Install Node.js if the JavaScript syntax checker reports it missing. Do not copy a virtual environment between machines. For older command examples using python3, activate the root environment first with source .venv/bin/activate from the root, or explicitly use its interpreter. System Python may lack the shared CRM dependencies.

## Repeatable checks

`make check` runs Python correctness/import lint and formatting, maintained Markdown links/fences, JSON/YAML/TOML parsing, Python syntax, JavaScript and shell syntax, CRM, LinkedIn, shared runtime, root Python and Node contract tests, database integrity and coverage. SQL files are inventoried; SQLite tests and the separate PostgreSQL migration check validate their respective schemas. The integrity and coverage steps read the configured shared PostgreSQL CRM. Synthetic tests and lint are local; none launch workers or contact outbound providers.

Ruff configuration is explicit in the root, so results do not depend on a user's global settings. Import-order exceptions are limited to entry points/tests that must establish paths or safety settings before imports. Templates are parsed with placeholder substitution; runtime configuration is parsed without substitution.

Standalone source checks cover maintained code, configs and Markdown; It excludes dependencies, separate worktrees, private account/recipient data, raw evidence, generated outputs and immutable archives. Those are retained data, not source files to reformat. The machine-readable lint report lists the exclusions and file counts. Actual source permissions, factual claims and live account connectivity are not established by lint.

To check formatting and source quality after editing:

```sh
make lint
```

That is check-only. Use Ruff's `format` on the specific edited files when needed, review the diff, and rerun `make source-check`. Run `make check` only when configured database integrity checks are also intended. Do not run bulk unsafe fixes or provider scripts as a shortcut.

## Consistent backup

For current canonical facts first create a sanitized shared snapshot following [shared CRM operations](SHARED_CRM.md). The example below backs up the historical local snapshot only. Separately back up data/private-crm-cache.sqlite3 to preserve private copy and evidence fields. Never restore the old canonical SQLite file as an independent writable CRM after cutover.

Use SQLite's backup API rather than copying a live database/WAL pair. From the workspace root, choose a new private destination and refuse overwrite:

```python
import sqlite3
from pathlib import Path

source = Path("runtime/data/crm.sqlite3")
target = Path("runtime/exports/recipient-handoff.sqlite3")
if not source.is_file() or target.exists():
    raise RuntimeError("existing source and new destination required")
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
    assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
```

Use the same procedure for the LinkedIn database. When present, also back up runtime/data/activity.sqlite3: its opaque identity mapping and pending/accepted/confirmed analytics state must survive restoration together. Losing that mapping can break attribution and replay reconciliation. Preserve private run receipts, draft references and evidence paths when transferring the corresponding data. Do not initialize an empty database and assume its history matches the existing one.

## Transfer and ownership

Jason will operate this local workspace first. A separate local environment for Sanjit is planned once this version works; cloning is deferred and the private transfer channel is undecided. This source-maintenance procedure does not itself publish or send anything. Select a private transport and define writer/contact ownership before moving personalized data. Code/docs and credential-free examples can be reviewed separately from private database/evidence backups.

Exclude `.env`, tokens, browser profiles/cookies, virtual environments, `.git` internals and unrelated worktrees from any ordinary source handoff. Do not zip the whole tree blindly.

On receipt: install dependencies, restore the agreed consistent database, fix local paths, run checks, reconnect each intended service account and reconcile existing Gmail draft/LinkedIn history references. Retire the previous writer before enabling anything later. A portable example is not a credential or account binding.

## Current evidence and recovery

Execution logs and recovery backups remain private under `runtime/logs/` and `runtime/exports/`. Current status/rules live in the working guides; historical source evidence and immutable review events remain data, not operating instructions.

## GTM disposable PostgreSQL tests

The CRM suite uses `CRM_TEST_POSTGRES_URL` for explicitly provisioned disposable
loopback PostgreSQL. Run the CRM suite with that environment variable only against
the disposable instance; it creates and removes test schemas. Without it, those
tests skip. The independent control-plane `TEST_DATABASE_URL` is a different
configuration and does not enable these tests. Never use a production URL.
