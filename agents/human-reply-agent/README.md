# Human Reply Agent

Start with `start human reply agent`, selecting X, Reddit, Hacker News or LinkedIn.
Use one platform per task and follow [startup](startup.md) and the
[channel contract](channels.md). Runs are manually invoked.

| Platform | Operating contract |
| --- | --- |
| X | [Rules](rules.md) |
| Reddit | [Reddit](reddit.md) |
| Hacker News | [Hacker News](hacker-news.md) |
| LinkedIn | [LinkedIn](linkedin.md) |

Read the actual source conversation, prepare a contextual reply, and verify the
published result on its exact thread. Preserve duplicate records and uncertain
outcomes. Produce separate CRM and body-free PostHog handoffs for Ops; the agent
does not call either service directly.

[Apify discovery](apify.md) defines supported non-X collection. Human Discovery
owns broader prospect acquisition; Human DM owns private conversation handling.

Implementation: `runtime/crm/human_reply_agent.py` owns preparation and receipts;
`runtime/crm/human_reply_capture.py` owns capture and account leases. Local ledger
filenames retain prior state for deduplication. Passing tests is not a live reply.
