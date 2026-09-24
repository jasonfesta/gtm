# Manual Reddit discovery handoff

`runtime/crm/reddit_discovery.py` consumes saved Apify observations. It makes no
network calls, starts no runs, schedules nothing, and sends no messages.
Human Reply owns outreach and its send ledger. Ops alone writes runtime/PostHog.

Use the parent provider contract's `reddit_search_input` for the
`trudax/reddit-scraper-lite` actor: individual terms in `searches`,
`searchPosts=true`, `skipComments=true`, `sort=new`, `time=day`, and bounded
`maxItems`/`maxPostCount`. Read back an existing run before considering another
chargeable launch. Preserve input, run manifest and raw dataset outside Git.
Failed or timed-out runs are partial acquisition even when their dataset has
usable records; zero records in such runs are not successful zero-result searches.

```sh
cd runtime
python3 -B -m crm.reddit_discovery \
  --input /private/tmp/reddit-run/reddit.raw.json \
  --run-id EXISTING_RUN_ID --provider-status TIMED-OUT \
  --output-dir /private/tmp/reddit-run/qualified
```

Use the actual provider status, never an assumed success. Pass each prior
`reddit.qualified.json` using repeated `--previous PATH` to suppress previously
handed-off account/thread pairs across manual retries. Use a fresh output
directory: the command refuses to overwrite evidence. All evidence must stay
private; commit only implementation, synthetic tests and documentation.

The observed actor schema includes `username`, `url`, `title`, `body`, and
`dataType=post`. `url` is the Reddit thread; `link` can be an outbound target.
The normalizer accepts only HTTPS Reddit thread paths and observed valid account
names. It rejects deleted accounts and AutoModerator. It preserves account case
for display and uses lowercased accounts plus post IDs for stable action keys,
independent of slug, tracking parameters and provider run IDs. A person key
separately deduplicates people across threads.

Qualification retains the canonical tool/work relevance filter and additionally
requires personal work evidence. It is a conservative text filter, not a claim
that the person or their assertions have been independently verified. Human Reply
reads the exact thread context before choosing outreach. No inferred emails or
invented identities are included. Distinct observed people are not net-new CRM
people until Ops returns its receipt.

Outputs are `reddit.qualified.json` (normalized `items` usable as the existing
collector's Reddit input) and `reddit.human-reply.json`. Both preserve run status
and partial status. Route qualified evidence and the provider manifest to the
parent for its Ops batch; route the reply file to Human Reply. Never treat a
handoff as a delivered reply.

Human Reply's separate Reddit ledger must durably record the action key, actual
sender account, exact body, and attempt time before sending. Confirm delivery
only with the observed comment permalink/account. For an uncertain send, read
back the same thread for the actual sender and exact body. Empty or incomplete
readback does not establish failure: retain the uncertain state and do not
blindly resend. The existing X contract remains unchanged.

Validation: `cd runtime && python3 -B -m unittest tests.test_reddit_discovery`.
