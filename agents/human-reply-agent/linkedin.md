# LinkedIn Reply Agent

Run only in `Human Reply Agent LinkedIn` under [rules.md](rules.md).
Read [apify.md](apify.md). Use Apify searches only,
collect native permalinks and deduplicate, then open those exact targets in this
run's browser to read context, draft, post and verify. Never search the site UI.

Accept observed public `linkedin.com/posts/...` or
`linkedin.com/feed/update/urn:li:activity:...` targets. Open the exact source URL
and verify the author, source time and full post before selection. Do not invent
a post from a profile or scrape Sales Navigator as a fallback. Dedup by canonical
profile identity and activity ID, not display name. Unknown author identity holds.

Sender is Jason Festa, `/in/jasonfesta/`, verified in the publishing session.
Read/write daily `runtime/logs/human-reply-agent/daily-sends/linkedin/YYYY-MM-DD.md`. Record the current explicit LinkedIn start request as authorization for this
manual run under rules.md; historical one-off approvals do not authorize a new run.

Use the new comment's actual ID and observed comment permalink for a complete
receipt. If exact body/sender/new comment ID is visible but link extraction fails,
record `published_readback_confirmed_permalink_missing`, exclude the author from
new attempts and withhold confirmed PostHog until reconciled. Never resend.
Preserve incomplete receipts across restart until reconciled.
