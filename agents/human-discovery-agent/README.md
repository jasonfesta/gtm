# Human Discovery Agent

## Purpose and operating mode

Find people discussing or building with AI tools on X, Reddit, LinkedIn,
Hacker News, Product Hunt, and Apollo. Prepare two separate handoffs for
`ops agents`: `human_upsert` records for CRM and one aggregate discovery
event for PostHog. This agent does not send outreach or write to CRM or
PostHog directly.

The operating target is **200 distinct people per New York calendar day**.
If run six times a day, the planning target is **34 per four-hour batch**.
These are goals, not numbers to pad or gates that block a short handoff.
Runs are manually invoked. No timer, cron job, daemon, or automation is active.

## Keywords and sources

The 23 tools visible in Darwin's sidebar produce 25 searchable phrases because
`GitHub Copilot / VS Code` and `LangChain / LangGraph` are split. The canonical
combined query is:

```text
("ChatGPT" OR "Claude" OR "Grok" OR "Perplexity" OR "Muse Code" OR "Cursor" OR "GitHub Copilot" OR "VS Code" OR "Gemini CLI" OR "Google Antigravity" OR "Grok Build" OR "Devin" OR "Replit Agent" OR "Windsurf" OR "OpenCode" OR "Hermes Agent" OR "OpenClaw" OR "OpenAI Agents SDK" OR "Anthropic SDK" OR "OpenRouter Agent SDK" OR "Vercel AI SDK" OR "Google ADK" OR "LangChain" OR "LangGraph" OR "Pydantic AI")
```

`runtime/crm/human_discovery.py` is the canonical term list; print the generated
query with `cd runtime && python3 -B -m crm.human_discovery --print-query`.
The manual collector keeps the combined query for X only. Reddit receives an array
of individual terms using `trudax/reddit-scraper-lite`; Hacker News receives one
keyword per bounded Actor run, including comments. Product Hunt is filtered locally.
Use repeated `--term` flags to select terms from the same 25-term inventory; the
default bounded pass uses the first five. Use repeated `--source` flags to select
sources. Provider errors and unfinished runs are recorded distinctly from successful
zero-result searches; unfinished run IDs can be read back manually without re-launch.

LinkedIn uses **Sales Navigator search → observed
public post permalink → Human Reply**. A profile link alone is not a replyable post.
Generic LinkedIn Apify acquisition is not this flow and is disabled in this collector.
Hacker News similarly supplies exact item permalinks to Human Reply. Reddit supplies
real thread permalinks, never outbound article URLs. Human Reply owns contextual
posting, duplicate prevention and exact readback in separate source-specific paths;
the existing X contract is unchanged.

Apollo name-and-company lookup validates identity and employer using configured access. Provider verification is not an independent deliverability test or permission to contact.

## Manual run and handoff

```sh
cd runtime
python3 -B -m crm.human_discovery --collect \
  --output-dir ../outputs/human-discovery
```

This command explicitly starts provider searches and may incur Apify charges.
It does not schedule another run. The collector normalizes names, profile
URLs, source links, and IDs; keeps observations with tool and work context;
deduplicates by stable profile identity; labels the audience; and writes:

- `<run-id>.crm.json`: `human_upsert` operations with source evidence and
  Darwin relevance.
- `<run-id>.posthog.json`: one `gtm.human_discovery_run` event with source
  and audience totals and a stable replay UUID.
- `<run-id>.collection.json`: query, provider run IDs or errors, batch yield,
  and distinct people observed across files in that output directory on the
  same New York calendar day.

Send the absolute CRM and PostHog file paths to the `ops agents` task.
Ops processes CRM first, submits PostHog separately, and returns two distinct
receipts. The collection receipt is not an Ops receipt. The daily count means
distinct people observed locally, **not** confirmed net-new CRM records.
Short or partially failed batches are reported honestly.

For saved observations, use `--input PATH` instead of `--collect`. Saved
search collection files can be passed with repeated `--search-collection PATH`
options and an optional `--reddit-input PATH`; these modes make no provider
calls. A held live batch can be rebuilt from its recorded evidence with
`--requalify-handoff PATH --collection-receipt PATH` without another search.
See `--help` for the complete manual command options.

## Boundaries

- Do not reply, DM, email, enroll, follow, like, or publish.
- Do not write directly to CRM or PostHog; route both handoffs through
  `ops agents`.
- Do not invent people, source URLs, provider results, or progress toward the
  target.
- Do not add approval queues, review gates, timers, cron jobs, or automation
  as part of this manual agent.
