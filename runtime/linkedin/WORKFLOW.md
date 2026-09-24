# LinkedIn workflow

## Interactive request

An operator request supplies a platform workflow and count, such as `linkedin casual 10`. The run detects the logged-in account; the request does not name or assume a hardcoded account.

Newly observed accounts default to preview. They need a reviewed local policy with voice, caps, cooldowns, skip rules, allowed action types, an inbox baseline, and a stop-file path before live submission can be enabled.

## Run sequence

1. Acquire the configured browser-profile lock.
2. Confirm LinkedIn login and resolve the visible member identity.
3. Load that account's local policy and stop switch.
4. Start a `runs` record.
5. Scan the requested surface without sending.
6. Resolve people and profiles; hold ambiguous identity matches for review.
7. Filter prior actual contacts, exact targets, and only prospectively recorded fresh-program suppressions. Never import legacy campaign blocklists or lead queues.
8. Capture the current post or inbound message needed for drafting.
9. Save draft revisions and lint the selected draft.
10. Reserve the deterministic idempotency key.
11. Re-check the logged-in account, target, eligibility, and composer context.
12. Submit only when the account policy and request authorize it.
13. Save the exact outbound message and platform reference, or a failure event.
14. Reconcile the run against visible account activity and close the run.

DM monitoring considers only a one-to-one inbound message first observed after the account's monitoring baseline. LinkedIn's visible conversation-row date is used when individual message timestamps are absent. InMail, sponsored threads, ambiguous participants, later owner replies, and sensitive or judgment-dependent messages are skipped or surfaced for review. The operator never starts a DM.

Public runs use Recent, with positive UI verification. They allow at most 10 comments per run and 20 confirmed public comments per America/New_York calendar day for the current account. Apply [contact rules](../Rules.md): at most three unanswered touches per person across public replies, DMs, X, LinkedIn, and Gmail, with 72-hour spacing. Engagement cancels the drip; one relevant response per actual new incoming reply may continue beyond the cold cap. The local preflight covers LinkedIn history; cross-platform reconciliation is still required before live work. Promoted, repost, comment-activity, unresolved, and exact-post duplicates are excluded. Hiring-related content is neither reserved nor excluded merely because it is about hiring.

## Reporting

Every run reports:

- detected account
- requested, eligible, drafted, attempted, sent, failed, and skipped counts
- account-day total and applicable spacing, three-touch, or engagement stops
- ambiguous identities or missing Message access
- whether unused candidates remain for failure-only refill

## Automation boundary

Scheduling will call the same `linkedin_operator.py` entry point with an account policy and explicit workflow. It must never inherit another account's voice, approvals, history, or daily count. Login loss, account mismatch, access barriers, an active stop file, another account run lock, or missing policy end the run without external action. The intended Jason cadence is 9:00 AM and 4:00 PM America/New_York; it is documented but not yet enabled.

## Daily fetched-batch workflow

Follow the selected agent runbook for scope and the saved copy workflow for preparation. Preserve contact limits, actual history and verified outcomes.

The existing feed/inbound operator is a channel capability, not a second daily lead source. Connecting fetched people to exact supported posts/profiles is pending integration; feed candidates outside the batch do not silently join the daily plan. Incoming conversation replies remain separately driven by actual incoming messages.
