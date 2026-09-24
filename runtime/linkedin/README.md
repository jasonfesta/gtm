# Local LinkedIn operator

The shared LinkedIn adapter provides account-aware browser access, private SQLite history, duplicate controls and confirmation receipts. It supports public comments and responses to actual inbound DMs; it does not initiate cold DMs or expose Connect/Follow operations.

Current agent behavior comes from [Human Reply](../../agents/human-reply-agent/linkedin.md) and [Human Discovery](../../agents/human-discovery-agent/linkedin-sales-handoff.md). This folder documents implementation, not another agent or schedule. Verify the configured adapter identity independently from any visible browser session.

See [data model](DATA_MODEL.md), [adapter workflow](WORKFLOW.md) and [local setup](LOCAL_SETUP.md).

## What is stored

- LinkedIn accounts observed in local browser profiles
- people and every known LinkedIn profile alias
- account-relative connection and Message availability
- posts considered for a comment
- every draft revision and its supporting context
- outbound and inbound comments, DMs, and replies
- append-only message status changes
- prospectively recorded fresh-program suppressions, skipped targets, failures, and automation runs
- idempotency reservations used to prevent retry duplicates

Personalized records stay in `data/linkedin.sqlite3`. Raw captures, drafts, logs, and exports also stay local. They are ignored by the repository Git rules.

## Quick start

From this folder:

```bash
python3 -B store.py init
python3 -B store.py status
python3 -B store.py verify
python3 -B store.py export-history --account ACCOUNT_ID --person PERSON_ID
python3 -B import_legacy.py --config accounts/ACCOUNT.json
python3 -B linkedin_operator.py --config accounts/ACCOUNT.json prepare --source interactive
```

The default database is `data/linkedin.sqlite3`. Set `LINKEDIN_DB_PATH` to use a different local database.

`prepare` writes an ignored `drafts/RUN_ID/prepared.json`. A selected file has this shape and is passed to `apply`:

```json
{
  "run_id": "RUN_ID",
  "actions": [
    {"kind": "casual_comment", "post_id": "POST_ID", "text": "selected reply"},
    {"kind": "dm_reply", "inbound_message_id": "MESSAGE_ID", "text": "selected reply"}
  ]
}
```

The operator accepts only eligible IDs from that prepared run, lints every selected reply, reserves the action atomically, and records a send only after the exact text is visible under the expected account and target.

Apply [CRM contact rules](../Rules.md) to public replies and DMs: three shared unanswered touches, at least 72 hours apart; engagement stops the drip and allows relevant conversation responses. Cross-platform history integration remains incomplete.

## Operating rule

Every future run must follow this order:

1. Detect the logged-in LinkedIn member from a positive UI identity signal.
2. Resolve that member to one `accounts.account_id`; never assume a hardcoded owner.
3. Resolve the target to one canonical `people.person_id` through profile URL, member URN, or reviewed alias evidence.
4. Check prior messages, the configured repeat policy, and only fresh-program suppressions recorded after the reset. Never import legacy campaign blocklists or queues.
5. Reserve an idempotency key before opening a composer.
6. Draft and lint from the current post or inbound message.
7. Re-check identity and eligibility immediately before submission.
8. Record the platform result and preserve the exact message text.

Confirmed sends also create a privacy-safe local analytics outbox row. It contains aggregate channel fields and an opaque account key; message text and recipient identity remain only in the local ledger.

See [System guide](../../docs/ARCHITECTURE.md), [DATA_MODEL.md](DATA_MODEL.md), and [WORKFLOW.md](WORKFLOW.md).
