# Ops Agents

## Mission

Own all GTM operation-agent requests to CRM and PostHog. Other agents send this task records or prepared analytics events; Ops performs those calls and returns a separate receipt for each destination. Ops does not send outreach.

Start manually on demand. **Every normal start or resume automatically runs the complete [startup reconciliation](startup.md): inventory all local GTM data, reconcile and verify canonical CRM writes, then backfill and independently verify PostHog.** Do not wait for the user to run reconciliation or supply individual handoff paths. No timer, cron job, or recurring automation is active.

The startup workflow is executed by the Ops Codex task. The CLI commands below are single-operation tools used within that workflow; invoking one CLI command alone does not perform a checkout-wide reconciliation.

## What is built

- A manual `--once` CLI with three actions: apply one CRM handoff, export a bounded Human or Agent CRM snapshot, or submit a prepared PostHog handoff/batch. There is no background runner.
- CRM adapters for company, human, agent, classification, contact, event, tag, owner-link, and suppression changes, using the existing canonical CRM package and credential. Confirmed provider outcomes become CRM events; drafts and uncertain sends do not.
- Separate CRM and PostHog handoff formats and receipts. CRM receipts include the handoff ID, status, operation results, and content hash. PostHog moves accepted files to `submitted/` and leaves failed files in the inbox.
- PostHog capture to the existing Darwin project using its project token. The handoff carries only prepared events; Ops does not invent message outcomes or generate analytics from private message text.
- Focused replay, dry-run, snapshot, outcome, and failure tests.

## CRM

Use the existing CRM package and its configured credential. Another task sends this task the absolute path to a JSON handoff:

```json
{
  "handoff_id": "source-agent-run-id",
  "source_agent": "human discovery agent",
  "operations": [
    {"action": "human_upsert", "payload": {}}
  ]
}
```

Supported actions are `company_upsert`, `human_upsert`, `agent_upsert`, `relationship_classify`, `contact_upsert`, `event_record`, `tag_set`, `agent_owner_link`, and `suppression_set`. Apply them through the existing CRM code and return a receipt with the handoff ID, status, operation results, and hash. A multi-operation handoff runs in one database transaction. A failed operation rolls back the whole handoff; a commit error is uncertain and must be reconciled before replay. Existing stable IDs and CRM uniqueness rules make tested replays idempotent. A draft is not a send; a send is not a reply or Darwin query.

For a read request, return a bounded Human or Agent snapshot with identities, contact routes, and recent history. Snapshots include applicable person, company and contact suppressions. Each route carries `active_suppressions` and `suppressed`; records carry `suppression_context_complete` and `history_truncated`. A record-level suppression list can include route-specific entries: use their scope and channel rather than treating every entry as a global prohibition. History completeness and route evidence remain separate eligibility facts.

```sh
./.venv/bin/python -m gtm_agent_runtime.cli run agents/ops-agents/README.md \
  --once --crm-root runtime \
  --handoff /path/to/run-id.crm.json --output /path/to/run-id.crm.receipt.json

./.venv/bin/python -m gtm_agent_runtime.cli snapshot agents/ops-agents/README.md \
  --once --crm-root runtime \
  --type agent --limit 200 \
  --output /path/to/agent-snapshot.json
```

Use `--dry-run` with `run` to inspect a CRM handoff without writing.

## PostHog

Another task sends this task the absolute path to its separate `run-id.posthog.json` handoff. Copy that file into this checkout's `runtime/imports/posthog/` inbox; keep the CRM and PostHog files separate. Each PostHog file contains `schema_version`, `source_agent`, `run_id`, and nonempty `events`. Each event has `event`, stable `uuid`, `timestamp`, and `properties`. Reconcile new and historical handoffs automatically during startup using `startup.md` before submitting them.

Run one batch or one selected handoff, receive the PostHog response, and move submitted files into the inbox's `submitted/` folder. A failed submission leaves files in place. An empty inbox returns `empty`.

```sh
./.venv/bin/python -m gtm_agent_runtime.cli posthog agents/ops-agents/README.md \
  --once --inbox runtime/imports/posthog \
  --output /path/to/posthog-batch.receipt.json

./.venv/bin/python -m gtm_agent_runtime.cli posthog agents/ops-agents/README.md \
  --once --handoff runtime/imports/posthog/run-id.posthog.json \
  --output /path/to/run-id.posthog.receipt.json
```

Use `--dry-run` to count waiting files and events without submitting them. A live submit needs the existing `POSTHOG_PROJECT_ID` and `POSTHOG_PROJECT_TOKEN` in this task's environment; no new key or personal API key is required for capture. If they are unavailable, the command returns a failed receipt and leaves files in the inbox. HTTP acceptance proves submission; independently query the event UUID and run ID to prove ingestion. Dashboard configuration lives in `agents/config/`; change dashboards only when requested.

## Rules

- This task owns CRM and PostHog calls for the operation agents.
- It does not discover leads, draft messages, send outreach, or schedule another agent.
- Keep exact private message text in CRM private evidence; send only privacy-safe event properties to PostHog.
- Keep CRM and PostHog receipts distinct so a failure at one destination does not claim success at the other.

## Route evidence and suppression contract

An agent route may carry nullable `agent_operated`, `agent_operated_evidence_url`, and `agent_operated_verified_at`. An explicit boolean requires a sourced HTTPS URL and a past or present timezone-aware verification timestamp. Omitted facts remain unknown; neither an agent email address nor a provider name proves operation by an agent. Owner approval is independent and is never inferred from route availability.

The discovery aliases `agent_operated_source_url` and `verified_at` normalize to the canonical evidence fields. `discord` identifies an addressed public channel; `webmcp` identifies a browser tool page. A published route does not establish send permission. Controlled loopback routes do not establish production eligibility.

Use `suppression_set` with the exact canonical `contact_id`, a reason and `effective_at`. A human relationship route backed by a legacy contact maps to the legacy suppression table. Other routes use `relationship_contact_suppressions`; this preserves the existing legacy foreign key. An optional channel must match the route. Explicit `is_active: false` releases the same suppression scope. Unknown IDs, mismatched scopes and collisions are errors, and a failed handoff rolls back all its operations.

The additive schema upgrade is explicit: `python -m crm.route_evidence_migration --credential-file <private-admin-file> --output <new-private-receipt>`. The credential target must match the configured CRM. Existing SQLite fixtures can be upgraded with `--sqlite <existing-file> --output <new-private-receipt>`. Shared runtime operators do not install schema. Private-cache refresh applies the compatible local upgrade before copying shared facts.

## Verification and integration still required

- Apply actual reviewed producer handoffs and return a separate CRM readback receipt and independently queried PostHog receipt. HTTP acceptance alone is not queried analytics evidence.
- Retain controlled protocol conversations in distinct private evidence and receipts with `controlled_test: true` and `organic_outreach: false`. Fixture or rollback-only CRM proof is not production CRM completion; never normalize synthetic observations into organic outbound events.
- Integrate only reviewed, narrowly scoped commits. Preserve other tasks' dirty files and keep every runtime manually invoked.

## Completion

A normal task start completes only after the startup inventory is accounted for and separate CRM and PostHog verification receipts or explicit blockers are saved. A targeted CLI request still ends with its individual snapshot or receipt; `submitted` alone is not end-to-end verification. Follow [startup.md](startup.md) for restart, deduplication, and partial-failure handling.
