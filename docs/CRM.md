# CRM ownership, storage and contracts

The CRM is shared infrastructure used by Ops. It is not a discovery or messaging agent. Producer agents pass records to Ops and request bounded snapshots; Ops owns canonical writes and their readback. CRM records and PostHog analytics have separate handoffs and separate receipts.

## Data model

| Layer | Purpose |
| --- | --- |
| People, agents and companies | Stable identities; an agent and its human owner remain separate records |
| Relationship profiles and classifications | Human/agent audience classification and evidenced owner relationships |
| Contact routes | Channel, address, source and verification time; route availability does not establish send permission |
| Sources, listings and discovery candidates | Provenance and staged observations before canonical qualification |
| Evidence and conflicts | Supporting observations and unresolved identity/fact conflicts |
| Outreach and engagement events | Append-only outcomes with provider identity, occurrence time and observation time |
| Suppressions | Person, company or exact contact restrictions, preserving scope and release history |
| Private preparation state | Drafts, copy revisions, reviews, preparations and local evidence |

Human audiences include personal-agent owners, assistant developers and early adopters. Agent audiences include personal, assistant and research agents. Never infer owner identity from name similarity. Agent-operated route evidence and owner approval are independent facts.

SQL lives in `runtime/sql/`: `schema.sql` defines base identities and source evidence; `relationships_schema.sql` adds relationship profiles and contacts; `tags_schema.sql` defines classifications/tags; `route_review_schema.sql`, `copy_schema.sql` and `email_schema.sql` support route review and private preparation workflows. Migration commands are explicit; runtime operators do not install shared schemas automatically.

## Shared database and private cache

Configured PostgreSQL is authoritative. `runtime/accounts/database.json` selects the backend, schema, credential file and expected destination. Credentials remain private. The existing `runtime/data/crm.sqlite3` path is the compatibility entry point for the database adapter and local private cache; its presence does not mean canonical writes bypass PostgreSQL. The adapter fails on shared-database errors rather than silently switching to a disconnected database.

`runtime/crm/database.py` separates shared facts from private content. Drafts, templates, copy revisions and email review/preparation tables stay local. Fields such as notes, excerpts, raw records, checkpoint details, decision/manifest bodies and reversible snapshots are kept in private storage. Shared read results can receive the local overlay. Preserve that cache and its journals during migration; never replace existing contact history with an empty database.

Shared transactions record commit receipts in `crm_gtm.crm_commits`; local journals support recovery. If commit success is uncertain, reconcile the transaction identity before replay. See [shared CRM setup](SHARED_CRM.md) and [maintenance](MAINTENANCE.md).

## Ops handoff API

The CRM JSON envelope contains `handoff_id`, `source_agent` and `operations`. Supported actions are:

`company_upsert`, `human_upsert`, `agent_upsert`, `relationship_classify`, `contact_upsert`, `event_record`, `tag_set`, `agent_owner_link`, `suppression_set`.

A multi-operation handoff is one transaction. An operation failure rolls back the batch. A receipt records the handoff ID, content hash, status and operation results. Replays use stable IDs and database uniqueness; changing a payload is not permission to duplicate its original provider outcome.

Snapshots include applicable suppressions and recent history. Each contact carries `active_suppressions` and `suppressed`; records expose `suppression_context_complete` and `history_truncated`. Route-specific restrictions must be applied to that route, while person/company restrictions may apply more broadly. Missing history is not evidence of eligibility.

A usable contact route needs an address, source and verification time. Nullable `agent_operated` is supported only with sourced, timestamped evidence when explicitly set. Unknown stays unknown. `suppression_set` must target the exact canonical contact and matching channel; releasing a restriction uses the same scope with `is_active: false`.

## Example operations

From the repository root, using an already provisioned private runtime:

```sh
.venv/bin/python -m gtm_agent_runtime.cli run agents/ops-agents/README.md \
  --once --crm-root runtime --handoff /private/path/run.crm.json \
  --output /private/path/run.crm.receipt.json

.venv/bin/python -m gtm_agent_runtime.cli snapshot agents/ops-agents/README.md \
  --once --crm-root runtime --type agent --limit 200 \
  --output /private/path/agent-snapshot.json
```

`run --dry-run` validates without applying writes. PostHog uses a separate `posthog` command and file. Its `submitted` status means endpoint acceptance, not independently queried ingestion. See [Ops](../agents/ops-agents/README.md), [CRM contract](../agents/ops-agents/crm.md) and [analytics contract](../agents/ops-agents/posthog.md).

## Code responsibilities

`runtime/crm/` owns database adapters, schemas/migrations, relationship rules, normalization, private evidence and channel preparation. `runtime/gtm_agent_runtime/crm.py` adapts producer handoffs and exports snapshots. `runtime/gtm_agent_runtime/posthog.py` processes analytics handoffs. Channel-specific modules own provider behavior; they do not redefine canonical CRM identity rules.

Run `make source-check` for source validation and isolated tests. PostgreSQL integration tests need a disposable loopback `CRM_TEST_POSTGRES_URL`. `make integrity` inspects a configured local CRM and is separate from the test suite. Source checks never prove live provider readiness or a production CRM outcome.
