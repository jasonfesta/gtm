# A2A and WebMCP — agent discovery and interaction evidence

[Research index](README.md) · Source task: Find recent A2A WebMCP research (source task: `01a0cefe-8167-7c90-93db-76aecdf92152`).

The task reviewed September 22 noon through September 23 noon, 2026 Eastern. This document consolidates existing observations; endpoints were not retested during organization.

## What was actually demonstrated

A public A2A Hello World endpoint returned HTTP 200 and an agent message, “Hello World,” after a JSON-RPC `message/send` call on September 22. A follow-up asking it to visit Darwin and return discovered agents produced another greeting. Basic external request/reply worked; useful task execution and persistent two-turn conversation were not established. The advertised skill was greeting only. Evidence: Check A2A agent messaging (source task: `01a0cac6-7084-7e83-afca-e2df94b0547d`).

A controlled WebMCP test discovered `send_agent_message` and `get_agent_conversation` on September 23. It did not invoke conversational tools. The related task reported that automatic approval review blocked model-backed server work involving Portkey credential access and outbound prompts. This is a historical task limitation, not a new approval request. Evidence: Complete WebMCP agent conversation (source task: `01a0ce3a-2c94-7bc1-81e3-bcb312237391`).

## Public A2A candidates

Five live Agent Cards were retrieved; no messages were sent to these candidates. Full version, authentication, interface and evidence details: public A2A discovery report (workspace source: `operation-agents/reports/agent-chat-a2a-public-discovery-2026-09-23.md`).

| Candidate | Discovery route | Recorded qualification |
|---|---|---|
| allagents | [Agent Card](https://allagents.app/.well-known/agent-card.json) | Public directory questions invited; task retrieval and durable continuation not established. |
| Nulliverba | [Agent Card](https://nulliverba.ol-lo.workers.dev/.well-known/agent-card.json) | Public research interface; protocol-version adapter gap and no persistent readback proof. |
| Octagon | [Agent Card](https://a2a.octagonai.co/.well-known/agent-card.json) | Requires a bearer key and billed usage; no authenticated interaction tested. |
| California Bitcoin | [Agent Card](https://californiabitcoin.org/.well-known/agent-card.json) | Free synchronous research advertised; persistent tasks not advertised. |
| Fodda | [Agent Card](https://www.fodda.ai/.well-known/agent-card.json) | Async tasks and `tasks/get` documented; requires credentials, with an auth mismatch between card and guide. |

Wiggle remained an excluded lead after an SSL connection failure in the research environment; this did not establish global unavailability.

## WebMCP candidates

Five sites were assessed through native tool discovery; four exposed send tools, but no conversational messages were sent. Full observations: WebMCP discovery report (workspace source: `operation-agents/reports/agent-chat-webmcp-discovery-2026-09-23.md`).

| Candidate | Discovered interface | Recorded qualification |
|---|---|---|
| [Lobby](https://lobby.host/) | `ask_concierge` | AI concierge; no dedicated history retrieval tool discovered. |
| [ARENNA](https://arenna.link/en) | `ask_concierge` | Lobby-powered service concierge; no dedicated history retrieval tool discovered. |
| [Asympta World](https://okok147.github.io/asympta-world/) | `asympta_send_agent_message`, `asympta_list_agent_messages` | Explicit simulation; persistence advertised but not exercised. |
| [Queen / MATCHED?](https://matched-webmcp.pages.dev/) | `message_queen` | Fictional public experiment; no independent message readback established. |
| [Kinro](https://kinro.com/) | `find_insurance_guidance` | Live tool differed from the directory's advertised question tool; exact agent recipient unverified. |

## How to use these findings

Keep discovered endpoints separate from proven conversations. A2A provides agent messaging/task interfaces; WebMCP exposes website tools. Neither a live card nor a tool schema proves autonomous identity, useful execution, or a persistent conversation. Preserve the original reports' dated eligibility and access limitations when selecting candidates.
