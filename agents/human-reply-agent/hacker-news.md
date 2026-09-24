# Hacker News Reply Agent

Run only in `Human Reply Agent Hacker News` under [rules.md](rules.md).
“Hackernut” refers to Hacker News in this workflow.
Read [apify.md](apify.md). Use Apify searches only,
collect native item permalinks, deduplicate, then open the exact targets in this
run's browser to read context, draft, post and verify, just like Reddit and
LinkedIn. This replaces the previous links-only workflow at Jason's request.
Never search HN directly or use external story URLs as reply targets.

For comment candidates, relevance must come from the comment's own words,
not just the story title. Verify the exact numeric item ID and parent context.
Sender is `jason-festa`, verified from live UI before submission. Persist the
observed target permalink and exact reply text before one submit. Confirm the
new comment ID, observed permalink, sender and exact text; never retry an
uncertain send or bypass a platform access restriction.

Use `runtime/logs/human-reply-agent/daily-sends/hacker-news/YYYY-MM-DD.md` for
same-day author/target exclusions and outcomes. Preserve prior records,
including prior uncertain outcomes. Discovery is not a sent event; queue
confirmed-only, body-free PostHog and separate CRM handoffs under rules.md.
