---
name: start-human-dm-agent
description: Start or operate the manual Human DM Agent when the user says "start human dm agent" or designates the current Codex task as the Human DM Agent.
---

# Start Human DM Agent

Read `agents/human-dm-agent/README.md` completely and follow it exactly from
the current Codex task.

- Use the current task as the visible Human DM Agent console.
- Create a fresh dedicated X browser tab at the start of every pass. Never claim,
  reuse, or navigate an existing X tab owned by the Human Reply Agent or another
  task. Both agents may keep separate X tabs open concurrently.
- Verify the authenticated X account is `@jasonfesta` before and after every
  bounded browser pass. Stop on an identity mismatch.
- Read technical replies in X Notifications with **All** selected.
- Treat collection as a hard phase gate. First collect every relevant visible
  inbound provider ID and its exact DOM evidence since the previous completed
  pass, across the three permitted inbound sources. While collecting, do not
  open underlying posts, profiles, or recipient DM conversations and do not
  check inboxes, deduplicate, or draft. A complete visible notification marked
  as a reply to `@jasonfesta` is sufficient evidence; open its post only if the
  notification is truncated or ambiguous.
- After collection ends, deduplicate the entire collected batch against that
  day's Markdown. Only after every collected ID is checked may inbox checks and
  drafting begin. Never interleave these phases.
- Check exactly three inbound sources: replies to our posts or replies, message
  requests, and waiting inbound replies in existing DM conversations.
- Send direct messages only. Never post a public reply.
- Confirm the exact recipient's DM inbox is available before drafting or asking
  for send approval. Treat a closed or uncertain inbox as a hold.
- Use durable local state and exact event IDs to deduplicate observations,
  messages, receipts, unavailable inboxes, and uncertain submissions.
- Preserve suppressions, opt-outs, identity/target holds, and existing
  action-authority settings. Starting the agent does not create broader
  outbound authority.
- Require exact X readback for every sent message. Never automatically retry an
  uncertain submission.
- Keep every run manual and bounded.
- Bound a run by the previous completed pass, never by a candidate count. In
  Notifications, collect newest first until reaching the exact reply ID saved as
  the previous completed notification checkpoint. If that pass found no reply,
  stop when notification timestamps are older than its saved scan time. With no
  checkpoint, collect every relevant reply visible for that day. Save the new
  checkpoint. There is no five-person or other numerical cap. Never restart the
  scan or start another pass automatically.
- End every pass with the exact mandatory completion report in
  `agents/human-dm-agent/README.md`, including zero-result and held passes.
