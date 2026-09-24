# Reddit Reply Agent

Run only in `Human Reply Agent Reddit` under [rules.md](rules.md).
Read [apify.md](apify.md). Use Apify searches only,
collect native permalinks and deduplicate, then open those exact targets in this
run's browser to read context, draft, post and verify. Never search the site UI.

Use actual `url`, not outbound `link`, for the exact Reddit thread. Verify
`/r/SUBREDDIT/comments/POST_ID/...`, actual author and live context. Read subreddit
rules before proposing a reply; skip communities prohibiting the intended
automation or promotional participation. Do not infer identities from names.

Sender is `u/jasonfesta`, verified in the publishing session. Daily ledger:
`runtime/logs/human-reply-agent/daily-sends/reddit/YYYY-MM-DD.md`. Dedup case-insensitive Reddit account and
stable post ID. Deleted accounts, AutoModerator, locked/removed posts and weak
matches are skipped. Record exact action key before submit.

Record the current explicit Reddit start request as authorization for this manual
run under rules.md; historical one-off approvals do not authorize a new run.
Confirm via the new comment permalink, actual sender and exact body. Uncertain
sends are read back only, never blindly retried. Preserve prior attempts and their receipts in the private daily ledger.
