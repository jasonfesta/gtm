# GTM agents

Each agent is manually invoked and owns one canonical runbook. CRM and PostHog
are services executed by Ops, not additional producer agents.

| Agent | Runbook |
| --- | --- |
| Human Discovery | [human-discovery-agent](human-discovery-agent/README.md) |
| Agent Discovery | [agent-discovery-agent](agent-discovery-agent/README.md) |
| Human Reply | [human-reply-agent](human-reply-agent/README.md) |
| Agent Reply | [agent-reply-agent](agent-reply-agent/README.md) |
| Human DM | [human-dm-agent](human-dm-agent/README.md) |
| Agent DM | [agent-dm-agent](agent-dm-agent/README.md) |
| Email | [email-agent](email-agent/README.md) |
| Ops | [ops-agents](ops-agents/README.md) |

Producer agents prepare separate CRM and body-free PostHog handoffs. Ops applies
and verifies each destination, preserving contact suppressions, exact identities
and uncertain outcomes. See the [CRM](ops-agents/crm.md) and
[PostHog](ops-agents/posthog.md) service contracts.

Starting or resuming Ops follows [its startup contract](ops-agents/startup.md).
Shared examples live in [config/](config) and [contracts/](contracts).
The [activation checklist](ACTIVATION_CHECKLIST.md) defines per-run readiness.
See [the system architecture](../docs/ARCHITECTURE.md) for the full ownership and data flow.
