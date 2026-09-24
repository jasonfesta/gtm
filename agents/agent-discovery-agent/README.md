# Agent Discovery Agent

## Mission

Import agents found through external agent indexes/directories or Darwin's own index, qualify research agents, assistants, and personal agents, verify agent routes, and establish supported agent-to-human links.

## Required access

An exported index batch or authenticated Darwin index endpoint, Portkey, and local handoff storage. An Agent snapshot is optional; Ops Agents performs final CRM matching.

The configured source registry is `agents/config/agent-discovery-sources.json`. Inspect source access and current yield on each run; directory membership alone does not establish agent identity or a contact route.

Classification and summarization use the configured Portkey route.

## Classification

For each indexed record, use Portkey to decide whether it is an agent, infrastructure, or irrelevant. For agents, choose research agent, assistant, or personal agent and summarize Darwin fit, capabilities, published routes, and any clear owner/developer relationship. Unclear records go to `needs_review` while the rest of the run continues.

## Rules

- Agent and human owner remain separate records.
- Infrastructure providers are not target agents by default.
- Never establish an owner relationship from name similarity alone.
- A usable route needs an address, source, and verification time.
- Handle changes, inactive records, and tombstones without erasing history.
- Discovery writes no CRM or PostHog data directly.

## Manual run

1. Crawl the next candidate window from every configured directory, or load an exported index batch.
2. Keep projects pushed in the last 30 days whose latest commit identifies a developer active in the same 30-day window.
3. Rank the active projects by recency, repository popularity, and source position; classify up to 750 candidates to find 250 actual agents when available, stopping at 600 classified agents overall.
4. Match identities, classify each record, and collect useful facts.
5. Write one normalized CRM handoff using `agent_upsert`, `relationship_classify`, `contact_upsert`, and `agent_owner_link` operations.
6. Save the per-source positions and run summary.
7. Give Ops Agents the two run-ID-named files: `<run-id>.crm.json` and `<run-id>.posthog.json`. Keep their receipts distinct.

## Completion test

One manual run processes an index batch, creates one CRM record handoff and one PostHog metrics handoff, saves the source position when available, and creates no duplicate agents when replayed. No automation is included.
