# Manual LinkedIn source handoff

Jason's LinkedIn discovery path is Sales Navigator people search, verified
public profile identity, then an actual public post permalink for Human Reply.
`crm.linkedin_sales_handoff` is an offline evidence validator and durable local
outbox. It does not browse, send outreach, call runtime/PostHog, or schedule work.
Human Reply retains outreach ownership and its X contract. Ops alone applies
CRM and PostHog changes through the existing separate handoffs.

Open Sales Navigator in a Codex browser panel on the right. Search a focused
canonical tool phrase and optionally select Posted on LinkedIn. Search results
and profile claims are candidate evidence, not automatic qualification. Open
the person's recent activity, read the original post, and verify its author,
AI work context, and Comment control. Use the profile menu's View LinkedIn
profile link to map the Sales Navigator identity to the public `/in/` URL.
Preserve the display name exactly; do not expand initials or infer a legal name.

Capture the public post link from the LinkedIn profile activity and open it
to verify the same author and text. The embedded Sales Navigator
`/feed/sales-navigator/urn:li:share:...` ID can differ from the public activity
ID. Never derive an activity URL from that share ID. Profile URLs, feed URLs,
and unverified post URLs are not reply targets. Apify evidence cannot substitute
for Sales Navigator search provenance. A login wall, missing subscription,
challenge, inaccessible post, or absent reply control is a precise blocker;
retain the candidate without claiming a ready handoff.

Write a private JSON input with `candidates` containing these fields:

- `source`: `sales_navigator_manual`; `name`: exact display name;
  `observed_at`: timezone-aware ISO time.
- `search_query`, `search_url`: executed query and Sales Navigator people-search
  URL; `sales_profile_url`: observed Sales Navigator person URL;
  `profile_url`: observed public `/in/` URL.
- `ai_work_evidence`: exact excerpt from the post describing real AI work;
  `relevance`: operator explanation of Darwin relevance.
- `post`: `url` (observed `/feed/update/urn:li:activity:ID/` or
  `/posts/...-activity-ID-.../`), `author_profile_url`, `text`,
  `verified: true`, `comment_available: true`. Optional fields such as
  `sales_embed_url` and `published_relative` remain intact.
- `evidence`: `search`, `identity`, and `post`, each with absolute local
  `path` and `sha256` of the capture. Operator transcription must be labeled
  as such. The helper checks file integrity, not whether a human's claim is true.

Run once from `CRM`:

```sh
python3 -B -m crm.linkedin_sales_handoff --input /private/path/input.json \
  --output-dir /private/path/linkedin-outbox
```

Use the same shared private outbox across worktrees/runs. JSON receipts are
atomically replaced under a file lock. An identical input returns the existing
receipt. A changed input suppresses post IDs already in the outbox; two posts
for the same public profile or Sales Navigator identity in one batch retain
only the first. A later distinct post remains eligible for Human Reply's own
send deduplication. Held records don't reserve identities. Never delete outbox
receipts to retry delivery: resend the same handoff ID/path and have the recipient
acknowledge it. `prepared_not_sent` is not an outreach or Ops receipt.

Send only the ready handoff path to Human Reply. This schema is a separate
LinkedIn intake artifact, not an input to `prepare-x`. Discovery's parent can
use the preserved evidence for its existing Ops handoffs. Keep live captures
and receipts in ignored/private folders; commit only helper, tests, and docs.

Verification: `python3 -B -m unittest tests.test_linkedin_sales_handoff`.
