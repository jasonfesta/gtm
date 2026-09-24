# PostHog submission in Ops Agents

The separate PostHog Agent task has been combined with [Ops Agents](README.md). Ops Agents receives prepared analytics handoffs, submits them to PostHog, and returns a PostHog receipt. At startup it also prepares missing analytics handoffs from independently verified CRM facts using established event mappings, as specified in [startup.md](startup.md). Unverified CRM requests are not analytics evidence. Other operation agents keep CRM handoffs and PostHog event handoffs separate.

Use the existing version 1 example at [config/posthog-handoff.example.json](../config/posthog-handoff.example.json). The handoff contains `source_agent`, `run_id`, and an `events` array. Each event has an event name, stable UUID, timestamp, and privacy-safe properties. The sender supplies confirmed outcomes; Ops Agents does not invent provider message IDs, replies, clicks, bookings, or conversions.

Run a prepared handoff manually with the command in [ops-agents.md](README.md#posthog). Each invocation submits its selected batch and exits. The intended future cadence is every 15 minutes, but no timer, cron job, or recurring automation is active.

The GT dashboard (`2118597`) now represents all seven producer agents, excluding Ops. Activity and discovery tiles query captured events by producer and respect dashboard dates. Seven metric cards, two charts, a data-gap card, and two supporting tables use live events. CRM verification stays in dated receipts and uses CRM MCP/API, independent of capture. Logging and dashboard verification use PostHog MCP; a browser URL is not a receipt. Maintain definitions in `agents/config/gtm-dashboard-2118597.json`.
