# Manual Hacker News discovery

Search one canonical tool keyword per bounded Apify `mangudai/hacker-news-scraper`
run. Use `contentType=all` or `comment`, `sortBy=date`, and `maxItems`.
Read back the terminal status and save input/run/raw records outside Git.
Date sorting does not restrict age: retain actual source dates and don't call
an older post a new post merely because it was discovered today.

`crm.hacker_news_discovery.normalize_hacker_news` reads actual `commentText`,
decodes HTML and preserves author, numeric item ID, source date and query.
Comment qualification uses the author's own words, not a parent story title.
Review copied quotations, incidental name matches and unrelated commentary;
retain observed tool/work context without inventing employment or email identity.

The parent collector supports a bounded manual pass:

```sh
cd runtime
python3 -B -m crm.human_discovery --collect --source hacker_news \
  --term Claude --term LangGraph --max-items-per-source 100 \
  --output-dir ../outputs/human-discovery
```

For reviewed normalized input with `run_id` and `sources.hacker_news`, prepare
an exact-permalink Human Reply handoff without provider calls:

```sh
python3 -B -m crm.hacker_news_discovery \
  --input /private/path/qualified.input.json \
  --output /private/path/hn-human-reply.json
```

Replay to the same path returns matching content; conflicting content refuses
to overwrite evidence. Repeated item IDs are deduplicated. Each candidate has
an observed author, numeric item permalink, source text/hash, source timestamp
and stable `hacker_news:item:ID` key. An external article URL is never a reply target.

Human Reply reopens that item, reads context and site rules, checks its operator's
existing replies and owns its durable attempt ledger. A handoff is not a published
reply. Uncertain send requires exact operator/body/thread readback, never blind
retry. Confirm only with the observed reply permalink, operator and exact text.
CRM and PostHog publication evidence go to Ops in separate files. Discovery
CRM and aggregate analytics handoffs remain separate from publication receipts.
