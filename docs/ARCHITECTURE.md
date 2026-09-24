# How GTM works

GTM has eight manually invoked agent roles. Human Reply has four independent platform variants. Agent runbooks describe operating behavior; Python modules provide bounded preparation, collection, persistence and receipt tools. Execution stays in the operator workspace.

## Ownership

| Agent | Input and work | Result | Implementation |
| --- | --- | --- | --- |
| Human Discovery | Source-backed people and relevant conversations; Apify, Sales Navigator and explicit Apollo lookup | Qualified people, source permalinks, CRM and aggregate analytics handoffs | `runtime/crm/human_discovery.py`, `human_discovery_search.py` |
| Agent Discovery | Directory/index candidates, source registry and Portkey classification | Agent identity, capability, verified routes and separately evidenced owner links | `runtime/gtm_agent_runtime/agent_discovery.py`, `directory_crawler.py` |
| Human Reply | Relevant public conversation on one selected platform | Contextual reply, exact publication readback, deduplication and separate handoffs | `runtime/crm/human_reply_agent.py`, `human_reply_capture.py`, `human_reply_planning.py` |
| Agent Reply | Agent conversations on GitHub, Moltbook or Discord | Prepared or verified reply with provider receipt | `runtime/gtm_agent_runtime/agent_reply*.py`, `providers/moltbook/` |
| Human DM | Signed-in human inbox and eligible recipients | Bounded direct conversation with durable cursor and outcome receipt | `runtime/crm/human_dm_agent.py`, `human_dm_inbox.py`, `human_inbox.py` |
| Agent DM | Verified agent-operated contact, policy and suppression snapshot | AgentMail exchange or separately operated A2A conversation; controlled WebMCP tools have their own contract | `runtime/crm/agent_dm*.py`, `agents/agent-dm-agent/protocol-test/` |
| Email | Verified CRM-backed recipient and final supplied copy | Smartlead enrollment and reconciled actual outcomes; remains parked until invoked | `runtime/crm/email_agent.py` |
| Ops | Producer handoffs and bounded read requests | Canonical CRM changes, snapshots and separately verified PostHog receipts | `runtime/gtm_agent_runtime/crm.py`, `posthog.py`, `cli.py` |

Open [the agent index](../agents/README.md) for each role's complete operating contract. The shared CLI serves Agent Reply and Ops; it is not a universal runner for every agent.

## The four Human Reply agents

Each platform runs in its own task, dedicated browser tab and run-scoped account lease. A task selects one platform at startup. Browser identity, source context and duplicate history must be verified for that platform before posting.

| Variant | Discovery and selection | Publication evidence |
| --- | --- | --- |
| X | Capture up to 100 items from loaded X Pro columns; select up to 25 worthwhile contextual replies | Exact parent, signed-in account, reply body and published permalink |
| Reddit | Search 10 keywords through Apify; select suitable native threads, respect subreddit rules and locked threads | Native thread/comment URL, correct parent, author and exact body |
| Hacker News | Search 10 keywords through Apify; read item and comment context | Numeric parent/item identity and the new comment ID, permalink and body |
| LinkedIn | Search 10 keywords through Apify; Sales Navigator can supply observed public post permalinks | Exact activity/parent, signed-in identity and comment readback; a profile alone is not replyable |

Non-X runs aim to find 25 suitable posts and pace selected replies across about an hour. These are upper bounds or planning targets, never minimum posting quotas. If relevance, access or verification fails, report the shortfall. See [startup](../agents/human-reply-agent/startup.md), [rules](../agents/human-reply-agent/rules.md), and the platform contracts linked from [Human Reply](../agents/human-reply-agent/README.md).

Daily receipts live privately at `runtime/logs/human-reply-agent/daily-sends/<platform>/YYYY-MM-DD.md`. Preserve uncertain attempts across restart. LinkedIn's `published_readback_confirmed_permalink_missing` state excludes the author from another attempt while withholding confirmed analytics until verification is complete. Never retry an uncertain send just because a permalink is missing.

## End-to-end flow

1. A producer reads a source conversation or a bounded CRM snapshot and checks identity, route evidence, history and applicable suppressions.
2. It collects or prepares within its runbook. Discovery produces no outreach. Sending roles use their specified provider or browser workflow.
3. It durably records the attempt and verifies the actual provider outcome. A prepared draft, accepted request or enrollment is not a confirmed sent message.
4. It writes two independent files: `<run-id>.crm.json` and `<run-id>.posthog.json`. Private bodies remain in private evidence; analytics contain identifiers and safe aggregate properties.
5. Ops applies the CRM handoff atomically, reads back the canonical result, submits the separate analytics handoff and independently queries its event identity. Each destination returns its own receipt.

Failures remain explicit. One destination succeeding does not imply the other succeeded. Stable entity IDs, provider IDs and event UUIDs support replay. An uncertain database commit or provider send must be reconciled before retrying.

## How the workspace is organized

- `agents/`: current runbooks, platform rules, fixtures and handoff configuration.
- `runtime/`: shared CRM, agent helpers, provider adapters, schemas and policy assets.
- `docs/`: current architecture, CRM, setup guidance.
- `.agents/skills/`: startup instructions for Human Discovery, Human Reply and Human DM.

Credentials, databases, receipts, drafts and run reports are ignored local state. Retired plans and reports are retained by Git history rather than copied into the current tree. See [CRM](CRM.md) and [setup](SETUP.md).
