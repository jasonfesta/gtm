# Mandatory Ops startup reconciliation

Starting or resuming `ops agents` (also called the operations agent or ops agent)
authorizes and requires this workflow immediately. Do not wait for a separate
reconcile command, a supplied handoff path, or per-batch approval. This is work
inside the manually started task, not a scheduled job. An explicit read-only,
dry-run, or narrowly scoped user request takes precedence.

## 1. Inventory all local GTM evidence

Resolve the GTM checkout from this run file. Inventory the whole checkout,
including ignored runtime data: `runtime/logs/`, `runtime/imports/`, producer run/output
folders, `agents/`, `runtime/gtm_agent_runtime/providers/moltbook/`, and any retained private state from previous layouts. Include local
CRM stores, discovery/identity/contact records, classifications, routes,
suppressions, confirmed activity, provider receipts, daily ledgers, and both
CRM and PostHog handoffs. Do not restrict discovery to today's files, tracked
files, a filename suffix, or the PostHog inbox. Exclude dependencies, source
code, examples, fixtures, caches of already inventoried evidence, and secrets
from ingestible records; record each source's coverage explicitly.

Read previous reconciliation scopes, manifests, corrections, and destination
receipts before preparing writes. Preserve prior manifest exclusions and corrections; a broad startup request does not erase specific exclusions.
A prior submitted folder or local receipt alone is not remote verification.

Save this run's inventory and checkpoints under a new unique directory in
`runtime/logs/reconciliation/`. Track source path, content hash, producer, stable
record/provider identity, original timestamp, destination IDs, and separate
runtime/PostHog states. Snapshot files before processing; defer and report files
that change during the read. Never modify producer evidence. Re-scan before
completion for newly queued work; explicitly report anything still pending.

## 2. Reconcile and commit CRM first

Compare local facts with the canonical shared CRM through the configured CRM
MCP/API or canonical CRM adapter, including remote readback. A local SQLite
cache, dry run, or rolled-back transaction is not shared CRM completion.
Normalize valid local records into supported CRM operations even when a
producer did not prepare a handoff. Preserve provenance, original times,
private evidence, owner approval, and suppressions. Do not infer sends from
intent, drafts, queue membership, or unverified publication.

Deduplicate across files and historical backfills using canonical identities
and provider receipt IDs scoped to channel/account, not just handoff names.
Check existing CRM events and resolved conflict records before applying.
Validate handoffs; apply dependency-ordered, bounded transactions through the
existing CRM adapter. Independently read back affected records and events,
then persist the matching IDs and verification receipt. Append corrections;
never erase historical evidence or automatically merge conflicting identities.

On an uncertain commit, query remote state before any replay. Hold ambiguous,
invalid, or unsupported records with an exact reason and source reference;
continue unrelated valid records. Missing schema or credentials is a reported
blocker, not permission to switch to a local database and claim success.

## 3. Update PostHog after CRM verification

Read the PostHog skill before using PostHog tools. Use Darwin project `121185`
and the established event/property definitions. Match prepared events against
the CRM-verified inventory. If analytics handoffs are absent, prepare backfill
events only from independently verified facts with an established event mapping;
hold unmapped facts instead of inventing event semantics. CRM-only records do
not automatically become analytics events.

Compare against previous manifests and queried PostHog events by stable UUID
and channel/account/provider receipt identity. Reuse an existing event's UUID
across historical and newly named handoffs; never mint a second UUID for the
same action. Preserve original occurrence time and actual producer/campaign
attribution. Respect corrected/invalid receipts, campaign exclusions, and
controlled-test labels. Keep message bodies and recipient contact details out
of PostHog.

Submit only missing, eligible events whose CRM dependencies have verified
readback. Use a separate prepared batch and receipt; do not blindly flush old
inbox files. A CRM failure holds its dependent analytics, while unrelated
verified records may proceed. Query PostHog after capture and compare UUIDs,
counts, timestamps, runtime/provider IDs, and attribution against the manifest.
Capture acceptance is `submitted`, not `verified`. If ingestion is delayed or
readback is unavailable, retain `verification_pending`; query before retrying
capture on the next start. Do not recreate dashboard definitions unless asked.

## 4. Completion and restart

Persist checkpoints after each destination outcome. On restart, inspect remote
state for pending or uncertain work before replay. Avoid concurrent writes for
the same inventory; if another Ops reconciliation is active, coordinate with
that task and report the overlap rather than submitting overlapping batches.

Finish with counts of discovered, already reconciled, newly CRM-verified,
newly PostHog-verified, excluded, held, failed, and verification-pending records,
with separate destination totals and links to receipts. All local sources must
be accounted for, including unsupported formats and older history. Report
partial coverage honestly; an empty inbox does not establish reconciliation.
Do not stop after writing a plan, preparing files, or reporting HTTP acceptance.
A clean second run should verify existing records and make zero duplicate writes.
No outreach, recurring automation, or automatic activation of producer tasks is
part of this workflow.
