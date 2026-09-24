# LinkedIn data model

The schema lives in [sql/schema.sql](sql/schema.sql). SQLite is authoritative; Markdown reports are derived views for review.

| Data | Purpose | Key deduplication rule |
|---|---|---|
| `accounts` | One LinkedIn identity observed in one or more local browser profiles | member URN first, reviewed public identifier second |
| `browser_profiles` | Maps a local browser profile key to an account without storing cookies or passwords | one active account per profile observation |
| `people` | Canonical human identity, optionally linked to the main CRM | one reviewed person record |
| `profiles` | LinkedIn URLs, slugs, and member URNs belonging to a person | normalized URL/member URN unique |
| `account_profile_state` | Connection degree, Message availability, and observation time relative to an account | one account/profile pair |
| `posts` | Posts scanned, selected, skipped, or commented on | platform post ID or canonical URL unique |
| `draft_revisions` | Immutable A/B or edited comment/DM drafts | draft ID plus revision |
| `conversations` | A comment thread or DM thread between an account and person | platform thread ID when available |
| `messages` | Exact inbound/outbound comments, DMs, and replies | external message ID or idempotency key |
| `message_status_events` | Delivery, failure, deletion, or reconciliation changes | append-only event history |
| `confirmed_send_receipts` | Evidence that exact text appeared under the expected account and target | one receipt per action and platform reference |
| `analytics_outbox` | Retryable aggregate event derived only from a confirmed receipt | one stable event per receipt; no text or recipient identity |
| `action_intents` | Reservation made before any external action | globally unique idempotency key |
| `action_events` | Planned, attempted, sent, failed, skipped, or cancelled transitions | append-only action history |
| `suppressions` | Prospectively recorded person/profile/account do-not-contact rules for the fresh program | an active current-program suppression always wins |
| `runs` and `run_items` | Interactive or scheduled batch provenance | one durable record per invocation and target |
| `import_sources` | Hashes and scope for legacy logs or owner-supplied exports | the same source and scope import once |
| `history_coverage` | Whether comments or DMs are unknown, partial, or complete for an account/person | incomplete private history blocks automatic cold DMs |

## Identity hierarchy

A display name is never a deduplication key. The preferred order is:

1. LinkedIn member URN or another stable platform member ID.
2. Exact normalized `/in/{slug}` profile URL.
3. An existing profile alias already reviewed onto a canonical person.
4. A candidate identity held for review.

Connection degree and Message availability belong to `account_profile_state` because they can differ for each logged-in account.

## Message history

`messages` is append-only. It stores the exact text, direction, surface, time, target post or thread, and available platform reference. If a message later fails, is deleted, or is reconciled from the platform, a new `message_status_events` row records that fact. Old text is not overwritten.

Private incoming DMs remain only in the ignored local database and optional ignored raw evidence folder. Reusable Markdown contains templates and rules, never copied private conversations.

A confirmed live send writes its outbound `messages` row, `sent` status, confirmation receipt, and analytics outbox event in one database transaction. A failed or uncertain browser result writes only an action event. Legacy `reported_sent` records never produce analytics events.

## Duplicate prevention

The future sender creates an `action_intents` row before touching the platform. It builds this deterministic source string and stores its SHA-256 hash as the idempotency key:

```text
{account_id}|linkedin|{action_type}|{target_key}|{policy_scope}
```

Examples of `target_key` are a post ID for a public comment, a person ID plus campaign for a lead DM, or an inbound message ID for a thread reply. The unique key prevents a crashed or repeated run from reserving the same action again.

Eligibility also checks message history:

- exact post: never comment twice from the same account
- casual comment: apply the shared three-touch drip and 72-hour spacing in [contact rules](../Rules.md); engagement stops cold follow-ups
- lead or hiring comment: shares the same three-touch person budget with casual replies and DMs
- campaign DM: once per person and campaign unless policy explicitly allows another touch
- cold DM: requires reviewed complete private-history coverage; partial or unknown history stays blocked
- reply to an inbound message: one outbound reply per inbound message unless the conversation policy allows more
- suppression: blocks matching actions only when it was recorded prospectively for the fresh program; legacy campaign lists are not imported

The browser adapter must repeat the check immediately before submission because external state can change after a scan.

## Data retained for each run

Each run stores the account, browser profile key, source, requested count, mode, policy version, start/end time, result counts, and errors. Each run item stores the person/profile/post considered, eligibility decision, skip reason, draft, reservation, and final action reference.
