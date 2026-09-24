# Human Reply Agent — separate platform loops

This is the shared operating contract for separate X, Reddit, Hacker News,
and LinkedIn tasks. Select one platform per task using the startup skill.
Each invocation sources a finite batch, processes its selected reply queue,
then reports and ends. Stop promptly on hold or stop. X captures up to 100
posts across the loaded X Pro columns and selects up to 25 worthwhile replies;
these are upper limits, not minimum targets. Finish that queue as long as needed.
Reddit, LinkedIn and Hacker News follow apify.md for their sourcing,
hour-long pacing and completion. No platform restarts discovery indefinitely.
Do not promise execution through app interruptions,
unavailable tools, or usage limits; report a real blocker instead of claiming
work is running. Never create or reactivate a timer, heartbeat, cron, daemon,
or side task. No cross-platform batches or Portkey. Apify discovery is required for Reddit,
LinkedIn and Hacker News; X retains its existing X Pro discovery.

Read this file once at startup, not between batches. Do not rewrite rules,
edit code, run tests, or load historical runbooks during execution. Only an
explicit user workflow-change request authorizes modifying this contract.
Canonical root: `/Users/jasonfesta/Documents/ChatGPT/gtm`.

## Platform selection and isolation

Task titles are exactly `Human Reply Agent X`, `Human Reply Agent Reddit`,
`Human Reply Agent Hacker News`, and `Human Reply Agent LinkedIn`. Each task
operates only its selected platform, owns its tabs and in-memory review cache,
and uses `runtime/logs/human-reply-agent/daily-sends/CHANNEL/YYYY-MM-DD.md` where
CHANNEL is `x`, `reddit`, `hacker-news`, or `linkedin`. Preserve existing records.
Use platform and run identifiers in report/handoff filenames to avoid collisions.
The old five-tab startup and hourly runtime registry are not active contracts.

The ten steps below describe X. For other platforms apply the same ownership,
matching, deduplication, intent, verification, reporting and cleanup rules with
these substitutions and the selected platform reference:

- For Reddit, LinkedIn and Hacker News, replace X Pro discovery in steps 2–3
  with Apify searches only. Read [apify.md](apify.md).
  The agent orchestrates the Actor and processes its dataset; it never searches
  the platform UI, a native search endpoint or a web search engine for candidates.
  Collect exact native permalinks, stable target IDs, authors, keywords and
  context from Apify results, then deduplicate against the daily ledger.
- Open shortlisted permalinks directly in this run's dedicated browser worker.
  Verify the target, author and full live conversation before drafting, save
  the exact parent permalink and reply text as immutable intent, submit once,
  then verify and record the new reply's own ID and permalink. Apply this same
  complete reply workflow to all three platforms, including Hacker News;
  do not stop at a list of suggested links. Skip invalid or mismatched targets.
  Empty results or Apify failure never authorize direct-search fallback.
  Follow the bounded sourcing plan and final-report completion in
  apify.md; X also ends after its finite queue.
- Substitute the selected platform's ledger, identity and stable parent ID in
  steps 4–10. Read relevant community rules before selection. Only actual new
  comment IDs and observed permalinks count as verified publication.
- An explicit request to start a named platform's reply agent authorizes routine
  contextual replies for that active manual run on Reddit, LinkedIn or Hacker News. Record
  that request in the run record. Configuring this capability alone starts no
  run and authorizes no sends. Historical one-off approvals and X authorization
  do not authorize another platform's run.
- At each fresh start, create a new task-owned visible Codex browser tab using
  `cua.createBrowserTab("iab", platformUrl, { visible: true })`; this is the
  task's dedicated browser surface. Never attach to another task's existing tab
  by URL. Open it in the right panel. Record the returned tab ID and a fresh run
  UUID; retain explicit handles for all tabs created by this run. Reuse these
  owned tabs within the loop. This does not imply separate cookies or an OS
  browser process; verify the signed-in account in the new surface.
- Set both lease scope and owner to `human-reply-CHANNEL-RUN_UUID`. Pass that
  exact `--scope` on acquire and release. Each fresh browser run has a distinct
  lease, so different platform tasks do not block each other's browser work.
  Never use a unique scope to control someone else's tab. On resume, retain the
  recorded scope for retained tabs; a fresh browser run receives a fresh UUID.
  Keep token/fence checks and release only your own lease between batches and
  on hold/stop. A busy lease for the same run must not be stolen.

## Active loop

1. **Own the browser and measure.** Confirm browser controls are available;
   acquire the run-specific lease from `CRM` with
   `python3 -m crm.human_reply_capture lease acquire --scope human-reply-CHANNEL-RUN_UUID --owner human-reply-CHANNEL-RUN_UUID`.
   Keep its exact token/fence, skip a busy lease without stealing it, and release
   only your own lease in `finally`. Use this module only for acquire/release,
   never planning, drafting, or recording. Measure wall time with timestamps;
   this is not a scheduled timer.
2. **Refresh X Pro.** In this run’s newly created browser tab, load the AI deck at
   `https://pro.x.com/i/decks/1990937645631799520`, visible on the right where
   supported. Never navigate a DM tab. Verify the AI deck and authenticated
   `@jasonfesta` account from live UI. Wait briefly for the columns to mount;
   the first loaded column is not evidence that all columns have loaded.
3. **Capture only posts.** Read mounted `article[data-testid="tweet"]` nodes
   across the visible columns in one compact DOM pass. Retain per-column
   in-memory blobs containing only column, status ID, handle, timestamp via
   `time.getAttribute('datetime')`, exact parent permalink from the anchor
   around that time, `tweetText`, and fingerprint. Combine in memory and
   deduplicate status IDs. **No post-age cutoff or minimum reply quota. Capture at most 100 posts and select at most 25 replies per run.**
   Do not dump the full DOM, scroll, prove a time boundary, or reconstruct a
   capture file. If capture raced page loading, one focused recapture after
   readiness is allowed.
4. **Match, then deduplicate.** Immediately match
   `runtime/config/timeline-keywords.txt` locally, case-insensitively, before
   reading today's America/New_York
   `runtime/logs/human-reply-agent/daily-sends/x/YYYY-MM-DD.md`. Exclude self,
   every recorded handle, and every attempted parent, including uncertain or
   incomplete intents. Keep at most one worthwhile post per remaining author.
   Keep reviewed IDs/fingerprints in memory to avoid re-evaluating unchanged
   weak matches; this cache never replaces the daily ledger.
5. **Draft here.** Read the captured context and write specific, non-promotional
   replies in this task. Discard weak, incomplete, or irrelevant matches. Seven
   was one batch's result, not a target or stopping rule. Do not fill a quota
   with generic replies or use another drafting service.
6. **Persist intent before sending.** Save immutable parent ID, handle, exact
   observed permalink, relevant context, and exact reply text in the daily
   record before submission. Jason's standing authorization covers routine
   contextual X replies in this workflow: do not add a per-batch approval
   pause. It does not override platform controls or approval requirements for
   sensitive/high-impact actions. Report any such conflict, never bypass it.
7. **Use one reply worker.** Open one fresh dedicated worker tab at the first
   selected permalink, then reuse it sequentially for the other parents. Keep
   X Pro intact. Re-derive controls from fresh state after navigation; pass
   current tab handles explicitly to reusable helpers, not stale closures.
   Use one bounded readiness wait, then skip a pre-submit timeout. Do not
   restart the deck or whole batch because one candidate cannot be composed.
8. **Send once and verify.** Verify exact parent ID/author, sender, and exact
   composer text, then submit once. Confirmation requires exact published
   text plus a new unique `@jasonfesta` reply ID and observed permalink, absent
   before submission. Record missing/ambiguous readback as uncertain; never
   retry an uncertain send. Stop immediately on hold or stop, including in
   the middle of a batch; record remaining intents as unattempted.
9. **Record the batch.** Preserve all pending history. Record confirmed,
   failed, uncertain, and unattempted outcomes with observed timestamps and
   reply URLs. Generate separate local CRM and body-free, confirmed-only
   PostHog handoff files under `runtime/logs/human-reply-agent/ops-handoffs/`.
   Ops tasks currently remain archived: queue files locally, do not unarchive
   them or claim delivery/ingestion. Never call runtime/DB/PostHog directly.
10. **Finish and report.** Complete the selected finite queue without refreshing
    to replenish it. X has no imposed hour-long pacing or minimum runtime.
    Close the owned worker, retain X Pro and release only the owned lease in
    `finally` with `lease release --scope human-reply-CHANNEL-RUN_UUID --owner human-reply-CHANNEL-RUN_UUID --token TOKEN --fence FENCE`.
    Save `runtime/logs/human-reply-agent/reports/x-RUN_UUID.md` with capture counts,
    matches, daily exclusions, selected count, confirmed/uncertain/failed/skipped
    and unattempted outcomes, confirmed reply links, start/finish timestamps,
    elapsed time and local handoff status. Deliver the final report and end.
    Empty batches also report and end. On hold/stop or a blocker, preserve the
    pending queue and report partial results. Never schedule an automatic restart.

## Speed and evidence

Keep extraction/matching compact, reuse the authenticated deck, draft the
batch together, and batch sequential UI operations with fresh state checks.
Do not repeatedly plan, reload instructions, or investigate unrelated systems.
Readiness targets: about 6 seconds for an exact parent and 4 seconds for a
composer; these bound waiting, not the run or post age. Skip unavailable
composers rather than debugging each one during the loop. Never label a
submitted-but-unverified reply a pre-submit failure.

Measure full elapsed wall time separately from instrumented send/readback
operations and any human wait. Report posts per column, total captures,
keyword matches, daily exclusions, unique candidates, confirmed/uncertain/
failed outcomes, and local handoff status; distinguish repeated captures from
unique posts across the run. Do not promise a two-minute run or call operation
time the total.
