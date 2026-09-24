# Contact rules — X, LinkedIn, and Gmail

These internal contact ceilings apply alongside the selected agent runbook and [route readiness](../docs/OUTREACH_ROUTES.md). Preserve actual contact history; a limit is not permission to send.

## Runtime policy

The fenced `crm-contact-policy` block below is the authoritative runtime configuration. Code reads it on each eligibility check; missing, malformed, or unsupported settings block the check. Change this block and the explanatory prose together. Account settings may restrict daily volume and available actions but cannot increase this shared budget. Schedules and sending authority remain separate.

```crm-contact-policy
{
  "version": 1,
  "channels": ["x", "linkedin", "email"],
  "social_formats": ["public_reply", "dm"],
  "max_unanswered_touches": 3,
  "max_unanswered_per_channel": 1,
  "minimum_gap_hours": 72,
  "stop_drip_on_engagement": true,
  "allow_conversation_replies": true,
  "one_response_per_incoming": true,
  "automatic_restart": false
}
```

## Three-touch drip

Each person gets **at most three unanswered outreach touches total**, shared across X, LinkedIn, Gmail, public replies, and DMs. Use each available channel once in email → X → LinkedIn order. Switching formats, accounts, or campaigns does not create another three-touch allowance.

| Surface | Unanswered drip | After engagement |
|---|---|---|
| X public post replies | One cold touch on this channel within the 3 shared touches; each needs a relevant actual post; never duplicate a cold reply on the same post | Stop drip; respond to their actual reply in context |
| X DMs | One cold touch on this channel within the 3 shared touches where DM access and sending are supported and authorized | Stop drip; answer the actual incoming message |
| LinkedIn public post replies/comments | One cold touch on this channel within the 3 shared touches; each needs a relevant actual post; never duplicate a cold comment on the same post | Stop drip; respond to their actual reply in context |
| LinkedIn DMs | One cold touch on this channel within the 3 shared touches where supported and authorized; current browser implementation only answers inbound DMs | Stop drip; answer the actual incoming message |
| Gmail | One email within the 3 shared touches | Stop drip; continue the actual conversation |

Minimum follow-up timing: **day 1, day 4, day 7 at the earliest**, at least **72 hours after the last actual outbound touch**. Use email → X → LinkedIn order among available verified channels when the run invokes a cold sequence. A public reply and DM on the same social platform are alternatives, not extra touches. Missing or unavailable channels stay held rather than being replaced by another touch on a used channel. If delivery is delayed, shift subsequent touches; never compress them into one day. Engagement cancels remaining steps; the third unanswered touch exhausts the sequence, with no automatic restart.

Before a manually invoked cold-sequence action, reconcile replies across every contact channel and review existing follow-ups within the requested scope. Advance only when at least 72 full hours have elapsed since the latest actual outbound touch and no reply or attributable engagement has arrived on any channel. Use the next available, unused channel in email → X → LinkedIn order; do not resend on the previous channel. Missing or stale inbox coverage means hold, not “no reply.” Refresh the context and draft before the next touch. Daily fetches and draft revisions never reset the clock or the three-touch limit. If a step misses its due time, send only at the next eligible reviewed session, then start a fresh 72-hour wait from that actual send.

Example: email on Monday at 10 a.m. → X no earlier than Thursday at 10 a.m. → LinkedIn no earlier than Sunday at 10 a.m., only while unanswered. Skip unavailable channels without reusing a channel. Delays, capacity and account availability can move these times later.

A/B means choose one version. Do not contact multiple coworkers to bypass a person's limit; keep one active cold lead per company until a documented review permits a different contact.

## Engagement ends the drip, not the conversation

Any attributable engagement with our outreach pauses/cancels all remaining drip touches across channels. A public reply or DM/email response moves the person into conversation. A like, reaction, or follow attributable to the outreach also stops the drip for review; it is not permission to invent a private conversation. Impressions, opens, and anonymous clicks are not human engagement evidence. Record the actual engagement, source, person, and time locally; if its meaning is uncertain, hold.

Conversation replies may exceed three total messages and need not wait 72 hours: they answer what the person actually said. Allow one relevant response per new incoming message/reply, preserving its ID and text. Additional incoming messages permit additional responses. Do not send multiple unanswered messages under a “conversation” label. An opt-out or decline still stops outreach. If the conversation goes quiet, hold; do not resume the drip automatically. An explicitly requested later follow-up must be recorded and reviewed separately.

## Stop and response rules

- Opt-out, do-not-contact, or decline: stop cold outreach across channels immediately. Record the actual message and suppression locally. Never switch channels to evade it.
- Human reply: cancel the remaining drip and review the incoming message. Continue only as a relevant conversation; actual incoming text is required for a private response. A requested follow-up date takes precedence over the cold cadence.
- Bounce: hold outreach pending contact/deliverability review. Do not guess another address or automatically switch channels.
- Auto-reply or out-of-office: hold for review, not a human conversion. No automatic retry or reset; respect a stated return date.
- Timeout, missing receipt, pending submission, or unknown outcome: reconcile before retrying. Treat an unresolved attempt as consuming capacity until evidence resolves it.
- Exhausted cap: retain the stop indefinitely. Reopening requires an explicit reviewed decision or a recipient-initiated conversation; elapsed time alone does not reopen it.

## What counts and what must be checked

A touch is one actual outbound message, public reply, or attempted submission whose outcome remains uncertain. Drafts, previews, copy edits, and choosing A/B are not touches. Sent/delivered receipts for the same message share one exact provider reference and count once; missing references are counted conservatively. Keep actual IDs, URLs, addresses, timestamps, channel, person, account context, and evidence locally. Do not count a draft as sent or a provider acknowledgement as a human reply.

Before each action: verify sender account, identity and exact recipient, current route/readiness, suppressions, company activity, all relevant channel histories, open draft/reservation status, touch counts, spacing, copy, and explicit action authority. Missing history is a hold, not zero prior contact. An empty CRM ledger does not establish an empty Gmail, X, or LinkedIn history. Manual activity and legacy factual sends must be reconciled too.

Daily send budgets must be explicitly selected by channel/account before launch and include manual sends and pending attempts. A draft preparation target is not a send budget. The selected agent determines its preparation workload; preparation never authorizes outreach.

## Code enforcement and remaining boundaries

[Shared cadence code](crm/outreach_rules.py) evaluates X/LinkedIn/Gmail limits and both public/DM touches together, shared spacing, stop outcomes, duplicate receipts, and shared-contact identities against local history. [Gmail preparation](crm/email_workflow.py) calls it during preparation and immediately before reserving external draft creation, in addition to its existing stricter history, suppression, readiness, and duplicate-reservation checks. The existing Gmail history hold still prevents automatic follow-ups even when 72 hours have passed. The shared helper also distinguishes drip eligibility from a response tied to a real incoming reply; a conversation response bypasses cold caps/spacing but not stop or uncertainty holds.

[LinkedIn preflight](linkedin/store.py) applies the same three-touch and 72-hour rules to its local public/DM history before reservations and stops cold actions after incoming engagement. It permits a deduplicated reply to an actual incoming message after the cold cap. The old lifetime-author rule no longer controls eligibility. Exact-post deduplication and account limits remain.

The CRM and LinkedIn ledgers are not yet automatically reconciled across platforms. Engagement reactions need explicit local review/capture; there is no complete reaction collector. The CRM schema represents message replies; other engagement must be retained with evidence and a hold/suppression rather than fabricated as a message reply. X dispatch, cold social DM initiation, public conversation-reply dispatch, and Gmail sending remain incomplete. This helper does not establish complete provider history, enforce live daily send budgets, or authorize a send. Before any future live run, reconcile all histories, check suppressions and these limits, reserve capacity atomically across channels/accounts, recheck before submission, and store exact receipts. Until shared history/reservations are integrated, retain manual cross-platform review and do not activate a drip sender. It must not use a passing cadence result as a send permission.

Validate with the synthetic CRM tests and [maintenance checks](../docs/MAINTENANCE.md). Keep runtime policy changes in the fenced block above and verify behavior with tests; preserve factual history.

## Cold outreach order — current override

Use **email → X → LinkedIn** for the daily cold sequence, in that order among available, verified and ready channels. Hydration records exact channel identities, ownership evidence and availability; relevance scores and historical route labels do not reorder this sequence. Unavailable channels are recorded and skipped without inventing contact details. Temporary readiness, stale incoming coverage or uncertain submission remains a hold, not evidence of exhaustion.

Use each channel at most once, with at least **72 hours between confirmed actual outbound sends**. A reply, engagement or opt-out stops the cold sequence. Once available channels are exhausted, exclude the person from new cold batches; keep their CRM record and all history. Do not restart the sequence or reuse a channel to reach three touches. Actual conversations are reviewed separately. Every send still needs individual approval.
