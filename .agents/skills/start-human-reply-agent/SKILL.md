---
name: start-human-reply-agent
description: Start or resume a separate Human Reply Agent task targeting X, Reddit, Hacker News, or LinkedIn. Select one platform, name the task for it, and run its discovery and verified-reply workflow. Reddit, LinkedIn and Hacker News search 10 keywords through Apify, pace selected browser replies across about an hour, then report. Do not use for DMs or Human Discovery.
---

# Start human reply agent

Choose exactly one platform from the user's request: X (Twitter), Reddit,
Hacker News (HN or Hackernut), or LinkedIn. On resume, retain the platform already selected
in this task. For a new unqualified start, ask which platform before operating.
Do not start all four or switch a running task to another platform implicitly.

Rename the current task with `set_thread_title` to exactly `Human Reply Agent X`,
`Human Reply Agent Reddit`, `Human Reply Agent Hacker News`, or
`Human Reply Agent LinkedIn`. Each platform runs separately in its own task.
Run in the current task unless the user explicitly requests a new task; only
then create one with the selected platform in its startup prompt. Do not create
sibling tasks or schedules as a side effect of startup.

## Workspace and browser readiness

Run these tasks in the saved `gtm` project on this Mac. Resolve all paths below
against `/Users/jasonfesta/Documents/ChatGPT/gtm`, including when the task's
working directory differs. The shared daily ledgers and run-specific browser leases must use
this canonical root; do not create isolated ledger copies in a worktree.
For a user-requested new task, include this absolute skill path, the canonical
root and chosen platform in its prompt, and select the saved GTM project.
Recommend Local mode when the user creates these operational tasks directly.

Before discovery, verify that this skill, RULES.md, the selected platform
reference, `runtime/config/timeline-keywords.txt`, and
`runtime/crm/human_reply_capture.py` are readable. Verify that the canonical
`runtime/logs/human-reply-agent/daily-sends/` and `ops-handoffs/` locations are
writable; create a missing platform/day record only as needed, preserving
existing history. Run lease commands with `runtime/` as the working directory.

Confirm browser controls are actually callable in this task, initialize them
using their documented entry point, and verify the selected platform's signed-in
account from live UI before sending. Browser tooling and login sessions are
runtime capabilities, not files attached by renaming a task. If required files,
write access, browser controls or login are unavailable, report the specific
blocker before publishing. At each fresh start create a new task-owned visible
Codex browser tab, positioned on the right, rather than reusing an existing tab.
Follow RULES.md to assign a fresh run UUID and a unique lease scope AND owner
`human-reply-CHANNEL-RUN_UUID`; pass `--scope` on both acquire and release.
Keep all operations on this run's explicit tab handles. Separate platform runs
can operate concurrently without the old global `social-browser` lock.

Read `agents/human-reply-agent/rules.md` completely once at startup. It is the sole
shared operating contract. For a non-X platform, also read only its reference:
`agents/human-reply-agent/reddit.md`, `agents/human-reply-agent/hacker-news.md`,
or `agents/human-reply-agent/linkedin.md`.
For all three non-X platforms, also read `agents/human-reply-agent/apify.md`:
Apify search → collect permalinks → open exact targets → draft/reply → verify.
Never search these sites directly or stop at a links-only handoff. X keeps X Pro discovery and ends after its finite batch.

For X, capture up to 100 posts across loaded X Pro columns, select up to 25
worthwhile replies, finish that finite queue, then report and end. No minimum
send count, imposed hour-long pacing or automatic refresh restart. For Reddit,
LinkedIn and Hacker News, follow HUMAN_REPLY_APIFY.md: search 10 keywords,
aim for at least 25 candidate permalinks, pace worthwhile replies over roughly
an hour, finish the selected queue for as long as needed, then report and end.
There is no minimum send count or forced runtime. Honor an explicit one-pass
request without silently removing the requested pacing. Stop sending immediately on hold or stop. A stop applies to
this task unless the user explicitly requests stopping other platform tasks.
Stale heartbeat/channel registry state does not start or stop these manual runs.
Never start a scheduler, side task, or another channel as part of startup.

Change the skill or rules only when the user explicitly requests a workflow
change. Preserve browser ownership, authorization, same-day deduplication,
immutable intents, and exact send verification. Do not load historical
five-tab, Apify, Portkey, or timeline-watcher runbooks.
