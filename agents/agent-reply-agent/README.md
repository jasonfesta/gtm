# Agent Reply Agent

## Purpose

Manually reply once in a relevant public GitHub, Moltbook, or Discord conversation with a useful Darwin Search query, a Markdown resource, or a task-specific Search/Act instruction. This agent does not send private messages or write directly to CRM or PostHog.

## Provider readiness

Verify access for every run: GitHub needs the intended connected identity; Moltbook needs its existing private credential and provider-confirmed claimed status; Discord needs the permitted channel and configured sender identity. Portkey is optional for the connected-provider path. Do not infer current readiness from previous receipts.

## One manual run

1. Get a bounded Agent snapshot from Ops Agents and scan current public conversations. GitHub routes come from Agent website/GitHub URLs. Moltbook currently reads recent public posts; Discord reads only channels explicitly listed in the config.
2. Pick one conversation where Darwin directly helps, and send one useful public reply. Use exactly one response form: `query`, `markdown_resource`, or `usage_instruction`. Record the exact text and resource version, if any.
3. Capture the provider message ID and permalink. If the connected account sent the reply, use `record --once` with that receipt. If a sender credential is configured, `run --once` can send the prepared reply directly.
4. Send the generated `ops-agents/<run-id>.crm.json` path to the `ops agents` task and obtain its CRM receipt. Send the separate `ops-agents/<run-id>.posthog.json` path to that same task and obtain its PostHog receipt.
5. Report the public permalink and both receipts, then stop. Reuse the private run directory so the same conversation is not posted twice.

There is no timer, cron job, heartbeat, daemon, or background loop. The Codex task sends both handoff paths to Ops Agents itself; the operator does not transfer files. A dry run or failed send may be followed by a real send. For an uncertain send, read the provider conversation before resolving it from a real provider receipt.

## Commands

Scan without sending:

```sh
python3 -m gtm_agent_runtime.cli scan agents/agent-reply-agent/README.md --once \
  --snapshot PRIVATE_AGENT_SNAPSHOT.json \
  --config agents/config/agent-reply-scan.example.json \
  --output PRIVATE_CANDIDATES.json
```

After posting through a connected provider, record its actual `message_id` and `permalink`:

```sh
python3 -m gtm_agent_runtime.cli record agents/agent-reply-agent/README.md --once \
  --input PRIVATE_PREPARED_REPLY.json \
  --receipt PRIVATE_PROVIDER_RECEIPT.json \
  --output-dir PRIVATE_RUN_DIRECTORY
```

The prepared reply shape is in [agent-reply-agent.example.json](../config/agent-reply-agent.example.json). The connected-provider route records an externally verified receipt. With a working Portkey key and provider API token, `engage --once` can scan, compose, send, and write both handoffs in one manual command; see [the scan config](../config/agent-reply-scan.example.json) and [Darwin assets](../assets/agent-reply/v1.json). Portkey and a provider API token are not required for the connected-provider route.

The Moltbook sender reads `MOLTBOOK_API_KEY` or, if unset, an ignored `.local-credentials/moltbook.json` in the active checkout or `~/.config/moltbook/credentials.json`. A `credentials_file` in the prepared provider payload can point to a different private file. Never commit any of these credentials.

## Provider confirmation

Moltbook API sends first require a claimed status. A creation response with a pending verification challenge is uncertain, retains the message ID, and prevents resending. Successful Moltbook and Discord creation responses are followed by one bounded provider readback matching the exact text and message ID; Discord also matches the channel and reply parent. Failed or mismatched readback remains uncertain. Resolve it manually from real provider evidence before recording a confirmed external receipt. No challenge is solved automatically.
