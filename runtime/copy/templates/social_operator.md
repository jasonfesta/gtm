# Shared social preparation contract

Current public reply behavior belongs to [Human Reply](../../../agents/human-reply-agent/README.md), and private conversation handling belongs to [Human DM](../../../agents/human-dm-agent/README.md). Use the selected runbook's scope, source method and browser lease.

Verify the publishing account, exact parent conversation and recipient identity. Read [voice](../copy.md), [copy workflow](../WORKFLOW.md) and [contact rules](../../Rules.md). Relevant prior sends, suppressions and uncertain attempts remain factual history across runs.

Use source-specific context and preserve exact URLs, handles and provider IDs. Check history and current route availability before preparing or submitting. Record the attempt durably, then read back the exact body, sender and new message identity. Unknown outcomes remain held for reconciliation, never blindly retried.

The [LinkedIn implementation](../../linkedin/README.md) maintains its existing private ledger; do not create a second history store. Private state and provider evidence remain local. Produce separate CRM and body-free PostHog handoffs for Ops. No schedule or background operation is defined here.
