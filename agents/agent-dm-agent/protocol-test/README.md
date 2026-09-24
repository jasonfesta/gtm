# Controlled native WebMCP adapter

This page exposes `send_agent_message` and `get_agent_conversation` through the
browser's native `document.modelContext` or `navigator.modelContext` only. It is
a controlled operator-owned Portkey counterpart, not organic outreach or a
production Agent DM adapter. There is no polyfill, timer, retry worker or scheduler.

Serve this exact page from the loopback counterpart owned by the A2A test lane.
The server must enforce Host/Origin, protocol/task/context isolation, unique
message IDs and persistent task/message storage. Its policy and agent-card
resources describe test ownership and lack of authentication. Loopback isolation
is not remote identity authentication. Never deploy this test origin publicly.

## Manual verification

1. Verify `/policy`, agent identity, authorization, no suppressions/opt-outs for
   this synthetic counterpart, quota and permitted model route. Never load real
   CRM snapshots into this test. Production suppression logic remains unchanged.
2. Use the configured Portkey route to draft, critique and revise synthetic query
   text. Review the exact text; no private data, resource delivery or external
   action is needed.
3. Open the loopback page through CUA in a right-side Codex browser panel. Obtain
   `await tab.capabilities.get('webmcp')`, then `fetchTools()`, and inspect
   `tools.description()`. If registration is unavailable, stop: do not call page
   JavaScript or REST directly and label it WebMCP.
4. Invoke `tools.call('send_agent_message', {messageId, text})` once. Record the
   real task/context/message IDs. HTTP acceptance is not a conversation proof.
5. Invoke `get_agent_conversation` with the returned task ID. Verify the outbound
   and generated inbound message, timestamps, protocol and controlled flags.
6. Draft a contextual second turn through Portkey using this exact synthetic
   response. Invoke the native send tool with a new message ID and the same
   task/context IDs. Read back all four messages through the native read tool.
   Reload the page, rediscover the tools, and read again to establish persistence
   beyond page memory. Compare with independently persisted server evidence.
7. Export separate CRM and body-free PostHog handoffs only after confirmation.
   Ops alone applies them and queries distinct receipts. Ops uses a disposable CRM fixture or rollback-only roundtrip for controlled
   integration proof; this does not complete production CRM verification. Never
   insert synthetic production outreach. Controlled PostHog events use a distinct
   event name and must be independently queried by UUID.
8. Stop the manually started server after both lanes finish.

Any attempted message ID stays in localStorage before transport. The page blocks
its reuse, including after reload and on uncertain failure. Any unresolved
attempt also blocks fresh message IDs. Follow-ups require native readback that
confirms both inbound/outbound IDs and the exact task/context. Reconcile against
server storage instead of resending. This ledger is only a duplicate-send guard;
it is not provider-origin receipt evidence. A lost first response without a task
ID requires operator reconciliation against the server's durable message ID.

Run offline contract checks with:

```sh
node --test agents/agent-dm-agent/protocol-test/webmcp-contract.test.cjs
```

Those tests use fake registration/transport and prove only fail-closed adapter
behavior. They do not prove native WebMCP, Portkey replies, persistent provider
readback, runtime/PostHog writes or a completed protocol conversation.
