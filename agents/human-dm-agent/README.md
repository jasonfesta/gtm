# Human DM Agent

## Canonical run rule

This file is the operating contract. Every normal Human DM Agent run follows it
as written. A run must not edit this file, agent code, tests, prompts, or other
architecture. Change the agent only when Jason explicitly asks to revise the
agent itself; otherwise, operate X, update only local run evidence/state/receipts,
and report the result.

Do not drift from the five-step flow below. Follow the steps in order and do not
add eligibility checks, extra surfaces, extra approvals, alternate storage, or
new actions during a run. Do not reinterpret or optimize the flow. Only Jason's
explicit instruction to revise this contract may change it.

## Job

Start with the exact prompt `start human dm agent`. Run one X DM pass when Jason
asks. Use the current Codex task as the agent and
open X in a browser panel on the right. At the start of each pass, create a new
dedicated X browser tab for the Human DM Agent. Never claim, reuse, or navigate an
existing X tab owned by the Human Reply Agent or another task. The Human Reply
Agent and Human DM Agent may keep separate X tabs open at the same time. This
agent is **manual only**: no timer, cron, heartbeat, daemon, or automatic
recurring run.

This agent is **DM only**. It never posts a public reply. Before asking for send
approval, it must open the exact recipient's X conversation and confirm that the
DM inbox is available. A closed, unavailable, or ambiguous inbox is a hold and
ends that recipient's send path. Do not publish a public reply as a substitute.

Only these three inbound conditions can create a DM candidate:

1. A person replied to one of our X posts or replies.
2. A person sent an X message request.
3. A person replied in an existing DM conversation and is waiting for our reply.

Every response must be contextual to the exact visible inbound message and its
conversation. No other notification, profile, conversation, or outreach source
belongs in this agent.

## Speed and recovery

A normal scan and draft pass should finish in about 3 minutes:

- Account verification and surface setup: 20 seconds.
- Three-source DOM scan: 90 seconds.
- Local check, inbox check, and contextual drafting: 45 seconds.
- Save evidence and present the approval batch: 25 seconds.

Move on as soon as a section is complete. Do not keep re-reading an unchanged
page. If navigation, DOM access, or identity verification breaks, make one clean
reload immediately. If the same problem remains, save a hold and stop; do not
loop or extend the bounded run.

Each run is bounded by the previous completed pass, not by a candidate count.
Collect every relevant inbound item that arrived since the last completed pass,
newest first. In Notifications, continue until reaching the exact reply ID saved
as the previous completed notification checkpoint. If that pass found no reply,
continue until notification timestamps are older than its saved scan time. On a
first run with no checkpoint, collect every relevant reply visible for that day.
Save the new checkpoint with the run evidence. Do not restart the scan or begin
another pass automatically.

## One-pass routine

1. Verify `@jasonfesta`. Through the rendered X DOM, check only Notifications with
   **All** selected for actual replies to our posts or replies, Message Requests,
   and existing DM conversations whose newest visible message is an inbound reply
   waiting for us. Ignore likes, follows, reposts, bots, nontechnical items, and
   unrelated conversations. For a complete visible notification marked as a reply
   to `@jasonfesta`, save the notification DOM evidence directly and do not open
   the underlying post. Open the post only when the notification is truncated or
   ambiguous; unresolved content is a hold and must never be inferred. Collect
   every relevant inbound item since the previous completed pass, newest first.
   **Complete this collection phase before doing anything from a later step:**
   first save every relevant visible inbound provider ID and its exact DOM
   evidence until the saved checkpoint boundary is reached.
   Do not open profiles, posts, or recipient DM conversations, check inbox
   availability, deduplicate, or draft while IDs are still being collected. Do
   not interleave collection with any later step. There is no five-person or
   other numerical collection cap.
2. Check that day's Human DM Agent Markdown using the exact inbound reply, request,
   or DM provider ID. Skip an inbound item already completed or already pending.
   Preserve opt outs, suppressions, identity holds, unavailable inbox outcomes,
   and uncertain sends. Deduplicate the entire collected batch before opening any
   recipient inbox. Save each new exact inbound item to that same Markdown. Only
   after every collected ID has been checked may step 3 begin.
3. Open the exact DM conversation for each collected person. Continue the existing
   conversation when one exists. Confirm the inbox accepts a DM before drafting.
   A closed or uncertain inbox is saved as a hold. Draft one short contextual
   lowercase DM for each available inbox. Mark a relevant message request for
   acceptance with its draft. Save the draft beside the inbound item in the same
   Markdown. Do not use hyphens, en dashes, or em dashes.
4. Show one list in this Codex chat containing every eligible recipient, exact DM,
   and message request acceptance. Ask once for approval. Do not message or accept
   a request before that confirmation. Never include a public reply.
5. After approval, accept approved message requests and send the approved DMs
   through the rendered X DOM. Require exact readback for every send and save its
   provider ID, conversation reference, timestamp, and outcome in the same
   Markdown entry. Never retry an uncertain submission. Verify `@jasonfesta`
   again, show the mandatory completion report below, and stop. Never start
   another pass on its own.

The daily Markdown is the single availability list, DM draft list,
message-request list, approval list, and receipt. A normal run does
not use another eligibility workflow, secondary step, or alternate state file,
and it does not change the agent itself.

## Mandatory completion report

Every pass ends with this report in the current Codex chat, including passes
with no candidates, no approvals, holds, unavailable inboxes, or uncertain
submissions. Use the same labels and report only observed results:

```text
Human DM Agent pass report

Account: @jasonfesta verified before and after.

Results:
• Notifications reviewed with All selected
• Message requests found: NUMBER
• Eligible people found: NUMBER
• DMs approved: NUMBER
• DMs sent and exactly verified: NUMBER
• Failed or uncertain sends: NUMBER
• Public replies posted: 0

Recipients:
• @HANDLE
  Provider ID: PROVIDER_ID

The pass stopped normally. Receipts are saved in today's Human DM Markdown.
```

If a pass stops on a hold or failure, replace the final sentence with the exact
hold or failure and state that no automatic retry occurred. Never omit the
report, start another pass after it, or report a send without exact readback.

## Existing code and handoff

`runtime/crm/human_dm_agent.py` provides small, explicitly invoked helpers for a
CRM-supplied outbound batch and for packaging first-contact or reply DMs into
an Ops Agents CRM handoff. It does not browse X, schedule work, or call CRM or
PostHog directly. The current Codex task handles the X UI and gives the
resulting separate handoffs to Ops Agents only after confirmed provider outcomes.
A CRM handoff receipt proves CRM recording;
a queued PostHog event does **not** prove live PostHog ingestion.

Run focused local tests from `runtime/` with
`python3 -B -m unittest tests.test_human_dm_agent -v`.
