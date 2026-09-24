# Shared CRM operating guide

The configured shared PostgreSQL database is authoritative; the deployed namespace is `crm_gtm`. Private credentials and the expected destination are configured per operator. Verify the destination before any operation.

## Connect an operator

Follow the [setup index](SETUP.md) from the lowercase gtm checkout. Use Python 3.12+ and install the documented dependencies. Obtain an individual CRM-scoped role through the database owner; do not copy Jason's credential, administrator URL or browser sessions. Runtime roles have namespace-limited read/insert/update rights, no delete or schema administration. Multi-operator outreach reservations are still pending; do not start concurrent dispatch.

Copy [database.example.json](../runtime/config/database.example.json) to private runtime/accounts/database.json and fill the verified host, database and individual role. Store the matching direct PostgreSQL URL in runtime/accounts/crm-runtime.url with mode 0600; never put it in chat, documentation or a command argument. The URL must include required TLS settings. Keep the config private too. Existing files must be preserved and reconciled, never overwritten during setup.

For a fresh operator without an existing private cache, run from `runtime/` using the root interpreter:

```sh
../.venv/bin/python -B -m crm.database --init-private-cache
../.venv/bin/python -B -m crm.database
```

Cache creation refuses overwrite. A fresh cache contains shared facts only: another operator's private reviews, drafts and evidence are unavailable unless explicitly transferred with provenance. Reconcile the agreed private data and channel ledgers before marking that operator ready. Run make check from the root; its integrity/coverage phase now reads the configured shared database.

## Write and hydrate

Maintained CRM commands and hydrate importers use crm.database.connect. Use the root .venv interpreter, including when working inside `runtime/`. The maintained importers target the configured shared backend, retain stable IDs and completed-run receipts, and keep private values in local journals. Historical run scripts under evidence are immutable receipts, not current import entry points.

Canonical local SQLite is a preserved pre-cutover snapshot with write-blocking triggers. A configured shared connection failure raises an error; it never falls back to writable SQLite. Explicit synthetic fixture and backup paths remain local. Do not remove guards or delete database.json to work around an outage. Resolve connectivity/identity and reconcile uncertain transactions first.

Transactions use PostgreSQL serializable isolation. Conflicting updates abort rather than silently lose work. Immutable review/tag history and commit receipts cannot be changed through normal operations. This is database concurrency protection; the cross-channel contact reservation and queue remain unfinished.

## Private data and recovery

Private draft/preparation tables, raw captures, notes and detailed JSON remain local. The shared schema stores sanitized facts and minimal routing decisions; it does not upload the SQLite file. Copy and email preparation read a refreshed private cache containing shared facts plus available local evidence. Its shared tables cannot be written through the private connection.

Before a shared commit, a private journal is persisted. A successful remote commit has an immutable crm_commits receipt. A timeout after submission is an uncertain outcome: reconcile that ID before retrying. Do not invent a new import ID or repeat an unresolved operation. Preserve journals and private cache together.

Create a new sanitized shared snapshot from `runtime/`:

```sh
../.venv/bin/python -B -m crm.database --backup exports/shared-crm-NEW-CHECKPOINT.sqlite3
```

Back up private-crm-cache.sqlite3, activity.sqlite3 and the LinkedIn ledger separately with SQLite's backup API. Check full integrity and foreign keys, record hashes, and test restoration to new disposable local files. A sanitized snapshot cannot restore omitted private evidence. Rebuild a remote namespace only through a separately reviewed restoration procedure; never run migration over the existing namespace. Preserve the frozen pre-migration backup and all historical backups.

The CRM integrity command validates relational constraints and application invariants; it is not a physical PostgreSQL checksum test. Native provider disaster recovery and a full remote restore rehearsal are not established by the local snapshot test. See [CRM architecture](CRM.md) for storage and recovery boundaries.
