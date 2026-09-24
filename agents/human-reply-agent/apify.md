# Apify discovery for public reply agents

Applies only to Reddit, LinkedIn and Hacker News. X keeps X Pro discovery.
For each active run, use Apify to search terms from
`runtime/config/timeline-keywords.txt`, selecting 10 distinct terms per network per
run and rotating the selection across runs. The browser
is for opening returned permalinks, reading context, replying and verification;
never use it for searching or feed-based candidate discovery.

## Sourcing volume, pacing and completion

Each Reddit, Hacker News or LinkedIn task runs independently for its selected
network. Search 10 distinct configured keywords on that network per invocation;
if fewer than 10 exist, use all available terms and report the shortage. Save
selected terms and a per-network rotation cursor with the run record. Complete
all 10 searches even if early terms already supply 25 results.

Request up to 25 results per keyword (up to 250 initial rows per network), using
one Actor call per term when the Actor only accepts one query, or an equivalent
multi-term input when supported. Aim for at least 25 distinct, relevant,
unattempted candidate permalinks per network after local matching and daily
deduplication. This is a sourcing target, never a guaranteed result count or a
posting quota. If the first sweep falls short and more results are available,
perform one additional bounded pagination/deeper-result sweep across the same
10 terms, up to 250 additional rows total. Respect account/spending limits;
report any remaining shortfall instead of repeatedly paying for empty searches.
Do not substitute weak results or expand into direct-site searching.

Read full live context, select worthwhile replies and build a persisted queue.
Spread planned submissions evenly across roughly the next hour, beginning when
that queue is ready: for N > 1 selected replies, assign offsets i*3600/(N-1)
seconds for i=0..N-1. A single worthwhile reply does not require an artificial
hour-long wait. There is no minimum number of sends. Never fill unused slots
with generic replies. Use timestamp-based pacing in this active task with short
interruptible waits of at most 60 seconds; release the owned browser lease while
waiting and reacquire before browser work. Recheck hold/stop before every send.
Do not create an automation, cron, heartbeat or background timer.

The hour is a pacing window, not a hard runtime limit. Discovery, live context,
slow navigation and verification can extend the run as long as needed to finish
the selected queue. Do not compress overdue slots into a burst: retain spacing
between remaining submissions. Refresh live context before each scheduled reply
and skip candidates that no longer qualify. A blocker or hold/stop ends posting
promptly and leaves remaining items recorded as unattempted. Never claim the run
will continue across app interruptions or unavailable tools.

After finishing the sourced queue (or exhausting suitable results), release the
lease and deliver a final report, then end this invocation. Do not automatically
restart discovery forever. Persist the report under
`runtime/logs/human-reply-agent/reports/CHANNEL-RUN_UUID.md`: keywords searched,
Actor/run/dataset IDs and available cost, raw and unique results, matches,
duplicate exclusions, selected replies, confirmed/uncertain/failed/skipped and
unattempted counts, confirmed reply permalinks, sourcing shortfall, actual start
and finish times, total elapsed time, posting span and local handoff paths/status.
No minimum runtime or minimum send count overrides relevance or real blockers.
An explicit one-pass request still processes one sourced queue with this pacing
unless the user explicitly changes the pacing for that run.

## Actor routes

- Reddit: `trudax/reddit-scraper-lite`. Existing input builder is
  `crm.human_source_providers.reddit_search_input`; use individual search terms,
  posts only, newest ordering and bounded result counts. Its historical `time=day`
  setting is not the current no-age-cutoff contract: use the Actor's verified
  all-time setting instead. Retain the native Reddit thread URL, not its outbound
  article link.
- Hacker News: `mangudai/hacker-news-scraper`. Verify the live input schema before
  launch; use a search term, date ordering and bounded items. Collect native
  `https://news.ycombinator.com/item?id=ID` targets for stories/comments. External
  article URLs are not reply targets. Preserve the exact comment ID when the
  candidate is a comment.
- LinkedIn: `themineworks/linkedin-post-search` is the existing candidate Actor,
  not yet live-validated. Verify availability, input schema and output before
  claiming readiness. Expected fields to check are `query`, `maxResults`,
  `monitorMode`, and output `post_url`, `author_profile_url`, `post_snippet`.
  Disable monitoring; run a finite search. Do not use Sales Navigator or the
  disabled `human_discovery --source linkedin` collector as a substitute.

## Execution and recovery

Use an available Apify connector, or the existing authenticated helpers in
`runtime/crm/human_source_providers.py` with `crm.local_secrets.apify_token()`.
Never print credentials. Verify the chosen Actor's current schema and pricing
from Apify before its first launch. Use the bounded 10-keyword sourcing plan above,
obey actual account/spending limits, and record Actor,
input, run ID, dataset ID, status and available usage. Do not treat historical
one-off budget reservations as current authorization or available balance.
If billing requires a new purchase or credentials/access are missing, report
that specific blocker; do not silently switch search providers.

Persist each launched run ID before waiting for completion. Read back pending
runs instead of relaunching them. Only a successful run with a readable dataset
can count as an empty result. Match results locally, deduplicate stable targets
and authors, and preserve exact observed native permalinks. Search snippets
are not enough to draft: read the live target in the dedicated browser first.

Then follow rules.md for contextual replies, persisted intents, one submit,
exact sender/text/new-ID/permalink verification and local handoffs on all three
platforms. A fresh start creates its own browser tab and unique lease scope;
Apify discovery does not require controlling any browser tab. No hourly timer,
background monitor or direct-site search fallback is part of this workflow.
