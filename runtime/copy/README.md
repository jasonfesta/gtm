# Copy workflow

Read [copy.md](copy.md), the [brief](brief.md), [three formats](three-flow-copy.md) and [outreach routing criteria](../../docs/OUTREACH_ROUTES.md). Use only the latest role/contact/relevance disposition and attributable professional evidence.

The three review formats are **email, DM and public reply to a post**. These are writing formats; they are separate from the email, social → email and contact/location outreach routes. A/B versions are editorial alternatives, not sequential touches or a measured experiment.

## Copy setup

Jason’s examples and feedback go into the single private [ingest.md](drafts/voice-session/ingest.md). At the end of collection, the assistant writes the consolidated final guide back into [copy.md](copy.md). Message preparation reads that file directly. Explicit developer-peer rules apply now; the current guide is finalized from explicit direction; no example-derived calibration is claimed.

## Local copy tool

The MCP client writes the prose; the local stdio server supplies evidence and stores immutable revisions. It does not send or schedule. Start it from the workspace root:

```sh
python3 -B runtime/crm/mcp_server.py
```

Adapt the path in [mcp.example.json](../config/mcp.example.json) to the receiving machine. Registration is client-specific and is not performed by copying that file. A cloud client/model processes whatever context is supplied to it.

Use `copy_context` for the exact agent/person; it reloads the saved guide on each call and returns `copy_guidelines_sha256` for audit. Review its latest `outreach_review`, then save with `copy_save` using accepted evidence IDs and an editor label. New revisions use the expected current revision; stale edits are rejected. `copy_export` writes private Markdown into the ignored drafts folder.

The tool currently stores email, DM and replies to actual incoming DMs. **Public post replies are prepared as private Markdown** until their distinct storage format is implemented. Never record a public reply as a private message. Incoming-DM support is a separate response capability and does not change the three review formats.

## Editing and handoff

Keep approved voice rules in copy.md and channel guidance consistent. The template paths contain guidance, not fill-in scripts. The copy context API includes copy.md and the private approved-reference file; supplied examples and approval state are retained locally. Read the exact post before drafting a public reply; read the incoming message before answering a DM. Preserve URL/handle/address case while keeping prose lowercase. Do not invent a product experience, relationship, working example, destination or result.

The local Markdown files are the working copy authority. External copies must be reconciled explicitly before they can replace them. Personalized revisions and incoming messages stay local; editor labels are audit labels, not authentication. For another machine, follow the [System guide](../../docs/CRM.md) and [maintenance](../../docs/MAINTENANCE.md) rather than syncing a live database.

## Daily generation and final deployment

The [daily copy workflow](WORKFLOW.md) defines fresh morning research, planning, original LLM drafting, tonality/punctuation/character-length review, and drafting before messaging. The current copy guide is finalized; future examples may refine it. Live deployment remains subject to readiness. Synthesize with the [final guide skeleton](templates/final_voice_guide.md); it structures guidance, not messages. The copy context API supplies the daily workflow alongside this saved guide.

## Link formation authority

Read the [canonical UTM formation rules](../UTM/FORMATION_RULES.md), supplied as `utm_formation_rules` by copy_context, before preparing a tracked destination. Preserve registered campaign identifiers, especially `gtm-dev-3935204`, and existing aggregate `utm_id` semantics. Proposed evergreen names need registry acceptance before use. Keep daily batch IDs, recipient identities and sequence steps in private records, not UTM values. Save the exact verified link with the draft and reuse it on retries. Missing destination, registry acceptance or supported delivery remains a hold.
