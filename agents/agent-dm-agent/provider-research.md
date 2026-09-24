# Agent DM Protocol Decision

## Decision

The Agent DM job mirrors the Human DM one-pass flow, but the counterpart is a remote agent. Develop A2A as the first interoperable **agent-to-agent** adapter, while retaining other approved agent-operated protocols as route-specific alternatives. Discover a verified Agent Card and declared interface, satisfy its authentication, send a contextual message, then follow the returned Message or Task and its conversation context. A2A is a task/conversation protocol rather than a social DM inbox; a public Agent Card does not itself invite cold outreach. Require evidence that the intended interaction is permitted.

AgentMail (`agentmail.to`) is the **existing implemented email fallback**, not the definition of the Agent DM agent. It exposes a hosted MCP endpoint and inbox/thread/message primitives. The recipient may use any email host, but its contact route must independently be verified as agent-operated. The manual sender uses AgentMail's documented REST send/reply endpoints to set `Idempotency-Key` and verify the exact message through readback. The MCP connection can assist inbox inspection and manual reconciliation after authentication. Keep Masumi Agent Messenger as another possible route for agents that advertise a verified Masumi slug; it needs a separate send/readback adapter. MCP may expose a route adapter as a tool, but MCP alone is not a peer-agent messaging protocol.

Do not use a human Slack, Discord, X, or email identity as an agent route. Do not use generic bridges that impersonate the operator or extract a user session. A GitHub reaction identifies the reacting account but GitHub has no native private-message channel, so GitHub-origin candidates must resolve to a separate verified agent-operated route.

## A2A adapter and manual rehearsal

1. Resolve the agent's card from a verified source and validate the card's declared identity, supported interface, skills, and security requirements. Do not turn an unverified URL into an approved route.
2. Acquire only the credentials required by that agent's declared security scheme; keep them outside the repository. Confirm the route's interaction policy and owner-approval rule separately from the card's existence.
3. The standalone `crm.agent_dm_a2a` implements a manual A2A 0.3.0 JSON-RPC text Task action with exact reviewed payload and a persistent local attempt record; other versions/transports and direct Message results are explicitly unsupported. Preserve message, task, and context identifiers; treat transport uncertainty as uncertain rather than blindly resending.
4. Read back the direct Message or Task state, handle input-required/auth-required and terminal states, and continue an existing task/context for follow-ups. Normalize exact inbound and confirmed outbound facts for separate Ops CRM and PostHog receipts.
5. Test with a controlled A2A endpoint first, then rehearse one permitted real interaction manually. The adapter has offline tests; a real conversation is not yet proven. See the [System guide](../../docs/ARCHITECTURE.md) for commands and evidence limits.

## WebMCP controlled messaging

The [native test page](protocol-test/README.md) registers `send_agent_message` and `get_agent_conversation` through the browser's native model context. These tools must reach the controlled Portkey agent and preserve actual message/task/context IDs before a tool invocation can count as an agent conversation. No polyfill, direct callback invocation, backing REST call, or email fallback counts as native WebMCP proof. Registration alone does not establish message execution or readback.

The temporary loopback counterpart is only for explicitly permitted synthetic test content. It must remain non-organic in every evidence file and handoff. Ops uses fixture/rollback-only CRM verification, not production synthetic outreach. A real controlled PostHog event may be submitted and queried separately after a real conversation. Production CRM verification still needs a real eligible recipient/outcome.

## Existing AgentMail connection and manual rehearsal

1. Select the existing Darwin-operated AgentMail sender inbox or create one under the intended organization.
2. Add the hosted MCP server to Codex using the official Streamable HTTP MCP setup, with endpoint `https://mcp.agentmail.to/mcp`.
3. Authenticate with the narrowest practical inbox/message/draft permissions. Keep secrets outside repository files.
4. Verify inbox discovery, draft creation, recipient binding, attachment support, send receipt readback, delivery/bounce/complaint events, and duplicate behavior in a test inbox.
5. Stage a single candidate in the durable manual outbox. The REST adapter writes the claim before sending and uses a stable `Idempotency-Key`. An uncertain result stops for readback and is never sent again automatically. AgentMail documents a 24-hour lifetime for send idempotency keys, so a later uncertain attempt must not be blindly retried.
6. Run one manual send/readback rehearsal with a verified agent recipient and sender inbox, then process the separate CRM and PostHog requests through `ops agents`.

## Markdown decision

Publish one canonical Darwin `.md` resource at a stable HTTPS URL and record its version plus SHA-256 digest. Use native `text/markdown` only when the destination advertises it. Otherwise send a short plain-text explanation plus the HTTPS URL. Use a `.md` attachment only for an agent-email route that advertises attachment support and only when the reviewed draft calls for it.

## Sources

- [AgentMail MCP integration](https://docs.agentmail.to/integrations/mcp)
- [AgentMail idempotent sends](https://docs.agentmail.to/idempotency)
- [AgentMail events](https://www.agentmail.to/docs/events)
- [AgentMail webhook verification](https://www.agentmail.to/docs/webhook-verification)
- [AgentMail attachments](https://docs.agentmail.to/attachments)
- [Masumi Agent Messenger](https://github.com/masumi-network/masumi-agent-messenger)
- [A2A agent discovery](https://a2a-protocol.org/latest/topics/agent-discovery/)
- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [A2A and MCP comparison](https://a2a-protocol.org/dev/topics/a2a-and-mcp/)
- [GitHub reactions API](https://docs.github.com/en/rest/reactions/reactions)
- [Codex MCP setup](https://learn.chatgpt.com/docs/extend/mcp)

- [WebMCP draft specification](https://webmachinelearning.github.io/webmcp/)
