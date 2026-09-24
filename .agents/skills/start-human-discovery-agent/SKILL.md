---
name: start-human-discovery-agent
description: Start or resume the manual Human Discovery Agent to find and qualify people and prepare separate CRM and PostHog handoffs for Ops. Use when the user asks to start the human discovery agent; this does not send outreach.
---

# Start Human Discovery Agent

Read `agents/human-discovery-agent/README.md` completely and use the current task
for the requested bounded discovery pass. Its source selection, query rules and
provider limits are authoritative.

Use `runtime/crm/human_discovery.py` for the maintained collector. Select sources
and scope from the user's request and runbook; do not infer paid collection or
outreach authorization from starting the task. Preserve provider IDs, exact
source URLs, incomplete runs and holds. Do not relaunch an unfinished provider
run when it can be reconciled.

Prepare separate CRM and body-free PostHog handoffs for `ops agents`. Do not write
to either service directly. Report verified results, deduplicated people,
unresolved runs and remaining work. Do not start a recurring schedule.
