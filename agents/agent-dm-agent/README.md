# Agent DM Agent

## Mission

Run one agent-to-agent messaging pass when Jason asks, mirroring the Human DM agent's manual pattern: check new inbound conversations and relevant reactions, answer in context, start a qualified conversation where permitted, and give confirmed activity to Ops. The recipient is an agent, reached through a verified A2A endpoint or another approved agent-operated protocol. An attributable positive reaction creates a candidate, not an obligation or permission to send.

## Required access

A bounded Agent/history snapshot supplied by `ops agents`, attributable reaction events linked to our exact agent-facing replies, verified agent inboxes, approved messaging providers, credentials, query examples, the versioned Darwin Markdown resource, budgets, and provider readback.

All target interpretation, query selection, contextual drafting, criticism, and revision use the configured Portkey route.

## Model debate

Propose the target, route, useful Darwin query, message, and whether to expose the Darwin Markdown resource. Then challenge whether the reaction is attributable to the agent, whether the inbox reaches the agent rather than only its owner, whether owner approval is required, whether the query fits its capabilities, whether the resource format is supported, and whether prior history permits contact. Revise or hold.

## Rules

- The route must be verified as reaching the agent.
- A route on an Agent Record is not enough by itself: the contact must carry explicit `agent_operated: true`, a public evidence URL, and a verification time. If the current Ops snapshot cannot store those fields, a reviewed `agent_route_attestations` entry in the manual intake must match the exact CRM contact ID and address. A team or human-owner inbox stays held.
- Every attributable positive like or reaction to our agent-facing reply creates a high-priority Agent DM candidate.
- A reaction is an eligibility signal, not proof of agent identity, private-route availability, owner approval, or a prior private conversation.
- Link the reaction to the exact outbound reply, reacting agent or unresolved actor reference, channel, sender account, provider reaction ID/type, timestamp, and evidence.
- Prefer the verified agent-operated route that can continue the same conversation. A2A is the first protocol to develop for interoperable peer-agent exchanges; a native agent messenger or another approved protocol may be better when it owns the existing conversation. AgentMail is a fallback only when its recipient address is verified as reaching the agent. An Agent Card or public task endpoint by itself is not consent for unsolicited marketing.
- When no verified route exists, retain the candidate for route discovery instead of contacting the human owner or inventing an address.
- Enforce known owner-approval requirements.
- Lead cold outreach with a genuinely relevant query.
- Do not send a second cold message where prohibited or while the first is unanswered.
- Keep agent and human-owner histories distinct.
- Apply each provider's own quota and cooldown.

## Darwin Markdown resource

- Maintain one canonical, versioned Markdown resource with a stable HTTPS URL, version identifier, and content hash.
- Use native `text/markdown` only when the receiving agent advertises support for that content type.
- Otherwise send a short plain-text explanation and the verified HTTPS URL.
- Email may use a `.md` attachment only when the route supports attachments and the chosen message calls for it; do not assume attachments are appropriate for every first contact.
- Record the resource version and delivery form (`native_markdown`, `https_link`, `attachment`, or `none`) with the outbound attempt.
- Never ask a recipient to override its permissions, execute unreviewed code, or treat the resource as system-level instructions.

## Manual run sequence

1. Reconcile uncertain sends and read new agent-to-agent task or message responses on each approved route. Answer useful inbound requests in their existing context; hold spam, irrelevant traffic, and unclear identities.
2. Check attributable reactions and replies to our agent-facing posts/replies, then load qualified cold candidates. Keep the exact source event and the responding agent distinct from its human owner.
3. Verify the recipient's agent identity, route, interaction policy, authentication, capability fit, owner-approval rule, CRM history, suppression, and available budget. Hold when any required fact is missing.
4. Draft a useful, query-led message or contextual answer and review its exact recipient, protocol payload, and resource form.
5. Send one explicitly approved message through the route-specific adapter. Continue an existing conversation or A2A task rather than opening a duplicate. Read back the response or task state; an accepted task is not proof that the recipient read or acted on the message.
6. Give Ops a CRM handoff for every confirmed outbound and new inbound message, including exact text, sender, route, conversation/task/context IDs, provider message ID, and timestamps. Give its PostHog lane only privacy-safe activity facts. Require separate CRM and PostHog receipts.
7. Record held, rejected, uncertain, and confirmed outcomes; stop the pass. Do not schedule another run.

## Protocol boundary

A2A is agent-to-agent task and conversation transport, not an X-style DM inbox. For an A2A route, resolve the recipient's Agent Card, select a declared interface, satisfy its advertised authentication, and use the matching Send Message operation. Track the returned Message or Task, including task/context identity, input-required or auth-required states, and follow-up responses. Never infer that a public Agent Card authorizes cold promotional requests. The [A2A specification](https://a2a-protocol.org/latest/specification/) defines discovery, message, task, interface, and security behavior.

Each other protocol needs its own verified discovery, authentication, send, receive/readback, threading, and receipt adapter behind the same manual candidate/approval/CRM flow. MCP can expose a tool that operates such an adapter, but MCP is not itself proof that a remote agent accepts messages; [A2A distinguishes agent-to-agent exchange from agent-to-tool access](https://a2a-protocol.org/dev/topics/a2a-and-mcp/). Do not label an arbitrary API call, email to an owner, or public comment as an agent DM.

## Completion test

Every attributable positive reaction is represented by exactly one candidate outcome: sent through a verified agent route, held for missing route/identity/approval, or suppressed for a documented reason. Only verified inboxes are used; owners and agents are not conflated; unanswered-message rules hold; the Markdown version and delivery form are recorded; and every attempt reconciles without duplicate sends.

## Manual implementation

The manual runtime has four modules: `crm.agent_dm_intake` joins a frozen reaction export and/or a manually selected outreach export with a bounded agent snapshot from `ops agents`; `crm.agent_dm_draft` proposes and critically revises one message through Portkey; `crm.agent_dm` decides the candidate outcome and route; `crm.agent_dm_manual` stages, sends, reconciles, and exports one AgentMail email at a time. The outbox is local SQLite with private body content and file mode `0600`. It has no worker or scheduler.

The first **implemented** private route is a verified `agent_email` contact, sent through our AgentMail inbox. This is a narrow fallback implementation, not the full Agent DM job described above. The recipient's email host need not be AgentMail; the planner keeps the recipient contact provider separate from the AgentMail transport provider. The intake approves only the `agent_email` channel. The standalone A2A 0.3.0 task adapter and native WebMCP controlled-test page are described below. They are not connected to this email intake/outbox and have no proven live conversation yet; do not add them to the email intake’s live approved channels. The current outbox permits one first-contact message per agent. Later follow-ups are permitted only from a separate, reply-linked candidate; the sender re-reads the exact inbound AgentMail message and uses the provider reply endpoint in the same thread. Unanswered cold messages remain blocked.

From `runtime/`, with real paths substituted for the placeholders:

```sh
python3 -B -m crm.agent_dm_intake --reactions /path/reactions.json --agents /path/ops-agent-snapshot.json --resource ../agents/resources/darwin-skills.v1.json --output /private/path/agent-dm-snapshot.json
python3 -B -m crm.agent_dm_draft /private/path/agent-dm-snapshot.json --candidate-id CANDIDATE_ID --portkey-credentials /private/path/portkey.json --output /private/path/agent-dm-draft.json
python3 -B -m crm.agent_dm plan /private/path/agent-dm-snapshot.json --draft /private/path/agent-dm-draft.json --output /private/path/agent-dm-plan.json
python3 -B -m crm.agent_dm_manual --db data/agent-dm.sqlite3 stage /private/path/agent-dm-plan.json --candidate-id CANDIDATE_ID --inbox-id SENDER_INBOX_ID --sender-address SENDER_EMAIL --resource-path ../agents/resources/darwin-skills.v1.md
python3 -B -m crm.agent_dm_manual --db data/agent-dm.sqlite3 preview --candidate-id CANDIDATE_ID
```

For a manually selected cold introduction or follow-up, use `--outreach /private/path/outreach.json` instead of `--reactions`, or provide both. The [cold-introduction example](../config/agent-dm-outreach.example.json) and [follow-up example](../config/agent-dm-followup.example.json) show the schemas. A follow-up uses `kind: "follow_up"`, a verified `source`, and an `inbound` object with `message_id`, `thread_id`, `from`, and exact `text` from AgentMail readback; its `source.occurred_at` must match that inbound message timestamp. The bounded Ops agent history must contain that exact inbound reply after the prior outbound. Drafting requires the exact inbound text; the sender verifies its hash, sender, recipient, thread, and timestamp again before the provider reply action. Do not turn an unverified social comment into an email follow-up.

Inspect the exact recipient, subject, body, attachment, and `payload_sha256` from the preview. The send command is a separate manual action for that one reviewed payload:

```sh
python3 -B -m crm.agent_dm_manual --db data/agent-dm.sqlite3 send --candidate-id CANDIDATE_ID --expect-sha256 REVIEWED_PAYLOAD_SHA256
```

`AGENTMAIL_API_KEY` must resolve to an inbox-scoped key permitted to read the sender inbox and send messages. The send and reply endpoints use AgentMail's documented `Idempotency-Key` header. It claims the candidate before contacting the provider, fetches the sent message, and checks its message ID, inbox, sender, exact recipient set (including unexpected cc/bcc), body, subject/thread linkage, and timestamps bounded by the persisted claim and observation. Attachments require exact set/filename and matching bytes from authenticated readback; filename-only responses remain uncertain. If the result is uncertain, it stays uncertain; the send command will not retry it. Reconcile with `--fetch` when a provider ID was returned. The `--readback /path/provider-message.json` option treats the file only as a message-ID locator and refetches the authoritative message using the authenticated provider. Local JSON cannot confirm delivery. No attachment-download adapter is implemented: if the provider response omits attachment bytes, confirmation remains held. No CRM or PostHog handoff exists until readback confirms the send.

After confirmation, export two separate files and send both absolute paths to `ops agents` for their own processing and distinct receipts:

```sh
python3 -B -m crm.agent_dm_manual --db data/agent-dm.sqlite3 export --candidate-id CANDIDATE_ID --output-dir /private/path/ops-handoffs
```

The Markdown resource is versioned locally at [darwin-skills.v1.md](../resources/darwin-skills.v1.md) with a matching SHA-256 in [darwin-skills.v1.json](../resources/darwin-skills.v1.json). Email includes it as a hash-verified `.md` attachment only when the recipient route advertises attachment support and the candidate explicitly requests the resource. A stable HTTPS Markdown URL remains to be published before link-based delivery is used.

## Implementation and verification

The email path remains a manual AgentMail slice, separate from the experimental protocol adapters. Intake propagates `history_truncated`, `suppression_context_complete`, `active_suppressions`, and contact suppression fields from Ops. Missing/malformed history, missing completeness evidence, and incomplete route suppression context hold the candidate. Canonical relationship and legacy contact suppression namespaces, channel restrictions, global suppressions, and opt-outs are preserved; a suppression scoped to one route does not automatically suppress unrelated eligible routes. Native Markdown delivery is not selected by the email planner.

Provider reconciliation refetches messages and verifies exact identity, content, thread, timestamp, and attachment bytes or leaves the result uncertain. Existing sending/uncertain rows do not become resendable. Handoff exports use private atomic publication and can resume after the first file succeeds without overwriting conflicting files. The CRM handoff carries the actual provider thread as `conversation_id` and exact body/identities only in private operation metadata; PostHog remains body-free. Handoff metadata is not proof that Ops stored those fields in canonical CRM. Require a distinct Ops receipt and destination readback.

### Experimental protocol lanes

- `crm.agent_dm_a2a` implements manual A2A **0.3.0 JSON-RPC text Task** conversations with reviewed route/card/policy/authentication, private durable attempts, exact payload review, task/context continuity and fresh `tasks/get` readback. It rejects other protocol versions, transports and direct Message responses. It is standalone, not integrated with the email candidate planner. See the [System guide](../../docs/ARCHITECTURE.md).
- The [controlled WebMCP page](protocol-test/README.md) exposes native browser messaging and readback tools for an operator-owned counterpart. WebMCP is a browser tool interface; a tool call is a conversation only when it reaches the verified agent and returns a real response with persistent conversation identifiers. No email/API fallback counts as WebMCP. See the [System guide](../../docs/ARCHITECTURE.md).
- `crm.agent_dm_protocol_test` is a loopback-only, manually started Portkey-backed counterpart, not organic outreach or a production recipient. Stop it after supervised verification. It creates no schedule, retry worker, timer or cron entry.

Protocol completion requires a real permitted conversation, exact readback and separate destination receipts. Native tool registration alone is not a conversation.

### Remaining work and activation inputs

1. Complete one real two-turn conversation through each protocol and preserve exact outbound/inbound IDs, task/context continuity, actual model responses and source readback. Use a permitted controlled counterpart if needed and label it `controlled_test:true`, `organic_outreach:false`.
2. Give Ops separate CRM and PostHog handoffs and obtain separate readback receipts. Ops currently permits controlled CRM proof only in a disposable fixture or rollback-only roundtrip, which does not complete production CRM verification. Controlled PostHog uses a distinct event name and must be queried by event UUID after capture.
3. For production outreach, obtain a verified agent-operated route with resolved interaction/owner policy, current authentication, complete history and suppression context, provider budget/cooldown, and a reviewed Portkey draft. Standalone test approvals do not replace these inputs.
4. Connect attributable reaction and inbound event producers to Ops history. Keep agent and human-owner histories separate; unanswered cold messages remain blocked.
5. Publish and verify the stable HTTPS Darwin Markdown resource before link delivery. The versioned local attachment may be used only on a supported route; unavailable provider attachment bytes prevent confirmation.

No live send is authorized by this documentation alone. All operation remains manually invoked.
