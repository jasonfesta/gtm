# Reusable CRM source intake

Use this prompt for a new directory, website, pasted list, attachment or dataset. Follow the [runbook](CRM_RESEARCH.md), [routing rules](OUTREACH_ROUTES.md) and [System guide](CRM.md).

## Copy/paste prompt

Review the supplied source for relevant developers, founders, product leaders and team members working on agents/products. Personal coding is not required. Use the configured shared CRM and preserve its records.

Inputs: source/reference, audience or product context, proposed bounded cohort, allowed research methods and an explicit budget if paid processing is requested. If scale is unknown, inspect a bounded sample and propose the next cohort before full expansion. Do not reuse a previous source's paid approval.

1. Read the workspace instructions and inspect source format, access policy and current database. Do not reinitialize an existing CRM or rerun a fixed-cohort import script against a new list.
2. Create a private unique run manifest with source/reference, timestamp/hash, scope, denominator, methods, processing boundary and budget. Preserve original rows and exact values separately from canonical entities.
3. Resolve people and actual product roles using primary professional evidence. Keep aliases, past roles, external/client work and conflicting identities explicit.
4. Review email, LinkedIn and X independently. Save exact public links/addresses and provenance. Candidate/provider-only contacts remain candidates. Report missing or blocked channels without inventing replacements.
5. Check professional relationships through existing Apollo/Gmail records, person-level SF location and dated personal social activity. Assign contact/location outreach first, otherwise social → email, otherwise email. Incomplete checks remain provisional.
6. Use paid enrichment or Actor execution only for the specific reviewed cohort/budget. Never reveal extra personal data or start remote jobs just to test a connection.
7. Review before preparing the Ops handoff, preserve immutable decisions and record one next action per held person. Retain factual interaction history and suppressions. Account presence and a route score do not authorize outreach.
8. Deliver the CRM handoff for Ops, readable private review, run/source receipts, per-channel outcomes, exact counts/costs and validation results. Do not convert discovery coverage into verified contact success.

For requested copy, use the [brief](../runtime/copy/brief.md) and [three formats](../runtime/copy/three-flow-copy.md): email, DM and public reply to a post. Preserve exact URL/handle/address case while keeping prose short, lowercase and personalized. Do not send, schedule, publish or create external drafts unless that action is explicitly requested.

## Local folder contract

Code belongs in `runtime/crm/`, reusable research/operation instructions in `docs/`, schemas in `runtime/sql/`, tests in `runtime/tests/`, aggregate status in `runtime/reports/`, and reusable copy in `runtime/copy/`. Personalized data belongs in the ignored database, `runtime/evidence/notes/`, `runtime/review/`, `runtime/logs/` and private exports. Follow the complete [folder map](../runtime/README.md#folder-map).

## Ownership

Discovery prepares source-backed handoffs; Ops applies and verifies canonical CRM updates. Use the selected agent's scope rather than assuming a daily batch or creating a schedule. [Copy preparation](../runtime/copy/WORKFLOW.md) is separate from delivery.
