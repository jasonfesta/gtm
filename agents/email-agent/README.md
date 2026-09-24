# Email Agent

## Purpose

The Email Agent runs human email through one persistent Smartlead campaign. Other agents supply
real leads and final subject/body copy at injection time. The Email Agent does not draft or
rewrite copy. After Smartlead reports an actual outcome, the Email Agent prepares recipient-level
CRM records and an aggregate PostHog send log for Ops Agents to apply.
Agent email is a separate queue for AgentMail when enabled; it never enters the human Smartlead
campaign. The Email Agent does not use Portkey or write directly to CRM or PostHog.

## What is built

1. **Campaign configuration:** `agents/config/email-agent.live.json` identifies the reviewed Smartlead campaign; `agents/config/email-agent.example.json` is a template. Read current campaign state, queued recipients, sender connections and limits before use. The agent remains parked until explicitly invoked. Configured limits do not establish actual delivery capacity.
2. **Lead injection:** `runtime/crm/email_agent.py` accepts a `human_email` JSON batch. Each lead
   must have CRM `person_id` and `contact_id`, an email address, final `email_subject` and
   `email_body`, and `copy_version`. It deduplicates email addresses within the batch and adds
   the copy and CRM IDs as Smartlead custom fields. `runtime/crm/smartlead.py` uses Smartlead's API
   and splits imports into batches of at most 400. Enrollment reads campaign leads before and
   after import. Only matching canonical IDs and final copy count as enrolled; aggregate added
   counts do not prove recipient enrollment. A partial import or transport failure leaves absent
   recipients uncertain; reruns skip matching existing recipients and import only missing ones.
   Conflicting identity/copy holds the batch for reconciliation.
3. **Outcome readback:** A manual reconciliation reads campaign leads, the Smartlead sent
   mailbox, and campaign analytics. A sent-mailbox item supplies the actual sender inbox,
   provider message ID, and send/reply time. Merely preparing or enrolling a lead does not
   count as sending it. Provider receipts determine delivery truth; cumulative analytics
   snapshots are not summed as new daily events.
4. **Handoff files:** Each run writes `crm-handoff.json`, `send-to-posthog-log.json`, and
   `run-receipt.json`. The CRM file contains one `event_record` per confirmed recipient outcome.
   The PostHog file contains one aggregate event without recipient copy or addresses. Ops Agents
   accepts the two files separately and returns separate CRM and PostHog receipts. The Email
   Agent currently generates the files; it does not dispatch them to Ops Agents itself.

## Manual use

Use a real CRM-backed snapshot with final copy. Enrollment rows also require
`email_verified: true`, `email_verification_evidence`, `identity_status: "verified"`,
`suppression_context_complete: true`, `suppression_checked_at` (timezone-aware check time),
and an explicit `active_suppressions` list. Ops supplies current person/company/contact
suppression context; copy the applicable route suppressions into the row. Suppressed recipients
and unknown context cannot enroll. Refresh the snapshot through Ops immediately before the
manual run; this agent never queries CRM itself. `suppression_max_age_seconds` bounds snapshot
age to 900 seconds (15 minutes) by default and in both configs. Stale or future-dated evidence
requires a fresh Ops check. Every input row is validated before duplicate addresses are removed. The file in `fixtures/` contains synthetic
examples and must not be enrolled as a real batch. Supply `SMARTLEAD_API_KEY` in the environment,
in `.local-credentials/smartlead.json` under the checkout root, or set
`SMARTLEAD_SHARED_ENV_FILE=/private/path/to/approved.env`. Never copy the API
key into this repository.

```sh
cd runtime
python3 -B -m crm.email_agent \
  --config ../agents/config/email-agent.live.json \
  --snapshot /absolute/path/to/real-email-snapshot.json \
  --output-dir ../outputs/email-agent/manual-run-001 \
  --enroll
```

An explicit `--enroll --activate` starts the campaign only after every supplied recipient is
verified enrolled. Enrollment alone never activates. Activation is campaign-wide: use the
existing reviewed campaign/sender setup and review its queued recipients before this command.
Smartlead documents `PATCH /campaigns/{id}/status` with `ACTIVE` in its
[API reference index](https://api.smartlead.ai/llms.txt).

After Smartlead actually sends, run the same command with `--reconcile` instead of `--enroll`.
Give Ops Agents the absolute paths to that run's `crm-handoff.json` and
`send-to-posthog-log.json`; keep its CRM and PostHog receipts separate. The campaign is already
provisioned, so normal batches do not use `--provision`.

## Completion

Reconcile actual provider outcomes, then obtain separate CRM readback and queried PostHog receipts through Ops. Aggregate metrics are snapshots, not additive daily send counts. Missing sender, message ID, timestamp or delivery evidence remains unresolved. There is no background worker or recurring run.

## Audience search briefs

The [CRM + Apollo keyword plan](sourcing/README.md) defines solo/open-source devs,
company assistant builders, and early-AI-adopter knowledge workers using Jason’s latest definitions.
The search briefs are complete; Apollo execution and email eligibility remain separate work.
